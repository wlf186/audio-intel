import type {AuthSession,BatchDeleteResult,Capabilities,Health,Job,JobResult,Probe,VoiceprintPerson,VoiceprintSample} from './types'

export class HttpError extends Error{status:number;constructor(status:number,message:string){super(message);this.status=status}}
export async function request<T>(path:string,init:RequestInit={}):Promise<T>{
  const headers=new Headers(init.headers)
  const response=await fetch(path,{...init,headers,credentials:'same-origin'}); if(!response.ok){const body=await response.json().catch(()=>({detail:response.statusText}));if(response.status===401)window.dispatchEvent(new Event('audio-intel:unauthorized'));throw new HttpError(response.status,body.detail||body.title||`HTTP ${response.status}`)}
  if(response.status===204)return undefined as T; return response.json()
}
export const api={
  probe:()=>request<Probe>('/api/v1/health'),
  auth:()=>request<AuthSession>('/api/v1/auth/session'),
  login:(key:string)=>request<void>('/api/v1/auth/session',{method:'POST',headers:{Authorization:`Bearer ${key}`}}),
  logout:()=>request<void>('/api/v1/auth/session',{method:'DELETE'}),
  jobs:(query='')=>request<{items:Job[]}>(`/api/v1/jobs${query}`),
  job:(id:string)=>request<Job>(`/api/v1/jobs/${id}`),
  system:()=>request<Health>('/api/v1/system'),
  capabilities:()=>request<Capabilities>('/api/v1/capabilities'),
  submitAsr:(data:FormData)=>request<Job>('/api/v1/asr/jobs',{method:'POST',body:data}),
  submitTts:(data:FormData)=>request<Job>('/api/v1/tts/jobs',{method:'POST',body:data}),
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
  uploadVoiceprintSample:(personId:string,data:FormData)=>request<{sample:VoiceprintSample;job:Job}>(`/api/v1/voiceprints/people/${personId}/samples/upload`,{method:'POST',body:data}),
  removeVoiceprintSample:(personId:string,sampleId:string)=>request<void>(`/api/v1/voiceprints/people/${personId}/samples/${sampleId}?purge=true`,{method:'DELETE'}),
}
export function artifactUrl(jobId:string,name:string){return `/api/v1/jobs/${jobId}/artifacts/${encodeURIComponent(name)}`}
export function sourceUrl(jobId:string,download=false){return `/api/v1/jobs/${jobId}/source${download?'?download=true':''}`}
export function formatTime(value=0,decimal=true){const ms=Math.max(0,Math.round(value*1000));const h=Math.floor(ms/3600000);const m=Math.floor(ms%3600000/60000);const s=Math.floor(ms%60000/1000);return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}${decimal?'.'+String(ms%1000).padStart(3,'0'):''}`}
export function size(value=0){if(value<1024)return `${value} B`;if(value<1048576)return `${(value/1024).toFixed(1)} KB`;return `${(value/1048576).toFixed(1)} MB`}
