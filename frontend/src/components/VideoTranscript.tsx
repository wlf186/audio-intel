import {useEffect,useRef,useState} from 'react'
import {useTranslation} from 'react-i18next'
import {formatTime,HttpError,request} from '../lib/api'
import type {Job,JobResult,VideoScene} from '../lib/types'
import {Modal} from './Modal'

type Action='merge_next'|'split'|'boundary'|'frame'|'restore'
function Frame({jobId,scene}:{jobId:string;scene:VideoScene}){
 const {t}=useTranslation();const [error,setError]=useState(false);const [retry,setRetry]=useState(0)
 useEffect(()=>setError(false),[scene.image])
 return error?<p role="alert">{t('video.frameFailed')} <button className="button" onClick={()=>{setError(false);setRetry(value=>value+1)}}>{t('common.actions.reload')}</button></p>:<img loading="lazy" src={`/api/v1/jobs/${jobId}/video-frames/${scene.image}?retry=${retry}`} alt={t('video.frameAt',{time:formatTime(scene.frame_time,false)})} onError={()=>setError(true)}/>
}
export function VideoTranscript({job,editing,onEditingChange,onUpdated,onSeek,onRename,onEnroll}:{job:Job;editing:boolean;onEditingChange:(value:boolean)=>void;onUpdated:(id:string,result:JobResult)=>void;onSeek:(time:number)=>void;onRename:(id:string)=>void;onEnroll:(id:string)=>void}){
 const {t}=useTranslation()
 const [data,setData]=useState<{revision:number;scenes:VideoScene[]}>()
 const [error,setError]=useState('')
 const [reload,setReload]=useState(0)
 const [visible,setVisible]=useState(20)
 const scrollRoot=useRef<HTMLDivElement>(null)
 const sentinel=useRef<HTMLDivElement>(null)
 const [dialog,setDialog]=useState<{action:Action;scene?:VideoScene;upper?:number;revision:number}>()
 const [conflict,setConflict]=useState(false)
 const saveController=useRef<AbortController|undefined>(undefined)
 const [moment,setMoment]=useState(0)
 const [preview,setPreview]=useState<number>()
 const [previewError,setPreviewError]=useState(false)
 const [saving,setSaving]=useState(false)
 const [saveError,setSaveError]=useState('')
 useEffect(()=>{let active=true;setData(undefined);setError('');void request<{revision:number;scenes:VideoScene[]}>(`/api/v1/jobs/${job.id}/video-scenes`).then(value=>{if(active)setData(value)},cause=>{if(active)setError(cause.message)});return()=>{active=false}},[job.id,job.result,reload])
 useEffect(()=>{setVisible(20);setDialog(undefined);setSaving(false);return()=>saveController.current?.abort()},[job.id])
 useEffect(()=>{
  const root=scrollRoot.current,target=sentinel.current
  if(!root||!target||!data||visible>=data.scenes.length)return
  let active=true
  const observer=new IntersectionObserver(entries=>{if(active&&entries.some(entry=>entry.isIntersecting))setVisible(value=>Math.min(data.scenes.length,value+20))},{root,rootMargin:'0px 0px 360px'})
  observer.observe(target)
  return()=>{active=false;observer.disconnect()}
 },[data,visible])
 const stale=conflict||Boolean(dialog&&data&&dialog.revision!==data.revision)
 function open(action:Action,scene?:VideoScene,index=0){if(!data)return;setDialog({action,scene,upper:action==='boundary'?data.scenes[index+1]?.end:scene?.end,revision:data.revision});setConflict(false);setMoment(scene?(scene.start+scene.end)/2:0);setPreview(undefined);setPreviewError(false);setSaveError('')}
 async function save(){
  if(!dialog||!data||stale||saving)return
  const controller=new AbortController();saveController.current=controller
  setSaving(true);setSaveError('')
  try{const result=await request<JobResult>(`/api/v1/jobs/${job.id}/video-scenes`,{method:'PATCH',signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:dialog.revision,action:dialog.action,scene_id:dialog.scene?.id,time:['split','boundary','frame'].includes(dialog.action)?moment:undefined})});if(!controller.signal.aborted){onUpdated(job.id,result);setDialog(undefined)}}catch(cause){if(!controller.signal.aborted){if(cause instanceof HttpError&&cause.status===409)setConflict(true);else setSaveError((cause as Error).message)}}finally{if(!controller.signal.aborted)setSaving(false)}
 }
 return <div className="video-transcript" ref={scrollRoot}>
  <div className="video-reading-tools"><button className="button" onClick={()=>onEditingChange(!editing)} aria-pressed={editing}>{t(editing?'video.finishEditing':'video.editSections')}</button>{editing?<button className="button" disabled={!data} onClick={()=>open('restore')}>{t('video.actions.restore')}</button>:null}</div>
  {error?<p className="error" role="alert">{error} <button className="button" onClick={()=>setReload(value=>value+1)}>{t('common.actions.reload')}</button></p>:!data?<p role="status">{t('video.loading')}</p>:<>
   {data.scenes.slice(0,visible).map((scene,index)=><section className="video-section" key={scene.id}>
    <header><h3>{t('video.scene',{number:index+1})}</h3><button className="video-scene-time" onClick={()=>onSeek(scene.start)}>{formatTime(scene.start,false)} – {formatTime(scene.end,false)}</button>{editing?<div className="video-scene-actions"><button className="button" onClick={()=>open('frame',scene,index)}>{t('video.actions.frame')}</button>{index+1<data.scenes.length?<button className="button" onClick={()=>{open('boundary',scene,index);setMoment(scene.end)}}>{t('video.actions.boundary')}</button>:null}</div>:null}</header>
    <Frame jobId={job.id} scene={scene}/>
    {scene.paragraphs?.length?scene.paragraphs.map((paragraph,i)=><div className="video-paragraph" key={i}><div><button className="video-speaker" onClick={()=>onRename(paragraph.speaker)}>{paragraph.speaker_label}</button><button className="video-paragraph-time" onClick={()=>onSeek(paragraph.start)}>{formatTime(paragraph.start,false)} – {formatTime(paragraph.end,false)}</button><button className="button" onClick={()=>onEnroll(paragraph.speaker)}>{t('asr.results.addToVoiceprints')}</button></div><p>{paragraph.text}</p></div>):<p className="video-silent">{t('video.noSpeech')}</p>}
    {editing?<div className="video-scene-actions"><button className="button" onClick={()=>open('split',scene,index)}>{t('video.actions.split')}</button>{index+1<data.scenes.length?<button className="button" onClick={()=>open('merge_next',scene,index)}>{t('video.actions.merge_next')}</button>:null}</div>:null}
   </section>)}
   <div className="segment-load-more" ref={sentinel}><span>{t('video.sceneCount',{visible:Math.min(visible,data.scenes.length),total:data.scenes.length})}</span>{visible<data.scenes.length?<button className="button" onClick={()=>setVisible(value=>Math.min(data.scenes.length,value+20))}>{t('asr.results.loadMore')}</button>:null}</div>
  </>}
  {dialog?<Modal title={t(`video.actions.${dialog.action}`)} closeLabel={t('common.actions.close')} onClose={()=>{if(!saving)setDialog(undefined)}}>
   <p>{t(`video.editHelp.${dialog.action}`)}</p>
   {['frame','boundary','split'].includes(dialog.action)?<><label>{t('video.timeSeconds')}<input type="number" min={dialog.scene?.start||0} max={dialog.upper} step="0.1" value={moment} onChange={event=>setMoment(Number(event.target.value))}/></label><button className="button" onClick={()=>{setPreview(moment);setPreviewError(false)}}>{t('video.previewFrame')}</button>{preview!==undefined?(previewError?<p className="error" role="alert">{t('video.frameFailed')}</p>:<img className="video-edit-preview" src={`/api/v1/jobs/${job.id}/video-preview?time=${preview}`} alt={t('video.frameAt',{time:formatTime(preview,false)})} onError={()=>setPreviewError(true)}/>):null}</>:null}
   {stale?<p className="error" role="alert">{t('video.editConflict')} <button className="button" onClick={()=>{setDialog(undefined);setConflict(false);setReload(value=>value+1)}}>{t('video.discardEditReload')}</button></p>:saveError?<p className="error" role="alert">{saveError}</p>:null}
   <div className="modal-actions"><button className="button" disabled={saving} onClick={()=>setDialog(undefined)}>{t('common.actions.cancel')}</button><button className="primary" disabled={saving||!data||stale} onClick={()=>void save()}>{saving?t('common.states.processing'):t('common.actions.save')}</button></div>
  </Modal>:null}
 </div>
}
