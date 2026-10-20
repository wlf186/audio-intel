import {HttpError,request,createIdempotencyKey,type SubmissionOptions} from './api'
import type {Job} from './types'
import i18n from '../i18n'

export const videoResumeKey='audio-intel:video-upload:v1'
export type VideoRegion={x:number;y:number;width:number;height:number}
export type VideoCapability={supported:boolean;formats:string[];max_upload_bytes:number;chunk_bytes:number;upload_idle_seconds:number;upload_lifetime_seconds:number;max_scenes:number;scene_editing:boolean;scene_detection?:import('./sceneDetection').SceneDetectionCapability}
type Upload={id:string;name:string;size_bytes:number;offset:number;expires_at:number;chunk_bytes:number;job_id:string|null;chunks:Array<{offset:number;size_bytes:number;sha256:string}>}
type Resume={id?:string;name:string;size:number;createKey:string;jobKey:string;params:Record<string,unknown>}
export function loadVideoResume():Resume|undefined{
 try{
  const saved:Resume|null=JSON.parse(sessionStorage.getItem(videoResumeKey)||'null')
  if(!saved)return undefined
  // Resume the submitted settings of uploads created before enhancement existed.
  return typeof saved.params.speech_enhancement==='boolean'||typeof saved.params.background_denoise==='boolean'?saved:{...saved,params:{...saved.params,audio_enhancement:saved.params.audio_enhancement??'off'}}
 }catch{return undefined}
}
function saveResume(value:Resume){sessionStorage.setItem(videoResumeKey,JSON.stringify(value))}
export async function discardVideoUpload(){const saved=loadVideoResume();if(saved?.id){try{await request(`/api/v1/asr/video-uploads/${saved.id}`,{method:'DELETE'})}catch(error){if(!(error instanceof HttpError)||![404,410].includes(error.status))throw error}};sessionStorage.removeItem(videoResumeKey)}
function chunkRequest(url:string,block:Blob,offset:number,checksum:string,total:number,options:SubmissionOptions):Promise<Upload>{
 return new Promise((resolve,reject)=>{
  const xhr=new XMLHttpRequest()
  const abort=()=>xhr.abort()
  const cleanup=()=>options.signal?.removeEventListener('abort',abort)
  xhr.open('PATCH',url)
  xhr.setRequestHeader('Content-Type','application/octet-stream')
  xhr.setRequestHeader('Upload-Offset',String(offset))
  xhr.setRequestHeader('Upload-Checksum',checksum)
  xhr.upload.onprogress=event=>options.onProgress?.({phase:'uploading',loadedBytes:offset+event.loaded,totalBytes:total,percent:Math.min(100,Math.floor((offset+event.loaded)/total*100))})
  xhr.onload=()=>{cleanup();let body:Upload&{detail?:string};try{body=JSON.parse(xhr.responseText)}catch{reject(new Error(i18n.t('errors.requestFailed',{status:xhr.status})));return}if(xhr.status>=200&&xhr.status<300)resolve(body);else{if(xhr.status===401)window.dispatchEvent(new Event('audio-intel:unauthorized'));reject(new HttpError(xhr.status,body.detail||i18n.t('errors.requestFailed',{status:xhr.status}),Number(xhr.getResponseHeader('Retry-After'))||undefined))}}
  xhr.onerror=()=>{cleanup();reject(new Error(i18n.t('errors.network')))}
  xhr.onabort=()=>{cleanup();reject(new DOMException('Paused','AbortError'))}
  if(options.signal?.aborted){reject(new DOMException('Paused','AbortError'));return}
  options.signal?.addEventListener('abort',abort,{once:true})
  xhr.send(block)
 })
}
export async function submitVideo(file:File,params:Record<string,unknown>,options:SubmissionOptions):Promise<Job>{
 const worker=new Worker(new URL('./videoHash.worker.ts',import.meta.url),{type:'module'})
 const hash=(blob:Blob)=>new Promise<string>((resolve,reject)=>{
  const abort=()=>{worker.terminate();reject(new DOMException('Paused','AbortError'))}
  if(options.signal?.aborted){abort();return}
  options.signal?.addEventListener('abort',abort,{once:true})
  worker.onmessage=(event:MessageEvent<string>)=>{options.signal?.removeEventListener('abort',abort);resolve(event.data)}
  worker.onerror=()=>{options.signal?.removeEventListener('abort',abort);reject(new Error(i18n.t('video.hashError')))}
  void blob.arrayBuffer().then(data=>{if(!options.signal?.aborted)worker.postMessage(data,[data])},reject)
 })
 try{
  let saved=loadVideoResume()
  if(saved&&(saved.name!==file.name||saved.size!==file.size))throw new Error(i18n.t('video.resumeMismatch'))
  if(!saved){saved={name:file.name,size:file.size,createKey:createIdempotencyKey(),jobKey:createIdempotencyKey(),params};saveResume(saved)}
  options.onProgress?.({phase:'preparing',loadedBytes:0,totalBytes:file.size})
  let upload:Upload
  if(saved.id){upload=await request<Upload>(`/api/v1/asr/video-uploads/${saved.id}`,{signal:options.signal})}
  else{
   upload=await request<Upload>('/api/v1/asr/video-uploads',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':saved.createKey},body:JSON.stringify({name:file.name,size_bytes:file.size}),signal:options.signal})
   saved={...saved,id:upload.id};saveResume(saved)
  }
  if(upload.job_id){const job=await request<Job>(`/api/v1/jobs/${upload.job_id}`);sessionStorage.removeItem(videoResumeKey);return job}
  if(upload.chunks.length)options.onProgress?.({phase:'verifying',loadedBytes:0,totalBytes:upload.offset,percent:0})
  for(const chunk of upload.chunks){
   if(await hash(file.slice(chunk.offset,chunk.offset+chunk.size_bytes))!==chunk.sha256)throw new Error(i18n.t('video.resumeMismatch'))
   const verified=chunk.offset+chunk.size_bytes
   options.onProgress?.({phase:'verifying',loadedBytes:verified,totalBytes:upload.offset,percent:Math.floor(verified/upload.offset*100)})
  }
  if(upload.offset<file.size)options.onProgress?.({phase:'uploading',loadedBytes:upload.offset,totalBytes:file.size,percent:Math.floor(upload.offset/file.size*100)})
  while(upload.offset<file.size){
   const offset=upload.offset
   const block=file.slice(offset,Math.min(file.size,offset+upload.chunk_bytes))
   const checksum=await hash(block)
   upload=await chunkRequest(`/api/v1/asr/video-uploads/${upload.id}`,block,offset,checksum,file.size,options)
  }
  options.onProgress?.({phase:'creating',loadedBytes:file.size,totalBytes:file.size,percent:100})
  const job=await request<Job>('/api/v1/asr/video-jobs',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':saved.jobKey},body:JSON.stringify({...saved.params,upload_id:upload.id}),signal:options.signal})
  sessionStorage.removeItem(videoResumeKey)
  return job
 }finally{worker.terminate()}
}
