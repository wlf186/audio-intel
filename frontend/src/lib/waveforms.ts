import {request} from './api'

export type ArtifactWaveform={artifact_name:string;duration:number;waveform:number[]}
const cache=new Map<string,ArtifactWaveform>()
type Pending={run:()=>void;signal:AbortSignal;cancel:()=>void}
const queue:Pending[]=[]
let running=0
let generation=0
export function clearWaveforms(){generation++;cache.clear()}
addEventListener('audio-intel:unauthorized',clearWaveforms)

function drain(){
 while(running<2&&queue.length){
  const next=queue.shift()!
  next.signal.removeEventListener('abort',next.cancel)
  if(next.signal.aborted){next.cancel();continue}
  running++
  next.run()
 }
}

export function readWaveform(url:string,signal:AbortSignal):Promise<ArtifactWaveform>{
 if(signal.aborted)return Promise.reject(new DOMException('Aborted','AbortError'))
 const existing=cache.get(url)
 if(existing){cache.delete(url);cache.set(url,existing);return Promise.resolve(existing)}
 const epoch=generation
 return new Promise((resolve,reject)=>{
  const pending:Pending={signal,cancel:()=>{const index=queue.indexOf(pending);if(index>=0)queue.splice(index,1);reject(new DOMException('Aborted','AbortError'))},run:()=>{
   void request<ArtifactWaveform>(url,{signal}).then(value=>{
    if(!Number.isFinite(value.duration)||value.duration<=0||!Array.isArray(value.waveform)||!value.waveform.length||value.waveform.length>240||value.waveform.some(peak=>!Number.isFinite(peak)||peak<0||peak>1))throw new Error('Invalid waveform response')
    if(!signal.aborted&&generation===epoch){cache.delete(url);cache.set(url,value);while(cache.size>64)cache.delete(cache.keys().next().value!)}
    resolve(value)
   }).catch(reject).finally(()=>{running--;drain()})
  }}
  queue.push(pending)
  signal.addEventListener('abort',pending.cancel,{once:true})
  drain()
 })
}
