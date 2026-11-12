import {useEffect,useState} from 'react'
import {useTranslation} from 'react-i18next'
import {formatTime,HttpError,requestResponse} from '../lib/api'
import {Modal} from './Modal'

/** This component mounts only after a click. The JPEG lives only in this dialog. */
function PreviewDialog({jobId,time,onClose}:{jobId:string;time:number;onClose:()=>void}){
 const {t}=useTranslation()
 const [url,setUrl]=useState(''),[error,setError]=useState(''),[retry,setRetry]=useState(0),[waiting,setWaiting]=useState(false)
 useEffect(()=>{
  const controller=new AbortController();let objectUrl='',timer:ReturnType<typeof setTimeout>|undefined
  setUrl('');setError('');setWaiting(false)
  void requestResponse(`/api/v1/jobs/${jobId}/video-preview?time=${time}`,{signal:controller.signal}).then(async response=>{
   const blob=await response.blob()
   if(controller.signal.aborted)return
   objectUrl=URL.createObjectURL(blob);setUrl(objectUrl)
  }).catch(cause=>{
   if(controller.signal.aborted)return
   setError(cause.message)
   if(cause instanceof HttpError&&cause.retryAfter){setWaiting(true);timer=setTimeout(()=>setWaiting(false),cause.retryAfter*1000)}
  })
  return()=>{controller.abort();clearTimeout(timer);if(objectUrl)URL.revokeObjectURL(objectUrl)}
 },[jobId,time,retry])
 return <Modal title={t('video.previewAt',{time:formatTime(time,false)})} closeLabel={t('common.actions.close')} className="video-frame-modal" onClose={onClose}>
  <p className="muted">{t('video.temporaryPreview')}</p>
  {error?<p role="alert">{error} <button className="button" disabled={waiting} onClick={()=>setRetry(n=>n+1)}>{t('common.actions.retry')}</button></p>:url?<img src={url} alt={t('video.previewAt',{time:formatTime(time,false)})} onError={()=>setError(t('video.frameFailed'))}/>:<p role="status">{t('common.states.loading')}</p>}
 </Modal>
}
export function VideoFramePreview({jobId,time}:{jobId:string;time:number}){
 const {t}=useTranslation(),[open,setOpen]=useState(false)
 return <><button type="button" className="button video-preview-trigger" onClick={()=>setOpen(true)}>{t('video.peekFrame')}</button>{open?<PreviewDialog jobId={jobId} time={time} onClose={()=>setOpen(false)}/>:null}</>
}
