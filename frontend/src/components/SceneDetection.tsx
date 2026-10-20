import {useEffect,useRef,useState} from 'react'
import {useTranslation} from 'react-i18next'
import {createIdempotencyKey,formatTime,HttpError,request} from '../lib/api'
import {detectionDefaults,loadDetection,saveDetection,validDetection,type SceneDetectionSettings,type SceneDetectionCapability} from '../lib/sceneDetection'
import type {Job,JobResult,VideoScene} from '../lib/types'
import {Modal} from './Modal'

type Preview={id:string;state:'running'|'ready'|'failed'|'cancelled'|'applied';settings:SceneDetectionSettings;scenes?:Array<VideoScene&{preview_url:string}>;error?:string;progress?:{phase:'scanning'|'detecting'|'images';completed:number;total:number}}
function PreviewImage({url,label}:{url:string;label:string}){
 const {t}=useTranslation()
 const [error,setError]=useState(false)
 const [retry,setRetry]=useState(0)
 return error?<button className="button" onClick={()=>{setError(false);setRetry(x=>x+1)}}>{t('video.frameFailed')} · {t('common.actions.reload')}</button>:<img loading="lazy" src={`${url}?retry=${retry}`} alt={label} onError={()=>setError(true)}/>
}
export function SceneDetection({jobId,result,capability,onClose,onUpdated}:{jobId:string;result:JobResult;capability:SceneDetectionCapability;onClose:()=>void;onUpdated:(id:string,result:JobResult)=>void}){
 const {t}=useTranslation()
 const video=result.video!
 const [openingRevision]=useState(video.revision)
 const [values,setValues]=useState(()=>video.scene_detection||loadDetection(capability))
 const [preview,setPreview]=useState<Preview>()
 const [creating,setCreating]=useState(false)
 const [applying,setApplying]=useState(false)
 const [error,setError]=useState('')
 const [pollRetry,setPollRetry]=useState(0)
 const [blocked,setBlocked]=useState(false)
 const [current,setCurrent]=useState(false)
 const [confirmed,setConfirmed]=useState(false)
 const [visible,setVisible]=useState(12)
 const alive=useRef(true)
 const previewId=useRef<string|undefined>(undefined)
 const attempt=useRef<{body:string;key:string}|undefined>(undefined)
 const cooldown=useRef<ReturnType<typeof setTimeout>|undefined>(undefined)
 const root=`/api/v1/jobs/${jobId}/video-scene-previews`
 const stale=openingRevision!==video.revision
 const changed=Boolean(preview&&(preview.settings.min_change_percent!==values.min_change_percent||preview.settings.min_stable_seconds!==values.min_stable_seconds))
 const manual=JSON.stringify(video.scenes)!==JSON.stringify(video.automatic_scenes||video.original_scenes)
 const ready=preview?.state==='ready'&&!changed&&!stale
 const busy=creating||applying||preview?.state==='running'
 const valid=validDetection(values,capability)
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;clearTimeout(cooldown.current);if(previewId.current)void request(`${root}/${previewId.current}`,{method:'DELETE',keepalive:true}).catch(()=>{})}},[root])
 function failure(cause:unknown){
  setError((cause as Error).message)
  if(cause instanceof HttpError&&cause.status===409)void request<Job>(`/api/v1/jobs/${jobId}`).then(next=>{if(alive.current&&next.result)onUpdated(jobId,next.result)},()=>{})
  if(cause instanceof HttpError&&cause.retryAfter){setBlocked(true);clearTimeout(cooldown.current);cooldown.current=setTimeout(()=>{if(alive.current)setBlocked(false)},cause.retryAfter*1000)}
 }
 useEffect(()=>{
  if(!preview||preview.state!=='running')return
  const controller=new AbortController()
  let timer:ReturnType<typeof setTimeout>|undefined
  async function poll(){
   try{const next=await request<Preview>(`${root}/${preview!.id}`,{signal:controller.signal});if(!controller.signal.aborted){setPreview(next);if(next.state==='running')timer=setTimeout(poll,500)}}
   catch(cause){if(!controller.signal.aborted){failure(cause);if(cause instanceof HttpError&&[404,410].includes(cause.status)){previewId.current=undefined;setPreview(undefined)}}}
  }
  timer=setTimeout(poll,250)
  return()=>{clearTimeout(timer);controller.abort()}
 },[root,preview?.id,preview?.state,pollRetry])
 async function generate(){
  if(!valid||busy||stale||blocked)return
  setCreating(true);setError('');setConfirmed(false)
  try{
   if(previewId.current){try{await request(`${root}/${previewId.current}`,{method:'DELETE'})}catch(cause){if(!(cause instanceof HttpError)||![404,410].includes(cause.status))throw cause}previewId.current=undefined;setPreview(undefined)}
   const body=JSON.stringify({revision:openingRevision,settings:values})
   if(attempt.current?.body!==body)attempt.current={body,key:createIdempotencyKey()}
   const next=await request<Preview>(root,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':attempt.current.key},body})
   if(!alive.current){void request(`${root}/${next.id}`,{method:'DELETE'}).catch(()=>{});return}
   previewId.current=next.id;setPreview(next);setCurrent(false);setVisible(12);attempt.current=undefined
  }catch(cause){if(alive.current)failure(cause)}finally{if(alive.current)setCreating(false)}
 }
 async function cancel(){
  if(!previewId.current)return
  setCreating(true);setError('')
  try{await request(`${root}/${previewId.current}`,{method:'DELETE'});previewId.current=undefined;setPreview(undefined)}
  catch(cause){if(alive.current)failure(cause)}finally{if(alive.current)setCreating(false)}
 }
 async function apply(){
  if(!ready||applying||manual&&!confirmed)return
  setApplying(true);setError('')
  try{
   const updated=await request<JobResult>(`/api/v1/jobs/${jobId}/video-scenes`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'apply_detection',preview_id:preview!.id,revision:openingRevision})})
   if(alive.current){saveDetection(values);onUpdated(jobId,updated);onClose()}
  }catch(cause){
   try{
    const next=await request<Job>(`/api/v1/jobs/${jobId}`)
    if(alive.current&&next.result?.video?.applied_preview_id===preview?.id){saveDetection(values);onUpdated(jobId,next.result);onClose();return}
   }catch{/* Keep the original error and allow a later retry. */}
   if(alive.current)failure(cause)
  }finally{if(alive.current)setApplying(false)}
 }
 const progress=preview?.progress
 const scenes=current?video.scenes:preview?.scenes||[]
 return <Modal className="scene-detection-modal" title={t('sceneDetection.title')} closeLabel={t('common.actions.close')} onClose={()=>{if(!applying)onClose()}}>
  <p>{t('sceneDetection.intro')}</p>
  {!video.scene_detection?<p className="scene-detection-note">{t('sceneDetection.legacy')}</p>:null}
  <div className="scene-detection-controls">{(['min_change_percent','min_stable_seconds'] as const).map(key=><label key={key}>
   <span>{t(`sceneDetection.${key}`)}</span>
   <input type="number" min={capability[key].min} max={capability[key].max} step={capability[key].step} value={Number.isNaN(values[key])?'':values[key]} disabled={busy||stale} onChange={event=>setValues(value=>({...value,[key]:event.target.value===''?NaN:Number(event.target.value)}))}/>
   <small>{t(`sceneDetection.${key}Help`)}</small>
  </label>)}</div>
  <div className="scene-detection-actions">
   <button className="button" disabled={busy||stale} onClick={()=>setValues(detectionDefaults(capability))}>{t('sceneDetection.reset')}</button>
   <button className="button" disabled={!valid||busy||stale||blocked} onClick={()=>void generate()}>{t('sceneDetection.preview')}</button>
   {preview?.state==='running'?<button className="button" disabled={creating} onClick={()=>void cancel()}>{t('sceneDetection.cancel')}</button>:null}
  </div>
  <small>{t('sceneDetection.memory')}</small>
  {stale?<p role="alert" className="error">{t('sceneDetection.conflict')}</p>:null}
  {changed?<p role="status">{t('sceneDetection.changed')}</p>:null}
  {creating||preview?.state==='running'?<div role="status" className="scene-detection-progress"><span>{t(`sceneDetection.phases.${progress?.phase||'preparing'}`)}</span>{progress&&progress.total>0?<><progress value={progress.completed} max={progress.total}/><span>{Math.min(100,Math.floor(progress.completed/progress.total*100))}%</span></>:null}</div>:null}
  {error?<p role="alert" className="error">{error} {preview?.state==='running'?<button className="button" disabled={blocked} onClick={()=>{setError('');setPollRetry(x=>x+1)}}>{t('common.actions.reload')}</button>:null}</p>:null}
  {preview?.state==='failed'?<p role="alert" className="error">{t('sceneDetection.failed')}</p>:null}
  {preview?.scenes?<><p className="scene-detection-count" role="status">{t('sceneDetection.count',{before:video.scenes.length,after:preview.scenes.length})}</p>
   <div className="scene-detection-actions"><button className="button" aria-pressed={current} onClick={()=>{setCurrent(true);setVisible(12)}}>{t('sceneDetection.current')}</button><button className="button" aria-pressed={!current} onClick={()=>{setCurrent(false);setVisible(12)}}>{t('sceneDetection.candidate')}</button></div>
   <div className="scene-detection-grid">{scenes.slice(0,visible).map((scene,index)=><figure key={`${current}:${scene.id}:${scene.image}`}>
    <PreviewImage url={current?`/api/v1/jobs/${jobId}/video-frames/${scene.image}`:(scene as VideoScene&{preview_url:string}).preview_url} label={t('video.scene',{number:index+1})}/>
    <figcaption>{t('video.scene',{number:index+1})} · {formatTime(scene.start,false)}–{formatTime(scene.end,false)}</figcaption>
   </figure>)}</div>
   {visible<scenes.length?<button className="button" onClick={()=>setVisible(v=>v+12)}>{t('asr.results.loadMore')}</button>:null}
  </>:null}
  {manual?<label className="scene-detection-confirm"><input type="checkbox" checked={confirmed} onChange={event=>setConfirmed(event.target.checked)}/>{t('sceneDetection.replaceEdits')}</label>:null}
  <div className="scene-detection-actions"><button className="button primary" disabled={!ready||applying||manual&&!confirmed||blocked} onClick={()=>void apply()}>{t(applying?'sceneDetection.applying':'sceneDetection.apply')}</button></div>
 </Modal>
}
