import type {AuthSession,BatchDeleteResult,Capabilities,Health,Job,JobListQuery,JobListResponse,JobResult,Probe,VoiceprintPerson,VoiceprintSample} from './types'

export class HttpError extends Error{status:number;retryAfter?:number;constructor(status:number,message:string,retryAfter?:number){super(message);this.status=status;this.retryAfter=retryAfter}}
export async function request<T>(path:string,init:RequestInit={}):Promise<T>{
  const headers=new Headers(init.headers)
  const response=await fetch(path,{...init,headers,credentials:'same-origin'}); if(!response.ok){const body=await response.json().catch(()=>({detail:response.statusText,retry_after_seconds:undefined}));if(response.status===401)window.dispatchEvent(new Event('audio-intel:unauthorized'));const retryAfter=Number(response.headers.get('Retry-After')||body.retry_after_seconds)||undefined;const detail=body.detail||body.title||`HTTP ${response.status}`;throw new HttpError(response.status,retryAfter?`${detail}；请在 ${retryAfter} 秒后重试。`:detail,retryAfter)}
  if(response.status===204)return undefined as T; return response.json()
}
function queryString(values:JobListQuery){const params=new URLSearchParams();for(const [key,value] of Object.entries(values)){if(value!==undefined&&value!=='')params.set(key,String(value))}const encoded=params.toString();return encoded?`?${encoded}`:''}
const pendingSubmissionKeys=new Map<string,string>()
function formSignature(path:string,data:FormData){
  const values=[...data.entries()].map(([name,value])=>[name,typeof value==='string'?value:{name:value.name,size:value.size,type:value.type,lastModified:value.lastModified}] as const)
  values.sort(([left],[right])=>left.localeCompare(right))
  return `${path}:${JSON.stringify(values)}`
}
async function submitForm<T>(path:string,data:FormData,idempotencyKey?:string){
  if(idempotencyKey)return request<T>(path,{method:'POST',headers:{'Idempotency-Key':idempotencyKey},body:data})
  const signature=formSignature(path,data)
  const key=pendingSubmissionKeys.get(signature)||crypto.randomUUID()
  pendingSubmissionKeys.set(signature,key)
  try{const result=await request<T>(path,{method:'POST',headers:{'Idempotency-Key':key},body:data});pendingSubmissionKeys.delete(signature);return result}catch(error){throw error}
}
export const api={
  probe:()=>request<Probe>('/api/v1/health'),
  auth:()=>request<AuthSession>('/api/v1/auth/session'),
  login:(key:string)=>request<void>('/api/v1/auth/session',{method:'POST',headers:{Authorization:`Bearer ${key}`}}),
  logout:()=>request<void>('/api/v1/auth/session',{method:'DELETE'}),
  jobs:(query:JobListQuery={})=>request<JobListResponse>(`/api/v1/jobs${queryString(query)}`),
  job:(id:string)=>request<Job>(`/api/v1/jobs/${id}`),
  system:()=>request<Health>('/api/v1/system'),
  capabilities:()=>request<Capabilities>('/api/v1/capabilities'),
  submitAsr:(data:FormData,idempotencyKey?:string)=>submitForm<Job>('/api/v1/asr/jobs',data,idempotencyKey),
  analyzeCloneReference:(data:FormData,idempotencyKey?:string)=>submitForm<Job>('/api/v1/tts/clone-references',data,idempotencyKey),
  submitTts:(data:FormData,idempotencyKey?:string)=>submitForm<Job>('/api/v1/tts/jobs',data,idempotencyKey),
  cancel:(id:string)=>request<Job>(`/api/v1/jobs/${id}/cancel`,{method:'POST'}),
  retry:(id:string)=>request<Job>(`/api/v1/jobs/${id}/retry`,{method:'POST'}),
  remove:(id:string)=>request<void>(`/api/v1/jobs/${id}?purge=true`,{method:'DELETE'}),
  removeMany:(jobIds:string[])=>request<BatchDeleteResult>('/api/v1/jobs/batch-delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_ids:jobIds,purge:true})}),
  voices:()=>request<{items:Array<{id:string;name:string;language:string}>;preset_speakers:string[]}>('/api/v1/tts/voices'),
  addVoice:(data:FormData)=>request<{id:string}>('/api/v1/tts/voices',{method:'POST',body:data}),
  renameSpeaker:(jobId:string,speakerId:string,name:string)=>request<JobResult>(`/api/v1/jobs/${jobId}/speakers/${encodeURIComponent(speakerId)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})}),
  voiceprints:()=>request<{items:VoiceprintPerson[]}>('/api/v1/voiceprints/people'),
  addVoiceprintPerson:(name:string)=>request<VoiceprintPerson>('/api/v1/voiceprints/people',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})}),
  renameVoiceprintPerson:(id:string,name:string)=>request<VoiceprintPerson>(`/api/v1/voiceprints/people/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})}),
  removeVoiceprintPerson:(id:string)=>request<void>(`/api/v1/voiceprints/people/${id}?purge=true`,{method:'DELETE'}),
  addAsrSamples:(personId:string,jobId:string,segmentIds:number[])=>request<{items:VoiceprintSample[]}>(`/api/v1/voiceprints/people/${personId}/samples/from-asr`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:jobId,segment_ids:segmentIds})}),
  uploadVoiceprintSample:(personId:string,data:FormData,idempotencyKey?:string)=>submitForm<{sample:VoiceprintSample;job:Job}>(`/api/v1/voiceprints/people/${personId}/samples/upload`,data,idempotencyKey),
  removeVoiceprintSample:(personId:string,sampleId:string)=>request<void>(`/api/v1/voiceprints/people/${personId}/samples/${sampleId}?purge=true`,{method:'DELETE'}),
}
export function artifactUrl(jobId:string,name:string){return `/api/v1/jobs/${jobId}/artifacts/${encodeURIComponent(name)}`}
export function sourceUrl(jobId:string,download=false){return `/api/v1/jobs/${jobId}/source${download?'?download=true':''}`}
export function formatTime(value=0,decimal=true){const ms=Math.max(0,Math.round(value*1000));const h=Math.floor(ms/3600000);const m=Math.floor(ms%3600000/60000);const s=Math.floor(ms%60000/1000);return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}${decimal?'.'+String(ms%1000).padStart(3,'0'):''}`}
export function size(value=0){if(value<1024)return `${value} B`;if(value<1048576)return `${(value/1024).toFixed(1)} KB`;return `${(value/1048576).toFixed(1)} MB`}
