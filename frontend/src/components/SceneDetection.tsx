import {SceneDetectionFields,SceneDetailChoice,SceneModeChoice,SceneChoice} from './SceneDetectionFields'
import {pictureNumbers} from '../lib/videoPresentation'
import {useEffect,useLayoutEffect,useRef,useState} from 'react'
import {useTranslation} from 'react-i18next'
import {createIdempotencyKey,formatTime,HttpError,request} from '../lib/api'
import {saveSceneDetail,type SceneDetail,detectionDefaults,saveSceneMode,loadDetection,saveDetection,validDetection,type SceneDetectionSettings,type SceneDetectionCapability} from '../lib/sceneDetection'
import type {Job,JobResult,VideoScene} from '../lib/types'
import {Modal} from './Modal'

type Preview={video_scene_detail?:SceneDetail;mode?:string;time_range?:[number,number]|null;id:string;state:'running'|'ready'|'failed'|'cancelled'|'applied';settings:SceneDetectionSettings|null;scenes?:Array<VideoScene&{preview_url:string|null}>;progress?:{phase:'scanning'|'detecting'|'selecting'|'images';completed:number;total:number}}
function PreviewImage({url,label}:{url:string|null;label:string}){
 const {t}=useTranslation()
 const [error,setError]=useState(false)
 const [retry,setRetry]=useState(0)
 if(!url)return <div className="video-no-frame">{t('video.noStableFrame')}</div>
 return error?<button className="button" onClick={()=>{setError(false);setRetry(x=>x+1)}}>{t('video.frameFailed')} · {t('common.actions.reload')}</button>:<img loading="lazy" src={`${url}?retry=${retry}`} alt={label} onError={()=>setError(true)}/>
}
function clockSeconds(text:string){
 const fields=text.trim().split(':')
 if(fields.length>3||fields.some((field,i)=>!(i===fields.length-1?/^\d+(\.\d+)?$/:/^\d+$/).test(field)||i>0&&Number(field)>=60))return NaN
 return fields.reduce((seconds,field)=>seconds*60+Number(field),0)
}
function clockText(value:number){return formatTime(value).replace(/\.000$/,'')}

