import type {AuthSession,BatchDeleteResult,Capabilities,Health,HotwordList,Job,JobListQuery,JobListResponse,JobResult,Probe,TlsBootstrap,VoiceprintPerson,VoiceprintSample} from './types'

export class HttpError extends Error{status:number;retryAfter?:number;constructor(status:number,message:string,retryAfter?:number){super(message);this.status=status;this.retryAfter=retryAfter}}
export type SubmissionPhase='preparing'|'uploading'|'creating'
export type SubmissionProgress={phase:SubmissionPhase;loadedBytes:number;totalBytes?:number;percent?:number}
export type SubmissionOptions={idempotencyKey?:string;onProgress?:(progress:SubmissionProgress)=>void;signal?:AbortSignal}
export function isUploadCancelled(cause:unknown){return cause instanceof DOMException&&cause.name==='AbortError'}
function detailMessage(detail:unknown,status:number){
  if(typeof detail==='string'&&detail.trim())return detail
  if(Array.isArray(detail)){
    const messages=detail.map(item=>{
      if(typeof item==='string')return item
      if(!item||typeof item!=='object')return ''
      const entry=item as {msg?:unknown;loc?:unknown[]}
      const location=Array.isArray(entry.loc)?entry.loc.filter(value=>value!=='body').join(' → '):''
      const message=typeof entry.msg==='string'?entry.msg:''
      return [location,message].filter(Boolean).join('：')
    }).filter(Boolean)
    if(messages.length)return messages.join('；')
  }
  if(detail&&typeof detail==='object'){
    const entry=detail as {message?:unknown;title?:unknown}
    if(typeof entry.message==='string')return entry.message
    if(typeof entry.title==='string')return entry.title
  }
  return `请求失败（HTTP ${status}）`
}
export async function request<T>(path:string,init:RequestInit={}):Promise<T>{
  const headers=new Headers(init.headers)
  let response:Response
  try{response=await fetch(path,{...init,headers,credentials:'same-origin'})}catch(cause){throw new Error(cause instanceof TypeError?'无法连接本地服务，请确认服务已启动后重试。':(cause as Error).message)}
  if(!response.ok){const body=await response.json().catch(()=>({detail:response.statusText,retry_after_seconds:undefined}));if(response.status===401)window.dispatchEvent(new Event('audio-intel:unauthorized'));const retryAfter=Number(response.headers.get('Retry-After')||body.retry_after_seconds)||undefined;const detail=detailMessage(body.detail||body.title,response.status);throw new HttpError(response.status,retryAfter?`${detail}；请在 ${retryAfter} 秒后重试。`:detail,retryAfter)}
  if(response.status===204)return undefined as T; return response.json()
}
function requestForm<T>(path:string,data:FormData,key:string,{onProgress,signal}:SubmissionOptions={}):Promise<T>{
  return new Promise((resolve,reject)=>{
    if(signal?.aborted){reject(new DOMException('上传已取消','AbortError'));return}
    const xhr=new XMLHttpRequest()
    let settled=false
    let loadedBytes=0
    let totalBytes: number|undefined
    let lastProgressAt=0
    const emit=(progress:SubmissionProgress)=>onProgress?.(progress)
    const cleanup=()=>signal?.removeEventListener('abort',abort)
    const finish=(action:()=>void)=>{if(settled)return;settled=true;cleanup();action()}
    const abort=()=>xhr.abort()
    xhr.open('POST',path)
    xhr.withCredentials=true
    xhr.setRequestHeader('Idempotency-Key',key)
    xhr.upload.onloadstart=()=>emit({phase:'preparing',loadedBytes:0})
    xhr.upload.onprogress=event=>{
      const now=performance.now()
      loadedBytes=event.loaded
      totalBytes=event.lengthComputable?event.total:undefined
      if(now-lastProgressAt<100&&(!totalBytes||loadedBytes<totalBytes))return
      lastProgressAt=now
      emit({phase:'uploading',loadedBytes,totalBytes,percent:totalBytes?Math.min(100,Math.round(loadedBytes/totalBytes*100)):undefined})
    }
    xhr.upload.onload=()=>emit({phase:'creating',loadedBytes:totalBytes||loadedBytes,totalBytes,percent:100})
    xhr.onload=()=>finish(()=>{
      let body:unknown
      try{body=xhr.responseText?JSON.parse(xhr.responseText):undefined}catch{body={detail:xhr.statusText}}
      if(xhr.status>=200&&xhr.status<300){resolve(body as T);return}
      if(xhr.status===401)window.dispatchEvent(new Event('audio-intel:unauthorized'))
      const responseBody=(body&&typeof body==='object'?body:{}) as {detail?:unknown;title?:unknown;retry_after_seconds?:unknown}
      const retryAfter=Number(xhr.getResponseHeader('Retry-After')||responseBody.retry_after_seconds)||undefined
      const detail=detailMessage(responseBody.detail||responseBody.title,xhr.status)
      reject(new HttpError(xhr.status,retryAfter?`${detail}；请在 ${retryAfter} 秒后重试。`:detail,retryAfter))
    })
    xhr.onerror=()=>finish(()=>reject(new Error('无法连接本地服务，请确认服务已启动后重试。')))
    xhr.ontimeout=()=>finish(()=>reject(new Error('上传请求超时，请检查网络后重试。')))
    xhr.onabort=()=>finish(()=>reject(new DOMException('上传已取消','AbortError')))
    signal?.addEventListener('abort',abort,{once:true})
    emit({phase:'preparing',loadedBytes:0})
    xhr.send(data)
  })
}
function queryString(values:JobListQuery){const params=new URLSearchParams();for(const [key,value] of Object.entries(values)){if(value!==undefined&&value!=='')params.set(key,String(value))}const encoded=params.toString();return encoded?`?${encoded}`:''}
const pendingSubmissionKeys=new Map<string,string>()
function createIdempotencyKey(){
  const cryptoApi=globalThis.crypto
  if(!cryptoApi||typeof cryptoApi.getRandomValues!=='function')throw new Error('当前浏览器无法生成安全的提交标识，请升级浏览器后重试。')
  if(typeof cryptoApi.randomUUID==='function')return cryptoApi.randomUUID()
  const bytes=cryptoApi.getRandomValues(new Uint8Array(16))
  bytes[6]=(bytes[6]&0x0f)|0x40
  bytes[8]=(bytes[8]&0x3f)|0x80
  const hex=Array.from(bytes,byte=>byte.toString(16).padStart(2,'0')).join('')
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`
}
function formSignature(path:string,data:FormData){
  const values=[...data.entries()].map(([name,value])=>[name,typeof value==='string'?value:{name:value.name,size:value.size,type:value.type,lastModified:value.lastModified}] as const)
  values.sort(([left],[right])=>left.localeCompare(right))
  return `${path}:${JSON.stringify(values)}`
}
async function submitForm<T>(path:string,data:FormData,options:SubmissionOptions={}){
  const signature=formSignature(path,data)
  const key=options.idempotencyKey||pendingSubmissionKeys.get(signature)||createIdempotencyKey()
  if(!options.idempotencyKey)pendingSubmissionKeys.set(signature,key)
  try{const result=await requestForm<T>(path,data,key,options);if(!options.idempotencyKey)pendingSubmissionKeys.delete(signature);return result}catch(error){throw error}
}
export const api={
  probe:()=>request<Probe>('/api/v1/health'),
  auth:()=>request<AuthSession>('/api/v1/auth/session'),
  tlsBootstrap:()=>request<TlsBootstrap>('/api/v1/tls/bootstrap'),
  login:(key:string)=>request<void>('/api/v1/auth/session',{method:'POST',headers:{Authorization:`Bearer ${key}`}}),
  logout:()=>request<void>('/api/v1/auth/session',{method:'DELETE'}),
  jobs:(query:JobListQuery={})=>request<JobListResponse>(`/api/v1/jobs${queryString(query)}`),
  job:(id:string)=>request<Job>(`/api/v1/jobs/${id}`),
  system:()=>request<Health>('/api/v1/system'),
  capabilities:()=>request<Capabilities>('/api/v1/capabilities'),
  hotwordLists:()=>request<{items:HotwordList[];count:number}>('/api/v1/asr/hotword-lists'),
  addHotwordList:(name:string,terms:string[])=>request<HotwordList>('/api/v1/asr/hotword-lists',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,terms})}),
  updateHotwordList:(id:string,name:string,terms:string[])=>request<HotwordList>(`/api/v1/asr/hotword-lists/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,terms})}),
  removeHotwordList:(id:string)=>request<void>(`/api/v1/asr/hotword-lists/${id}`,{method:'DELETE'}),
  submitAsr:(data:FormData,options?:SubmissionOptions)=>submitForm<Job>('/api/v1/asr/jobs',data,options),
  analyzeCloneReference:(data:FormData,options?:SubmissionOptions)=>submitForm<Job>('/api/v1/tts/clone-references',data,options),
  submitTts:(data:FormData,options?:SubmissionOptions)=>submitForm<Job>('/api/v1/tts/jobs',data,options),
  cancel:(id:string)=>request<Job>(`/api/v1/jobs/${id}/cancel`,{method:'POST'}),
  retry:(id:string)=>request<Job>(`/api/v1/jobs/${id}/retry`,{method:'POST'}),
  remove:(id:string)=>request<void>(`/api/v1/jobs/${id}?purge=true`,{method:'DELETE'}),
  removeMany:(jobIds:string[])=>request<BatchDeleteResult>('/api/v1/jobs/batch-delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_ids:jobIds,purge:true})}),
  voices:()=>request<{items:Array<{id:string;name:string;language:string}>;preset_speakers:string[]}>('/api/v1/tts/voices'),
  addVoice:(data:FormData)=>request<{id:string}>('/api/v1/tts/voices',{method:'POST',body:data}),
  renameSpeaker:(jobId:string,speakerId:string,name:string)=>request<JobResult>(`/api/v1/jobs/${jobId}/speakers/${encodeURIComponent(speakerId)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})}),
  voiceprints:()=>request<{items:VoiceprintPerson[]}>('/api/v1/voiceprints/people'),
  addVoiceprintPerson:(name:string,note:string|null=null,includeInHotwordLibrary=true)=>request<VoiceprintPerson>('/api/v1/voiceprints/people',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,note,include_in_hotword_library:includeInHotwordLibrary})}),
  updateVoiceprintPerson:(id:string,name:string,note:string|null,includeInHotwordLibrary:boolean)=>request<VoiceprintPerson>(`/api/v1/voiceprints/people/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,note,include_in_hotword_library:includeInHotwordLibrary})}),
  removeVoiceprintPerson:(id:string)=>request<void>(`/api/v1/voiceprints/people/${id}?purge=true`,{method:'DELETE'}),
  addAsrSamples:(personId:string,jobId:string,segmentIds:number[])=>request<{items:VoiceprintSample[]}>(`/api/v1/voiceprints/people/${personId}/samples/from-asr`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:jobId,segment_ids:segmentIds})}),
  uploadVoiceprintSample:(personId:string,data:FormData,options?:SubmissionOptions)=>submitForm<{sample:VoiceprintSample;job:Job}>(`/api/v1/voiceprints/people/${personId}/samples/upload`,data,options),
  removeVoiceprintSample:(personId:string,sampleId:string)=>request<void>(`/api/v1/voiceprints/people/${personId}/samples/${sampleId}?purge=true`,{method:'DELETE'}),
}
export function artifactUrl(jobId:string,name:string){return `/api/v1/jobs/${jobId}/artifacts/${encodeURIComponent(name)}`}
export function sourceUrl(jobId:string,download=false){return `/api/v1/jobs/${jobId}/source${download?'?download=true':''}`}
export function formatTime(value=0,decimal=true){const ms=Math.max(0,Math.round(value*1000));const h=Math.floor(ms/3600000);const m=Math.floor(ms%3600000/60000);const s=Math.floor(ms%60000/1000);return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}${decimal?'.'+String(ms%1000).padStart(3,'0'):''}`}
export function size(value=0){if(value<1024)return `${value} B`;if(value<1048576)return `${(value/1024).toFixed(1)} KB`;return `${(value/1048576).toFixed(1)} MB`}
export function uploadLimitMessage(file:File,maxBytes?:number){return maxBytes&&file.size>maxBytes?`文件大小 ${size(file.size)}，超过服务允许的 ${size(maxBytes)} 上限。请选择更小的文件。`:''}
