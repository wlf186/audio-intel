import type {BatchDeleteResult,Health,Job} from './types'

const key=()=>sessionStorage.getItem('audio-intel:key')||''
export async function request<T>(path:string,init:RequestInit={}):Promise<T>{
  const headers=new Headers(init.headers); if(key()) headers.set('Authorization',`Bearer ${key()}`)
  const response=await fetch(path,{...init,headers}); if(!response.ok){const body=await response.json().catch(()=>({detail:response.statusText}));throw new Error(body.detail||body.title||`HTTP ${response.status}`)}
  if(response.status===204)return undefined as T; return response.json()
}
export const api={
  jobs:(query='')=>request<{items:Job[]}>(`/api/v1/jobs${query}`),
  job:(id:string)=>request<Job>(`/api/v1/jobs/${id}`),
  health:()=>request<Health>('/api/v1/health'),
  submitAsr:(data:FormData)=>request<Job>('/api/v1/asr/jobs',{method:'POST',body:data}),
  submitTts:(data:FormData)=>request<Job>('/api/v1/tts/jobs',{method:'POST',body:data}),
  cancel:(id:string)=>request<Job>(`/api/v1/jobs/${id}/cancel`,{method:'POST'}),
  retry:(id:string)=>request<Job>(`/api/v1/jobs/${id}/retry`,{method:'POST'}),
  remove:(id:string)=>request<void>(`/api/v1/jobs/${id}?purge=true`,{method:'DELETE'}),
  removeMany:(jobIds:string[])=>request<BatchDeleteResult>('/api/v1/jobs/batch-delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_ids:jobIds,purge:true})}),
  voices:()=>request<{items:Array<{id:string;name:string;language:string}>;preset_speakers:string[]}>('/api/v1/tts/voices'),
  addVoice:(data:FormData)=>request<{id:string}>('/api/v1/tts/voices',{method:'POST',body:data}),
}
export function artifactUrl(jobId:string,name:string){return `/api/v1/jobs/${jobId}/artifacts/${encodeURIComponent(name)}`}
export function sourceUrl(jobId:string,download=false){return `/api/v1/jobs/${jobId}/source${download?'?download=true':''}`}
export function formatTime(value=0,decimal=true){const ms=Math.max(0,Math.round(value*1000));const h=Math.floor(ms/3600000);const m=Math.floor(ms%3600000/60000);const s=Math.floor(ms%60000/1000);return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}${decimal?'.'+String(ms%1000).padStart(3,'0'):''}`}
export function size(value=0){if(value<1024)return `${value} B`;if(value<1048576)return `${(value/1024).toFixed(1)} KB`;return `${(value/1048576).toFixed(1)} MB`}
