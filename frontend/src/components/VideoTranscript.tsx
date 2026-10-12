import {useEffect,useRef,useState} from 'react'
import {createPortal} from 'react-dom'
import {Fingerprint} from 'lucide-react'
import {useTranslation} from 'react-i18next'
import {formatTime,HttpError,request} from '../lib/api'
import type {Job,JobResult,VideoScene} from '../lib/types'
import {Modal} from './Modal'
import {readReaderBookmark} from '../lib/workspaceSession'
import {useReaderBookmark} from '../hooks/useReaderBookmark'

type Action='merge_next'|'split'|'boundary'|'frame'|'restore'
function Frame({jobId,scene}:{jobId:string;scene:VideoScene}){
 const {t}=useTranslation();const [error,setError]=useState(false);const [retry,setRetry]=useState(0);const [expanded,setExpanded]=useState(false)
 useEffect(()=>setError(false),[scene.image])
 const url=`/api/v1/jobs/${jobId}/video-frames/${scene.image}?retry=${retry}`
 const label=t('video.frameAt',{time:formatTime(scene.frame_time,false)})
 return error?<p role="alert">{t('video.frameFailed')} <button className="button" onClick={()=>{setError(false);setRetry(value=>value+1)}}>{t('common.actions.reload')}</button></p>:<><button type="button" className="video-frame" aria-label={t('workspaceSplit.enlargeFrame')} title={t('workspaceSplit.enlargeFrame')} onClick={()=>setExpanded(true)}><img loading="lazy" src={url} alt={label} onError={()=>setError(true)}/></button>{expanded?<Modal title={label} closeLabel={t('common.actions.close')} className="video-frame-modal" onClose={()=>setExpanded(false)}><img src={url} alt={label} onError={()=>{setExpanded(false);setError(true)}}/></Modal>:null}</>
}
export function VideoTranscript({job,toolbar,editing,onEditingChange,onUpdated,onSeek,onRename,onEnroll,currentTime=0}:{job:Job;toolbar?:HTMLElement|null;editing:boolean;onEditingChange:(value:boolean)=>void;onUpdated:(id:string,result:JobResult)=>void;onSeek:(time:number)=>void;onRename:(id:string)=>void;onEnroll:(id:string,segmentIds:number[])=>void;currentTime?:number}){
 const {t}=useTranslation()
 const [data,setData]=useState<{revision:number;scenes:VideoScene[]}>()
 const [error,setError]=useState('')
 const [reload,setReload]=useState(0)
 const [visible,setVisible]=useState(()=>readReaderBookmark(`${job.id}:video`)?.visibleCount||20)
 const [refreshing,setRefreshing]=useState(true)
 const scrollRoot=useRef<HTMLDivElement>(null)
 const sentinel=useRef<HTMLDivElement>(null)
 const [dialog,setDialog]=useState<{action:Action;scene?:VideoScene;upper?:number;revision:number}>()
 const [conflict,setConflict]=useState(false)
 const saveController=useRef<AbortController|undefined>(undefined)
 const [momentInput,setMomentInput]=useState('0')
 const [clockInput,setClockInput]=useState('00:00:00.000')
 const moment=Number(momentInput)
 function setMoment(value:number){setMomentInput(String(value));setClockInput(formatTime(value))}
 const [preview,setPreview]=useState<number>()
 const [previewError,setPreviewError]=useState(false)
 const [saving,setSaving]=useState(false)
 const [saveError,setSaveError]=useState('')
 const {prepareRefresh}=useReaderBookmark(`${job.id}:video`,scrollRoot,{active:true,ready:Boolean(data)&&!refreshing,visibleCount:visible})
 useEffect(()=>{
  const controller=new AbortController();prepareRefresh();setRefreshing(true);setError('')
  void request<{revision:number;scenes:VideoScene[]}>(`/api/v1/jobs/${job.id}/video-scenes`,{signal:controller.signal}).then(value=>{if(!controller.signal.aborted){setData(value);setRefreshing(false)}},cause=>{if(!controller.signal.aborted){setError(cause.message);setRefreshing(false)}})
  return()=>controller.abort()
 },[job.id,job.result,reload,prepareRefresh])
 useEffect(()=>{setDialog(undefined);setSaving(false);return()=>saveController.current?.abort()},[job.id])
 useEffect(()=>{
  const root=scrollRoot.current,target=sentinel.current
  if(!root||!target||!data||refreshing||visible>=data.scenes.length)return
  let active=true
  const observer=new IntersectionObserver(entries=>{if(active&&entries.some(entry=>entry.isIntersecting))setVisible(value=>Math.min(data.scenes.length,value+20))},{root,rootMargin:'0px 0px 360px'})
  observer.observe(target)
  return()=>{active=false;observer.disconnect()}
 },[data,visible,refreshing])
 const stale=conflict||Boolean(dialog&&data&&dialog.revision!==data.revision)
 const timed=Boolean(dialog&&['split','boundary','frame'].includes(dialog.action))
 const validTime=!timed||Boolean(momentInput.trim()&&Number.isFinite(moment)&&dialog?.scene&&(dialog.action==='frame'?moment>=dialog.scene.start:moment>dialog.scene.start)&&moment<(dialog.upper??dialog.scene.end))
 function open(action:Action,scene?:VideoScene,index=0){if(!data||refreshing||error)return;setDialog({action,scene,upper:action==='boundary'?data.scenes[index+1]?.end:scene?.end,revision:data.revision});setConflict(false);setMoment(scene?(scene.start+scene.end)/2:0);setPreview(undefined);setPreviewError(false);setSaveError('')}
 async function save(){
  if(!dialog||!data||refreshing||error||stale||saving||!validTime)return
  const controller=new AbortController();saveController.current=controller
  setSaving(true);setSaveError('')
  try{const result=await request<JobResult>(`/api/v1/jobs/${job.id}/video-scenes`,{method:'PATCH',signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:dialog.revision,action:dialog.action,scene_id:dialog.scene?.id,time:['split','boundary','frame'].includes(dialog.action)?moment:undefined})});if(!controller.signal.aborted){onUpdated(job.id,result);setDialog(undefined)}}catch(cause){if(!controller.signal.aborted){if(cause instanceof HttpError&&cause.status===409)setConflict(true);else setSaveError((cause as Error).message)}}finally{if(!controller.signal.aborted)setSaving(false)}
 }
 const tools=<div className="video-reading-tools"><button className="button" onClick={()=>onEditingChange(!editing)} aria-pressed={editing}>{t(editing?'video.finishEditing':'video.editSections')}</button>{editing?<button className="button" disabled={!data||refreshing||Boolean(error)} onClick={()=>open('restore')}>{t('video.actions.restore')}</button>:null}</div>
 return <>{toolbar?createPortal(tools,toolbar):tools}<div className="video-transcript" ref={scrollRoot} aria-busy={refreshing}>
  {error?<p className="error" role="alert">{error} <button className="button" onClick={()=>setReload(value=>value+1)}>{t('common.actions.reload')}</button></p>:refreshing?<p className="reader-refresh-status" role="status">{t('video.loading')}</p>:null}
  {data?<>
   {data.scenes.slice(0,visible).map((scene,index)=><section className="video-section" key={scene.id} data-reader-id={scene.id} data-reader-time={scene.start}>
    <header><h3>{t('video.scene',{number:index+1})}</h3><button className="video-scene-time" onClick={()=>onSeek(scene.start)}>{formatTime(scene.start,false)} – {formatTime(scene.end,false)}</button>{editing?<div className="video-scene-actions"><button className="button" disabled={refreshing||Boolean(error)} onClick={()=>open('frame',scene,index)}>{t('video.actions.frame')}</button>{index+1<data.scenes.length?<button className="button" disabled={refreshing||Boolean(error)} onClick={()=>{open('boundary',scene,index);setMoment(scene.end)}}>{t('video.actions.boundary')}</button>:null}</div>:null}</header>
    <div className="video-scene-content"><Frame jobId={job.id} scene={scene}/><div className="video-scene-text">
    {scene.paragraphs?.length?scene.paragraphs.map((paragraph,i)=><div className="video-paragraph" key={i}><div><button className="video-speaker" onClick={()=>onRename(paragraph.speaker)}>{paragraph.speaker_label}</button><button className="video-paragraph-time" onClick={()=>onSeek(paragraph.start)}>{formatTime(paragraph.start,false)} – {formatTime(paragraph.end,false)}</button><button className="icon-button video-enroll" title={t('asr.results.addToVoiceprints')} aria-label={t('asr.results.addToVoiceprints')} onClick={()=>onEnroll(paragraph.speaker,paragraph.segment_ids)}><Fingerprint size={17}/></button></div><p>{paragraph.text}</p></div>):<p className="video-silent">{t('video.noSpeech')}</p>}
    </div></div>
    {editing?<div className="video-scene-actions"><button className="button" disabled={refreshing||Boolean(error)} onClick={()=>open('split',scene,index)}>{t('video.actions.split')}</button>{index+1<data.scenes.length?<button className="button" disabled={refreshing||Boolean(error)} onClick={()=>open('merge_next',scene,index)}>{t('video.actions.merge_next')}</button>:null}</div>:null}
   </section>)}
   <div className="segment-load-more" ref={sentinel}><span>{t('video.sceneCount',{visible:Math.min(visible,data.scenes.length),total:data.scenes.length})}</span>{visible<data.scenes.length?<button className="button" onClick={()=>setVisible(value=>Math.min(data.scenes.length,value+20))}>{t('asr.results.loadMore')}</button>:null}</div>
  </>:null}
  {dialog?<Modal title={t(`video.actions.${dialog.action}`)} closeLabel={t('common.actions.close')} onClose={()=>{if(!saving)setDialog(undefined)}}>
   <p>{t(`video.editHelp.${dialog.action}`)}</p>
   {timed?<><p className="time-range-help">{t('workspaceUx.timeRange',{start:formatTime(dialog.scene?.start||0),end:formatTime(dialog.upper||0),left:t(dialog.action==='frame'?'workspaceUx.inclusive':'workspaceUx.exclusive')})}</p><div className="two-cols"><label>{t('video.timeSeconds')}<input type="number" min={dialog.scene?.start||0} max={dialog.upper} step="0.1" value={momentInput} aria-invalid={!validTime} onChange={event=>{setMomentInput(event.target.value);if(event.target.value.trim()&&Number.isFinite(Number(event.target.value)))setClockInput(formatTime(Number(event.target.value)))}}/></label><label>{t('workspaceUx.timeClock')}<input value={clockInput} placeholder="00:00:00.000" aria-invalid={!validTime} onChange={event=>{const raw=event.target.value;setClockInput(raw);const match=/^(\d+):([0-5]\d):([0-5]\d(?:\.\d{1,3})?)$/.exec(raw);setMomentInput(match?String(Number(match[1])*3600+Number(match[2])*60+Number(match[3])):'')}}/></label></div>{!validTime?<p className="error" role="alert">{t('workspaceUx.invalidTime')}</p>:null}<div className="time-edit-actions"><button className="button" disabled={!Number.isFinite(moment)||!momentInput.trim()} onClick={()=>setMoment(Math.round((moment-.1)*10)/10)}>−0.1 {t('workspaceUx.seconds')}</button><button className="button" disabled={!Number.isFinite(moment)||!momentInput.trim()} onClick={()=>setMoment(Math.round((moment+.1)*10)/10)}>+0.1 {t('workspaceUx.seconds')}</button><button className="button" onClick={()=>setMoment(currentTime)}>{t('workspaceUx.currentTime')}</button><button className="button" disabled={!validTime} onClick={()=>{setPreview(moment);setPreviewError(false)}}>{t('video.previewFrame')}</button></div>{preview!==undefined?(previewError?<p className="error" role="alert">{t('video.frameFailed')}</p>:<img className="video-edit-preview" src={`/api/v1/jobs/${job.id}/video-preview?time=${preview}`} alt={t('video.frameAt',{time:formatTime(preview,false)})} onError={()=>setPreviewError(true)}/>):null}</>:null}
   {stale?<p className="error" role="alert">{t('video.editConflict')} <button className="button" onClick={()=>{setDialog(undefined);setConflict(false);setReload(value=>value+1)}}>{t('video.discardEditReload')}</button></p>:saveError?<p className="error" role="alert">{saveError}</p>:null}
   <div className="modal-actions"><button className="button" disabled={saving} onClick={()=>setDialog(undefined)}>{t('common.actions.cancel')}</button><button className="primary" disabled={saving||!data||refreshing||Boolean(error)||stale||!validTime} onClick={()=>void save()}>{saving?t('common.states.processing'):t('common.actions.save')}</button></div>
  </Modal>:null}
 </div></>
}
