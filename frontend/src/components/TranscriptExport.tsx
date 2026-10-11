import {useEffect,useRef,useState} from 'react'
import {Download} from 'lucide-react'
import {useTranslation} from 'react-i18next'
import {artifactUrl,HttpError,requestResponse} from '../lib/api'
import type {Job} from '../lib/types'

function downloadName(response:Response){
 const disposition=response.headers.get('Content-Disposition')||''
 const encoded=/filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1]
 let name=/filename="([^"]+)"/i.exec(disposition)?.[1]||'transcript.pdf'
 if(encoded){try{name=decodeURIComponent(encoded)}catch{/* Use the plain fallback for malformed headers. */}}
 return name.split(/[/\\]/).pop()?.replace(/[\x00-\x1f]/g,'')||'transcript.pdf'
}

export function TranscriptExport({job}:{job:Job}){
 const {t}=useTranslation()
 const [busy,setBusy]=useState(false)
 const [error,setError]=useState('')
 const [remaining,setRemaining]=useState(0)
 const controller=useRef<AbortController|undefined>(undefined)
 const releaseDownload=useRef<(()=>void)|undefined>(undefined)
 const video=Boolean(job.result?.video)
 useEffect(()=>()=>{controller.current?.abort();releaseDownload.current?.()},[])
 useEffect(()=>{if(!remaining)return;const timer=setTimeout(()=>setRemaining(value=>Math.max(0,value-1)),1000);return()=>clearTimeout(timer)},[remaining])
 async function pdf(){
  if(controller.current||remaining)return
  const active=new AbortController();controller.current=active
  setBusy(true);setError('')
  try{
   const response=await requestResponse(`/api/v1/jobs/${job.id}/transcript.pdf`,{signal:active.signal})
   const blob=await response.blob()
   if(active.signal.aborted)return
   releaseDownload.current?.()
   const url=URL.createObjectURL(blob)
   const timer=setTimeout(()=>URL.revokeObjectURL(url),60_000)
   releaseDownload.current=()=>{clearTimeout(timer);URL.revokeObjectURL(url)}
   const link=document.createElement('a');link.href=url;link.download=downloadName(response);document.body.append(link);link.click();link.remove()
  }catch(cause){
   if(active.signal.aborted)return
   if(cause instanceof HttpError){
    if(cause.status===429){setRemaining(Math.ceil(cause.retryAfter||0));setError(t('video.pdfBusy'))}
    else if(cause.status===413)setError(t('video.pdfTooLarge'))
    else if(cause.status===401)setError(t('video.pdfLogin'))
    else setError(t('video.pdfFailed',{status:cause.status}))
   }else setError(t('video.pdfNetwork'))
  }finally{if(!active.signal.aborted){controller.current=undefined;setBusy(false)}}
 }
 return <div className="transcript-export">
  <div className="transcript-export-actions"><button className="primary" title={t(video?'video.pdfVideoHelp':'video.pdfAudioHelp')} disabled={busy||remaining>0} onClick={()=>void pdf()}><Download size={16}/>{busy?t('video.pdfPreparing'):remaining?t('video.pdfRetryIn',{seconds:remaining}):t('video.pdf')}</button>
  {!video&&job.result?.artifacts?.length?<details className="transcript-export-details"><summary>{t('video.detailFormats')}</summary><div>{job.result.artifacts.map(item=><a className="button" title={t('asr.results.downloadNamed',{name:item.name})} key={item.name} href={artifactUrl(job.id,item.name)}><Download size={14}/><span>{item.name.split('.').pop()?.toUpperCase()}<small>{t(item.name.endsWith('.srt')||item.name.endsWith('.vtt')?'video.subtitleFormat':'video.detailFormat')}</small></span></a>)}</div></details>:null}</div>
  {error?<p className="error" role="alert">{error}</p>:null}
 </div>
}
