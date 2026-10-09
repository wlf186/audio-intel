import {useEffect,useState} from 'react'
import {useTranslation} from 'react-i18next'
import {formatTime,request} from '../lib/api'
import type {Job,JobResult,VideoScene} from '../lib/types'
import {Modal} from './Modal'

type Action='merge_next'|'split'|'boundary'|'frame'|'restore'
function Frame({jobId,scene}:{jobId:string;scene:VideoScene}){
 const {t}=useTranslation();const [error,setError]=useState(false);const [retry,setRetry]=useState(0)
 useEffect(()=>setError(false),[scene.image])
 return error?<p role="alert">{t('video.frameFailed')} <button className="button" onClick={()=>{setError(false);setRetry(value=>value+1)}}>{t('common.actions.reload')}</button></p>:<img loading="lazy" src={`/api/v1/jobs/${jobId}/video-frames/${scene.image}?retry=${retry}`} alt={t('video.frameAt',{time:formatTime(scene.frame_time,false)})} onError={()=>setError(true)}/>
}
export function VideoTranscript({job,editing,onEditingChange,onUpdated,onSeek,onRename}:{job:Job;editing:boolean;onEditingChange:(value:boolean)=>void;onUpdated:(id:string,result:JobResult)=>void;onSeek:(time:number)=>void;onRename:(id:string)=>void}){
 const {t}=useTranslation()
 const [data,setData]=useState<{revision:number;scenes:VideoScene[]}>()
 const [error,setError]=useState('')
 const [reload,setReload]=useState(0)
 const [visible,setVisible]=useState(20)
 const [dialog,setDialog]=useState<{action:Action;scene?:VideoScene;index:number}>()
 const [moment,setMoment]=useState(0)
 const [preview,setPreview]=useState<number>()
 const [previewError,setPreviewError]=useState(false)
 const [saving,setSaving]=useState(false)
 const [saveError,setSaveError]=useState('')
 useEffect(()=>{let active=true;setData(undefined);setError('');void request<{revision:number;scenes:VideoScene[]}>(`/api/v1/jobs/${job.id}/video-scenes`).then(value=>{if(active)setData(value)},cause=>{if(active)setError(cause.message)});return()=>{active=false}},[job.id,job.result,reload])
 useEffect(()=>{setVisible(20);setDialog(undefined)},[job.id])
 function open(action:Action,scene?:VideoScene,index=0){setDialog({action,scene,index});setMoment(scene?(scene.start+scene.end)/2:0);setPreview(undefined);setPreviewError(false);setSaveError('')}
 async function save(){
  if(!dialog||!data)return
  setSaving(true);setSaveError('')
  try{const result=await request<JobResult>(`/api/v1/jobs/${job.id}/video-scenes`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:data.revision,action:dialog.action,scene_id:dialog.scene?.id,time:['split','boundary','frame'].includes(dialog.action)?moment:undefined})});onUpdated(job.id,result);setDialog(undefined)}catch(cause){setSaveError((cause as Error).message)}finally{setSaving(false)}
 }
 return <div className="video-transcript">
  <div className="video-reading-tools"><button className="button" onClick={()=>onEditingChange(!editing)} aria-pressed={editing}>{t('video.editSections')}</button>{editing?<button className="button" onClick={()=>open('restore')}>{t('video.actions.restore')}</button>:null}</div>
  {error?<p className="error" role="alert">{error} <button className="button" onClick={()=>setReload(value=>value+1)}>{t('common.actions.reload')}</button></p>:!data?<p role="status">{t('video.loading')}</p>:<>
   {data.scenes.slice(0,visible).map((scene,index)=><section className="video-section" key={scene.id}>
    <header><h3>{t('video.scene',{number:index+1})}</h3><button className="video-scene-time" onClick={()=>onSeek(scene.start)}>{formatTime(scene.start,false)} – {formatTime(scene.end,false)}</button><div className="video-scene-actions"><button className="button" onClick={()=>open('frame',scene,index)}>{t('video.actions.frame')}</button>{index+1<data.scenes.length?<button className="button" onClick={()=>{open('boundary',scene,index);setMoment(scene.end)}}>{t('video.actions.boundary')}</button>:null}</div></header>
    <Frame jobId={job.id} scene={scene}/>
    {scene.paragraphs?.length?scene.paragraphs.map((paragraph,i)=><div className="video-paragraph" key={i}><div><button className="video-speaker" onClick={()=>onRename(paragraph.speaker)}>{paragraph.speaker_label}</button><button className="video-paragraph-time" onClick={()=>onSeek(paragraph.start)}>{formatTime(paragraph.start,false)} – {formatTime(paragraph.end,false)}</button></div><p>{paragraph.text}</p></div>):<p className="video-silent">{t('video.noSpeech')}</p>}
    {editing?<div className="video-scene-actions"><button className="button" onClick={()=>open('split',scene,index)}>{t('video.actions.split')}</button>{index+1<data.scenes.length?<button className="button" onClick={()=>open('merge_next',scene,index)}>{t('video.actions.merge_next')}</button>:null}</div>:null}
   </section>)}
   {visible<data.scenes.length?<button className="button" onClick={()=>setVisible(value=>value+20)}>{t('asr.results.loadMore')}</button>:null}
  </>}
  {dialog?<Modal title={t(`video.actions.${dialog.action}`)} closeLabel={t('common.actions.close')} onClose={()=>{if(!saving)setDialog(undefined)}}>
   <p>{t(`video.editHelp.${dialog.action}`)}</p>
   {['frame','boundary','split'].includes(dialog.action)?<><label>{t('video.timeSeconds')}<input type="number" min={dialog.scene?.start||0} max={dialog.action==='boundary'?data?.scenes[dialog.index+1]?.end:dialog.scene?.end} step="0.1" value={moment} onChange={event=>setMoment(Number(event.target.value))}/></label><button className="button" onClick={()=>{setPreview(moment);setPreviewError(false)}}>{t('video.previewFrame')}</button>{preview!==undefined?(previewError?<p className="error" role="alert">{t('video.frameFailed')}</p>:<img className="video-edit-preview" src={`/api/v1/jobs/${job.id}/video-preview?time=${preview}`} alt={t('video.frameAt',{time:formatTime(preview,false)})} onError={()=>setPreviewError(true)}/>):null}</>:null}
   {saveError?<p className="error" role="alert">{saveError} <button className="button" onClick={()=>{setDialog(undefined);setReload(value=>value+1)}}>{t('common.actions.reload')}</button></p>:null}
   <div className="modal-actions"><button className="button" disabled={saving} onClick={()=>setDialog(undefined)}>{t('common.actions.cancel')}</button><button className="primary" disabled={saving||!data} onClick={()=>void save()}>{saving?t('common.states.processing'):t('common.actions.save')}</button></div>
  </Modal>:null}
 </div>
}