export function SceneDetection({position,jobId,result,capability,onClose,onUpdated}:{position:number;jobId:string;result:JobResult;capability:SceneDetectionCapability;onClose:()=>void;onUpdated:(id:string,result:JobResult)=>void}){
 const {t}=useTranslation()
 const video=result.video!
 const [openingRevision,setOpeningRevision]=useState(video.revision)
 const [values,setValues]=useState(()=>video.scene_detection||loadDetection(capability))
 const hasDetail=Boolean(video.video_scene_detail)
 const autoResult=video.scene_detection_mode==='auto'||video.detector==='presentation-v4'||hasDetail
 const [detail,setDetail]=useState<SceneDetail>(video.video_scene_detail??'balanced')
 const [mode,setMode]=useState(autoResult?'auto':'custom')
 const [local,setLocal]=useState(Boolean(capability.range_preview&&video.duration>180))
 const initialStart=Math.max(0,Math.min(video.duration-180,position-90))
 const [startText,setStartText]=useState(()=>clockText(initialStart))
 const [endText,setEndText]=useState(()=>clockText(Math.min(video.duration,initialStart+180)))
 const start=clockSeconds(startText)
 // The displayed last millisecond may round beyond the actual media duration.
 const end=endText===clockText(video.duration)?video.duration:clockSeconds(endText)
 const [editRange,setEditRange]=useState(false)
 const [expanded,setExpanded]=useState(true)
 const [manage,setManage]=useState(false)
 const [zoom,setZoom]=useState<string>()
 const [nativeSize,setNativeSize]=useState(false)
 const zoomTrigger=useRef<HTMLButtonElement|null>(null)
 const zoomClose=useRef<HTMLButtonElement|null>(null)
 const positions=useRef([{top:0,count:12},{top:0,count:12}])
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
 const [allSections,setAllSections]=useState(false)
 const [selected,setSelected]=useState<Set<string>>(()=>new Set())
 const alive=useRef(true)
 const previewId=useRef<string|undefined>(undefined)
 const attempt=useRef<{body:string;key:string}|undefined>(undefined)
 const cooldown=useRef<ReturnType<typeof setTimeout>|undefined>(undefined)
 const scrollRoot=useRef<HTMLDivElement>(null)
 const sentinel=useRef<HTMLDivElement>(null)
 const root=`/api/v1/jobs/${jobId}/video-scene-previews`
 const stale=conflict||openingRevision!==video.revision
 const changed=Boolean(preview&&((preview.mode||'custom')!==mode||mode==='auto'&&Boolean(capability.detail_levels)&&(preview.video_scene_detail??'balanced')!==detail||JSON.stringify(preview.time_range??null)!==JSON.stringify(local?[start,end]:null)||mode==='custom'&&(preview.settings?.min_change_percent!==values.min_change_percent||preview.settings?.min_stable_seconds!==values.min_stable_seconds)))
 const manual=JSON.stringify(video.scenes)!==JSON.stringify(video.automatic_scenes||video.original_scenes)
 const ready=!local&&preview?.state==='ready'&&!changed&&!stale
 const busy=creating||applying||saving||preview?.state==='running'
 const valid=(mode==='auto'||validDetection(values,capability))&&(!local||start>=0&&start<end&&end<=video.duration)
 const hidden=new Set(video.export_hidden_scene_ids||[])
 const clearsHidden=hidden.size>0&&(mode==='auto'&&(detail!==video.video_scene_detail||video.detector!==capability.detector)||mode!==(autoResult?'auto':'custom')||mode==='custom'&&(!video.scene_detection||video.scene_detection.min_change_percent!==values.min_change_percent||video.scene_detection.min_stable_seconds!==values.min_stable_seconds))
 const resultsReady=current||Boolean(preview?.scenes)
 const allScenes=current?(local?video.scenes.filter(s=>s.end>start&&s.start<end):video.scenes):preview?.scenes||[]
 const numbers=pictureNumbers(allScenes)
 const scenes=allSections?allScenes:allScenes.filter(scene=>scene.image)
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
   try{const next=await request<Preview>(`${root}/${preview!.id}`,{signal:controller.signal});if(!controller.signal.aborted){setPreview(next);if(next.state==='ready'){setExpanded(false);scrollRoot.current?.scrollTo(0,0)}if(next.state==='running')timer=setTimeout(poll,500)}}
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
  if(applied){setExpanded(false);setManage(false);setVisible(12);setConfirmed(false);saveSceneMode(mode==='auto'?'auto':'custom');if(mode==='auto'&&capability.detail_levels)saveSceneDetail(detail);if(mode==='custom')saveDetection(values)}
 }
 async function generate(whole=false){
  if(!valid||busy||stale||blocked)return
  setCreating(true);setError('');setNotice('');setConfirmed(false)
  try{
   await discardPreview()
   if(whole)setLocal(false)
   const body=JSON.stringify({revision:openingRevision,...(mode==="auto"?{mode,...(capability.detail_levels?{video_scene_detail:detail}:{})}:{settings:values}),...(local&&!whole?{time_range:[start,end]}:{})})
   if(attempt.current?.body!==body)attempt.current={body,key:createIdempotencyKey()}
   const next=await request<Preview>(root,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':attempt.current.key},body})
   if(!alive.current){void request(`${root}/${next.id}`,{method:'DELETE'}).catch(()=>{});return}
   positions.current=[{top:0,count:12},{top:0,count:12}];setManage(false);setExpanded(next.state!=='ready');scrollRoot.current?.scrollTo(0,0);previewId.current=next.id;setPreview(next);setCurrent(false);setVisible(12);setSelected(new Set());attempt.current=undefined
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
 function switchView(next:boolean){
  if(next===current)return
  positions.current[Number(current)]={top:scrollRoot.current?.scrollTop||0,count:visible}
  setCurrent(next);setVisible(positions.current[Number(next)].count);setSelected(new Set());setManage(false)
 }
 useLayoutEffect(()=>{scrollRoot.current?.scrollTo(0,positions.current[Number(current)].top)},[current])
 const shownRange=!current&&preview?preview.time_range:local?[start,end]:null
 const rangeLabel=shownRange&&shownRange.every(Number.isFinite)?shownRange.map(clockText).join(' – '):t('sceneDetection.full')
 const pictures=allScenes.filter(scene=>scene.image)
 const zoomIndex=pictures.findIndex(scene=>scene.id===zoom)
 const focused=pictures[zoomIndex]
 const sourceUrl=(scene:VideoScene)=>current?`/api/v1/jobs/${jobId}/video-frames/${scene.image}`:(scene as VideoScene&{preview_url:string}).preview_url
 function closeZoom(){setNativeSize(false);setZoom(undefined);requestAnimationFrame(()=>zoomTrigger.current?.focus())}
 function moveZoom(index:number){if(pictures[index])setZoom(pictures[index].id)}
 useEffect(()=>{if(zoom)zoomClose.current?.focus()},[Boolean(zoom)])
 const progress=preview?.progress
 const previewLabel=t(local?'sceneDetection.previewRange':'sceneDetection.previewFull')
 return <Modal className="scene-detection-modal" title={t('sceneDetection.title')} closeLabel={t('common.actions.close')} onClose={()=>{if(focused)closeZoom();else if(!applying&&!saving)onClose()}}>
  {focused?<div className="scene-focus" onKeyDown={event=>{
   if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();moveZoom(zoomIndex+(event.key==='ArrowLeft'?-1:1))}
  }}>
   <div className={nativeSize?'scene-focus-image native':'scene-focus-image'}><PreviewImage key={focused.id} url={sourceUrl(focused)} label={clockText(focused.frame_time??focused.start)}/></div>
   <span>{clockText(focused.start)} – {clockText(focused.end)} · {zoomIndex+1} / {pictures.length}</span>
   <div><button className="button" disabled={!zoomIndex} onClick={()=>moveZoom(zoomIndex-1)}>{t('sceneDetection.prev')}</button>
   <button className="button" disabled={zoomIndex===pictures.length-1} onClick={()=>moveZoom(zoomIndex+1)}>{t('sceneDetection.next')}</button>
   <button className="button" aria-pressed={nativeSize} onClick={()=>setNativeSize(!nativeSize)}>{t('sceneDetection.nativeSize')}</button>
   <button ref={zoomClose} className="button" onClick={closeZoom}>{t('common.actions.close')}</button></div>
  </div>:null}
  <div hidden={Boolean(focused)} className="scene-detection-body" ref={scrollRoot} tabIndex={0} aria-label={t('sceneDetection.gallery')}>
   <div className="scene-setting-head"><small>{t(expanded||current?'sceneDetection.appliedMode':'sceneDetection.mode')} · {expanded||current?t(video.detector==='presentation-v4'?'sceneDetection.oldAuto':hasDetail?`sceneDetection.${video.video_scene_detail??'balanced'}`:video.scene_detection?'sceneDetection.custom':'sceneDetection.legacyMode'):t(mode==='auto'&&capability.detail_levels?`sceneDetection.${detail}`:mode==='auto'?'sceneDetection.auto':'sceneDetection.custom')}</small>
   {!expanded?<button className="button" onClick={()=>setExpanded(true)}>{t('sceneDetection.adjust')}</button>:null}</div>
   {expanded?<section className="scene-setup">
    {capability.modes?<SceneModeChoice value={mode} disabled={busy} onChange={setMode}/>:null}
    {mode==='auto'&&capability.detail_levels?<SceneDetailChoice value={detail} disabled={busy} onChange={setDetail}/>:null}
    {capability.range_preview?<SceneChoice caption={t('sceneDetection.scopeLabel')} label={t('sceneDetection.editRange')} value={local?'range':'full'} disabled={busy} choices={[['range',t('sceneDetection.range')],['full',t('sceneDetection.full')]]} onChange={v=>setLocal(v==='range')}/>:null}
    {local?<button className="button" disabled={busy} aria-expanded={editRange} onClick={()=>setEditRange(!editRange)}>{t('sceneDetection.editRange')} · {startText} – {endText}</button>:null}
    {local&&editRange?<div className="scene-clock-fields">
     <label>{t('sceneDetection.startClock')}<input disabled={busy} value={startText} onChange={e=>setStartText(e.target.value)}/></label>
     <label>{t('sceneDetection.endClock')}<input disabled={busy} value={endText} onChange={e=>setEndText(e.target.value)}/></label>
    </div>:null}
    {local&&!(start>=0&&start<end&&end<=video.duration)?<p role="alert">{t('sceneDetection.rangeError')}</p>:null}
    {mode==='custom'?<><SceneDetectionFields values={values} capability={capability} disabled={busy||stale} onChange={setValues}/><button className="button" disabled={busy||stale} onClick={()=>setValues(detectionDefaults(capability))}>{t('sceneDetection.reset')}</button></>:null}
   </section>:null}
   {clearsHidden?<p className="scene-detection-note">{t('sceneDetection.clearsHidden')}</p>:null}
   {stale?<p role="alert" className="error">{t('sceneDetection.conflict')}</p>:null}
   {changed?<p role="status">{t('sceneDetection.changed')}</p>:null}
   {creating||preview?.state==='running'?<div role="status" className="scene-detection-progress"><span>{t(`sceneDetection.phases.${progress?.phase||'preparing'}`)}</span>{progress&&progress.total>0?<><progress value={progress.completed} max={progress.total}/><span>{Math.min(100,Math.floor(progress.completed/progress.total*100))}%</span></>:null}</div>:null}
   {preview?.state==='failed'?<p role="alert" className="error">{t('sceneDetection.failed')}</p>:null}
   <header className="scene-gallery-head">{preview?.scenes?<SceneChoice label={t('sceneDetection.gallery')} value={current?'saved':'result'} choices={[['saved',t('sceneDetection.saved')],['result',t('sceneDetection.result')]]} onChange={v=>switchView(v==='saved')}/>:<strong>{t(current?'sceneDetection.saved':'sceneDetection.result')}</strong>}
    {current?<button className="button" disabled={busy} aria-pressed={manage} onClick={()=>{setManage(!manage);setSelected(new Set())}}>{t(manage?'sceneDetection.done':'sceneDetection.manage')}</button>:null}
   </header>
   {resultsReady?<div className="scene-detection-count" role="status">{rangeLabel} · {t('video.detectionCounts',{sections:allScenes.length,pictures:numbers.size,missing:allScenes.length-numbers.size})}
   {numbers.size<allScenes.length?<button className="button" aria-pressed={allSections} onClick={()=>setAllSections(!allSections)}>{t(allSections?'video.savedPictures':'video.allSections')}</button>:null}</div>:null}
   {manage&&current?<div className="scene-detection-actions"><button className="button" onClick={()=>setSelected(new Set(scenes.slice(0,visible).filter(s=>s.image).map(s=>s.id)))}>{t('sceneDetection.selectLoaded')}</button><button className="button" onClick={()=>setSelected(new Set())}>{t('sceneDetection.clearSelection')}</button></div>:null}
   {resultsReady&&!allSections&&!numbers.size?<p className="muted">{t('video.noPictures')}</p>:null}
   <div className="scene-detection-grid">{scenes.slice(0,visible).map((scene,index)=><figure className={selected.has(scene.id)?'selected':''} key={`${current}:${scene.id}:${scene.image}`}>
    {manage&&current?<label className="scene-detection-select"><input type="checkbox" checked={selected.has(scene.id)} disabled={busy||stale||!scene.image} onChange={()=>setSelected(previous=>{const next=new Set(previous);if(next.has(scene.id))next.delete(scene.id);else next.add(scene.id);return next})}/>{scene.image?t('sceneDetection.selectPicture',{number:numbers.get(scene.id)}):t('video.section',{number:allScenes.indexOf(scene)+1})}</label>:null}
    {scene.image?<div className="scene-thumb"><PreviewImage url={sourceUrl(scene)} label={t('video.scene',{number:numbers.get(scene.id)})}/><button aria-label={t('video.scene',{number:numbers.get(scene.id)})} onClick={e=>{zoomTrigger.current=e.currentTarget;setZoom(scene.id)}}/></div>:<div className="video-no-frame">{t('video.noStableFrame')}</div>}
    <figcaption>{scene.frame_time!==null?t('timeline.frameTime')+' '+clockText(scene.frame_time):t('timeline.sceneRange')+' '+clockText(scene.start)+' – '+clockText(scene.end)}{shownRange&&(scene.start<shownRange[0]||scene.end>shownRange[1])?<small> · {t('sceneDetection.continues')}</small>:null}{current&&hidden.has(scene.id)?<span className="scene-hidden-badge">{t('sceneDetection.hidden')}</span>:null}</figcaption>
   </figure>)}</div>
   <div className="scene-detection-sentinel" ref={sentinel} hidden={!resultsReady}><span>{t('sceneDetection.loaded',{count:Math.min(visible,scenes.length),total:scenes.length})}</span>{visible<scenes.length&&typeof IntersectionObserver==='undefined'?<button className="button" onClick={()=>setVisible(value=>value+12)}>{t('asr.results.loadMore')}</button>:null}</div>
  </div>
  <div hidden={Boolean(focused)} className="scene-detection-footer">
   {manual&&ready&&!manage?<label className="scene-detection-confirm"><input type="checkbox" checked={confirmed} onChange={event=>setConfirmed(event.target.checked)}/>{t('sceneDetection.replaceEdits')}</label>:null}
   {error?<p role="alert" className="error">{error} {preview?.state==='running'?<button className="button" disabled={blocked} onClick={()=>{setError('');setPollRetry(x=>x+1)}}>{t('common.actions.reload')}</button>:null}</p>:null}
   {notice?<p role="status">{notice}</p>:null}
   {manage&&current?<><small>{t('sceneDetection.selectionSummary',{selected:selected.size,hidden:hidden.size})}</small><div className="scene-detection-actions">
    <button className="button" disabled={busy||stale||blocked||![...selected].some(id=>!hidden.has(id))} onClick={()=>void saveVisibility('hide')}>{t('sceneDetection.hide')}</button>
    <button className="button" disabled={busy||stale||blocked||![...selected].some(id=>hidden.has(id))} onClick={()=>void saveVisibility('restore')}>{t('sceneDetection.restore')}</button>
    <button className="button" disabled={busy||stale||blocked||!hidden.size} onClick={()=>void saveVisibility('all')}>{t('sceneDetection.restoreAll')}</button>
   </div></>:null}
   {!manage&&ready?<p className="notice">{t('optimization.sceneInvalidation')}</p>:null}
   {!manage?<div className="scene-detection-actions">{preview?.state==='running'?<button className="button" onClick={()=>void cancel()} disabled={creating}>{t('sceneDetection.cancel')}</button>
    :ready?<button className="primary" disabled={busy||manual&&!confirmed||blocked} onClick={()=>void apply()}>{t(applying?'sceneDetection.applying':'sceneDetection.applyFull')}</button>
    :<button className="primary" disabled={!valid||busy||stale||blocked} onClick={()=>void generate(Boolean(local&&preview?.state==='ready'&&!changed))}>{local&&preview?.state==='ready'&&!changed?t('sceneDetection.whole'):previewLabel}</button>}</div>:null}
  </div>
 </Modal>
}
