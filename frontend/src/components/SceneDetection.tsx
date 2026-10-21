import {useEffect,useRef,useState} from 'react'
import {useTranslation} from 'react-i18next'
import {createIdempotencyKey,formatTime,HttpError,request} from '../lib/api'
import {detectionDefaults,loadDetection,saveDetection,validDetection,type SceneDetectionSettings,type SceneDetectionCapability} from '../lib/sceneDetection'
import type {Job,JobResult,VideoScene} from '../lib/types'
import {Modal} from './Modal'

type Preview={id:string;state:'running'|'ready'|'failed'|'cancelled'|'applied';settings:SceneDetectionSettings;scenes?:Array<VideoScene&{preview_url:string}>;progress?:{phase:'scanning'|'detecting'|'images';completed:number;total:number}}
function PreviewImage({url,label}:{url:string;label:string}){
 const {t}=useTranslation()
 const [error,setError]=useState(false)
 const [retry,setRetry]=useState(0)
 return error?<button className="button" onClick={()=>{setError(false);setRetry(x=>x+1)}}>{t('video.frameFailed')} · {t('common.actions.reload')}</button>:<img loading="lazy" src={`${url}?retry=${retry}`} alt={label} onError={()=>setError(true)}/>
}
export function SceneDetection({jobId,result,capability,onClose,onUpdated}:{jobId:string;result:JobResult;capability:SceneDetectionCapability;onClose:()=>void;onUpdated:(id:string,result:JobResult)=>void}){
 const {t}=useTranslation()
 const video=result.video!
 const [openingRevision,setOpeningRevision]=useState(video.revision)
 const [values,setValues]=useState(()=>video.scene_detection||loadDetection(capability))
 const [preview,setPreview]=useState<Preview>()
 const [creating,setCreating]=useState(false)
 const [applying,setApplying]=useState(false)
 const [saving,setSaving]=useState(false)
 const [error,setError]=useState('')
 const [notice,setNotice]=useState('')
 const [conflict,setConflict]=useState(false)
 const [pollRetry,setPollRetry]=useState(0)
 const [blocked,setBlocked]=useState(false)
 const [current,setCurrent]=useState(true)
 const [confirmed,setConfirmed]=useState(false)
 const [visible,setVisible]=useState(12)
 const [selected,setSelected]=useState<Set<string>>(()=>new Set())
 const alive=useRef(true)
 const previewId=useRef<string|undefined>(undefined)
 const attempt=useRef<{body:string;key:string}|undefined>(undefined)
 const cooldown=useRef<ReturnType<typeof setTimeout>|undefined>(undefined)
 const scrollRoot=useRef<HTMLDivElement>(null)
 const sentinel=useRef<HTMLDivElement>(null)
 const root=`/api/v1/jobs/${jobId}/video-scene-previews`
 const stale=conflict||openingRevision!==video.revision
 const changed=Boolean(preview&&(preview.settings.min_change_percent!==values.min_change_percent||preview.settings.min_stable_seconds!==values.min_stable_seconds))
 const manual=JSON.stringify(video.scenes)!==JSON.stringify(video.automatic_scenes||video.original_scenes)
 const ready=preview?.state==='ready'&&!changed&&!stale
 const busy=creating||applying||saving||preview?.state==='running'
 const valid=validDetection(values,capability)
 const hidden=new Set(video.export_hidden_scene_ids||[])
 const clearsHidden=hidden.size>0&&(!video.scene_detection||video.scene_detection.min_change_percent!==values.min_change_percent||video.scene_detection.min_stable_seconds!==values.min_stable_seconds)
 const scenes=current?video.scenes:preview?.scenes||[]
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;clearTimeout(cooldown.current);if(previewId.current)void request(`${root}/${previewId.current}`,{method:'DELETE',keepalive:true}).catch(()=>{})}},[root])
 useEffect(()=>{
  const target=sentinel.current,element=scrollRoot.current
  if(!target||!element||visible>=scenes.length||typeof IntersectionObserver==='undefined')return
  let active=true
  const observer=new IntersectionObserver(entries=>{if(active&&entries.some(entry=>entry.isIntersecting))setVisible(value=>Math.min(scenes.length,value+12))},{root:element,rootMargin:'0px 0px 240px'})
  observer.observe(target)
  return()=>{active=false;observer.disconnect()}
 },[visible,scenes.length,current,preview?.id])
 function failure(cause:unknown){
  setError((cause as Error).message)
  if(cause instanceof HttpError&&cause.status===409)setConflict(true)
  if(cause instanceof HttpError&&cause.status===409)void request<Job>(`/api/v1/jobs/${jobId}`).then(next=>{if(alive.current&&next.result)onUpdated(jobId,next.result)},()=>{})
  if(cause instanceof HttpError&&cause.retryAfter){setBlocked(true);clearTimeout(cooldown.current);cooldown.current=setTimeout(()=>{if(alive.current)setBlocked(false)},cause.retryAfter*1000)}
 }
 useEffect(()=>{
  if(!preview||preview.state!=='running')return
  const controller=new AbortController()
  let timer:ReturnType<typeof setTimeout>|undefined
  async function poll(){
   try{const next=await request<Preview>(`${root}/${preview!.id}`,{signal:controller.signal});if(!controller.signal.aborted){setPreview(next);if(next.state==='running')timer=setTimeout(poll,500)}}
   catch(cause){if(!controller.signal.aborted){failure(cause);if(cause instanceof HttpError&&[404,410].includes(cause.status)){previewId.current=undefined;setPreview(undefined);setCurrent(true)}}}
  }
  timer=setTimeout(poll,250)
  return()=>{clearTimeout(timer);controller.abort()}
 },[root,preview?.id,preview?.state,pollRetry])
 async function discardPreview(){
  const id=previewId.current
  if(id){try{await request(`${root}/${id}`,{method:'DELETE'})}catch(cause){if(!(cause instanceof HttpError)||![404,410].includes(cause.status))throw cause}}
  previewId.current=undefined;setPreview(undefined)
 }
 function accept(updated:JobResult,message:string,applied=false){
  const id=previewId.current
  previewId.current=undefined;setPreview(undefined)
  if(id)void request(`${root}/${id}`,{method:'DELETE'}).catch(()=>{})
  setConflict(false);setOpeningRevision(updated.video!.revision);onUpdated(jobId,updated)
  setSelected(new Set());setCurrent(true);setNotice(message);setError('')
  if(applied){setVisible(12);setConfirmed(false);saveDetection(values)}
 }
 async function generate(){
  if(!valid||busy||stale||blocked)return
  setCreating(true);setError('');setNotice('');setConfirmed(false)
  try{
   await discardPreview()
   const body=JSON.stringify({revision:openingRevision,settings:values})
   if(attempt.current?.body!==body)attempt.current={body,key:createIdempotencyKey()}
   const next=await request<Preview>(root,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':attempt.current.key},body})
   if(!alive.current){void request(`${root}/${next.id}`,{method:'DELETE'}).catch(()=>{});return}
   previewId.current=next.id;setPreview(next);setCurrent(false);setVisible(12);setSelected(new Set());attempt.current=undefined
  }catch(cause){if(alive.current)failure(cause)}finally{if(alive.current)setCreating(false)}
 }
 async function cancel(){
  if(!previewId.current)return
  setCreating(true);setError('')
  try{await discardPreview();setCurrent(true)}catch(cause){if(alive.current)failure(cause)}finally{if(alive.current)setCreating(false)}
 }
 async function apply(){
  if(!ready||busy||manual&&!confirmed)return
  setApplying(true);setError('')
  try{
   const updated=await request<JobResult>(`/api/v1/jobs/${jobId}/video-scenes`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'apply_detection',preview_id:preview!.id,revision:openingRevision})})
   if(alive.current)accept(updated,t('sceneDetection.applied'),true)
  }catch(cause){
   try{
    const next=await request<Job>(`/api/v1/jobs/${jobId}`)
    if(alive.current&&next.result?.video?.applied_preview_id===preview?.id){accept(next.result,t('sceneDetection.applied'),true);return}
   }catch{/* Keep the original error and allow a later retry. */}
   if(alive.current)failure(cause)
  }finally{if(alive.current)setApplying(false)}
 }
 async function saveVisibility(mode:'hide'|'restore'|'all'){
  if(busy||stale||blocked||!current)return
  const nextHidden=new Set(hidden)
  if(mode==='all')nextHidden.clear()
  else for(const id of selected){if(mode==='hide')nextHidden.add(id);else nextHidden.delete(id)}
  const ids=video.scenes.filter(scene=>nextHidden.has(scene.id)).map(scene=>scene.id)
  const message=t(preview?'sceneDetection.visibilityInvalidated':'sceneDetection.visibilitySaved')
  setSaving(true);setError('');setNotice('')
  try{
   const updated=await request<JobResult>(`/api/v1/jobs/${jobId}/video-scenes`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'set_export_visibility',revision:openingRevision,export_hidden_scene_ids:ids})})
   if(alive.current)accept(updated,message)
  }catch(cause){
   // Reconcile a lost response without blindly replaying a stale revision.
   try{
    const next=await request<Job>(`/api/v1/jobs/${jobId}`)
    if(alive.current&&next.result?.video&&JSON.stringify(next.result.video.export_hidden_scene_ids||[])===JSON.stringify(ids)){accept(next.result,message);return}
   }catch{/* Preserve the failed operation and selection for retry. */}
   if(alive.current)failure(cause)
  }finally{if(alive.current)setSaving(false)}
 }
 function switchView(next:boolean){setCurrent(next);setVisible(12);setSelected(new Set())}
 const progress=preview?.progress
 return <Modal className="scene-detection-modal" title={t('sceneDetection.title')} closeLabel={t('common.actions.close')} onClose={()=>{if(!applying&&!saving)onClose()}}>
  <div className="scene-detection-body" ref={scrollRoot} tabIndex={0} aria-label={t('sceneDetection.gallery')}>
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
   {clearsHidden?<p className="scene-detection-note">{t('sceneDetection.clearsHidden')}</p>:null}
   {stale?<p role="alert" className="error">{t('sceneDetection.conflict')}</p>:null}
   {changed?<p role="status">{t('sceneDetection.changed')}</p>:null}
   {creating||preview?.state==='running'?<div role="status" className="scene-detection-progress"><span>{t(`sceneDetection.phases.${progress?.phase||'preparing'}`)}</span>{progress&&progress.total>0?<><progress value={progress.completed} max={progress.total}/><span>{Math.min(100,Math.floor(progress.completed/progress.total*100))}%</span></>:null}</div>:null}
   {preview?.state==='failed'?<p role="alert" className="error">{t('sceneDetection.failed')}</p>:null}
   {preview?.scenes?<p className="scene-detection-count" role="status">{t('sceneDetection.count',{before:video.scenes.length,after:preview.scenes.length})}</p>:null}
   {preview?<div className="scene-detection-actions"><button className="button" aria-pressed={current} onClick={()=>switchView(true)}>{t('sceneDetection.current')}</button><button className="button" aria-pressed={!current} onClick={()=>switchView(false)}>{t('sceneDetection.candidate')}</button></div>:<h3>{t('sceneDetection.current')}</h3>}
   {current?<div className="scene-detection-actions"><button className="button" disabled={busy||stale} onClick={()=>setSelected(new Set(scenes.slice(0,visible).map(s=>s.id)))}>{t('sceneDetection.selectLoaded')}</button><button className="button" disabled={!selected.size||busy} onClick={()=>setSelected(new Set())}>{t('sceneDetection.clearSelection')}</button></div>:<p>{t('sceneDetection.applyBeforeHide')}</p>}
   <div className="scene-detection-grid">{scenes.slice(0,visible).map((scene,index)=><figure className={selected.has(scene.id)?'selected':''} key={`${current}:${scene.id}:${scene.image}`}>
    {current?<label className="scene-detection-select"><input type="checkbox" checked={selected.has(scene.id)} disabled={busy||stale} onChange={()=>setSelected(previous=>{const next=new Set(previous);if(next.has(scene.id))next.delete(scene.id);else next.add(scene.id);return next})}/>{t('sceneDetection.selectPicture',{number:index+1})}</label>:null}
    <PreviewImage url={current?`/api/v1/jobs/${jobId}/video-frames/${scene.image}`:(scene as VideoScene&{preview_url:string}).preview_url} label={t('video.scene',{number:index+1})}/>
    <figcaption>{t('video.scene',{number:index+1})} · {formatTime(scene.start,false)}–{formatTime(scene.end,false)}{current&&hidden.has(scene.id)?<span className="scene-hidden-badge">{t('sceneDetection.hidden')}</span>:null}</figcaption>
   </figure>)}</div>
   <div className="scene-detection-sentinel" ref={sentinel}><span>{t('sceneDetection.loaded',{count:Math.min(visible,scenes.length),total:scenes.length})}</span>{visible<scenes.length&&typeof IntersectionObserver==='undefined'?<button className="button" onClick={()=>setVisible(value=>value+12)}>{t('asr.results.loadMore')}</button>:null}</div>
   {manual&&preview?<label className="scene-detection-confirm"><input type="checkbox" checked={confirmed} onChange={event=>setConfirmed(event.target.checked)}/>{t('sceneDetection.replaceEdits')}</label>:null}
  </div>
  <div className="scene-detection-footer">
   {error?<p role="alert" className="error">{error} {preview?.state==='running'?<button className="button" disabled={blocked} onClick={()=>{setError('');setPollRetry(x=>x+1)}}>{t('common.actions.reload')}</button>:null}</p>:null}
   {notice?<p role="status">{notice}</p>:null}
   {current?<><small>{t('sceneDetection.selectionSummary',{selected:selected.size,hidden:hidden.size})}</small><div className="scene-detection-actions">
    <button className="button" disabled={busy||stale||blocked||![...selected].some(id=>!hidden.has(id))} onClick={()=>void saveVisibility('hide')}>{t('sceneDetection.hide')}</button>
    <button className="button" disabled={busy||stale||blocked||![...selected].some(id=>hidden.has(id))} onClick={()=>void saveVisibility('restore')}>{t('sceneDetection.restore')}</button>
    <button className="button" disabled={busy||stale||blocked||!hidden.size} onClick={()=>void saveVisibility('all')}>{t('sceneDetection.restoreAll')}</button>
   </div></>:null}
   <div className="scene-detection-actions"><button className="primary" disabled={!ready||busy||manual&&!confirmed||blocked} onClick={()=>void apply()}>{t(applying?'sceneDetection.applying':'sceneDetection.apply')}</button><button className="button" disabled={applying||saving} onClick={onClose}>{t('common.actions.close')}</button></div>
  </div>
 </Modal>
}
