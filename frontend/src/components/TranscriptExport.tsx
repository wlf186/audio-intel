import {useEffect,useRef,useState} from 'react'
import {Download,Settings2} from 'lucide-react'
import {useTranslation} from 'react-i18next'
import {HttpError,requestResponse} from '../lib/api'
import {loadTranscriptExportPreferences,saveTranscriptExportPreferences,parseTranscriptRules,defaultTranscriptExportPreferences,type TranscriptExportPreferences} from '../lib/transcriptExportPreferences'
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
 const [preferences,setPreferences]=useState(loadTranscriptExportPreferences)
 const updatePreferences=(next:TranscriptExportPreferences)=>{setPreferences(next);saveTranscriptExportPreferences(next);setError('')}
 const [busy,setBusy]=useState(false)
 const [error,setError]=useState('')
 const [remaining,setRemaining]=useState(0)
 const controller=useRef<AbortController|undefined>(undefined)
 const releaseDownload=useRef<(()=>void)|undefined>(undefined)
 const formats=useRef<HTMLDetailsElement>(null)
 const rules=useRef<HTMLDetailsElement>(null)
 const video=Boolean(job.result?.video)
 useEffect(()=>()=>{controller.current?.abort();releaseDownload.current?.()},[])
 useEffect(()=>{if(!remaining)return;const timer=setTimeout(()=>setRemaining(value=>Math.max(0,value-1)),1000);return()=>clearTimeout(timer)},[remaining])
 async function download(format:string){
  if(controller.current||remaining)return
  const parsed=preferences.redact?parseTranscriptRules(preferences.rules):{rules:[]}
  if(parsed.error){setError(t('transcriptExport.ruleError',{line:parsed.errorLine,reason:t(`transcriptExport.errors.${parsed.error}`)}));return}
  const active=new AbortController();controller.current=active
  setBusy(true);setError('')
  try{
   const response=await requestResponse(`/api/v1/jobs/${job.id}/transcript-export`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({format,redact:preferences.redact,replacements:parsed.rules}),signal:active.signal})
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
  <div className="transcript-export-actions"><button className="primary" title={t(video?'video.pdfVideoHelp':'video.pdfAudioHelp')} disabled={busy||remaining>0} onClick={()=>void download('pdf')}><Download size={16}/>{busy?t('video.pdfPreparing'):remaining?t('video.pdfRetryIn',{seconds:remaining}):t('video.pdf')}</button>
  <details ref={formats} onToggle={event=>{if(event.currentTarget.open&&rules.current)rules.current.open=false}} className="transcript-export-details"><summary>{t('video.detailFormats')}</summary><div>{['json','txt','srt','vtt'].map(format=><button className="button" disabled={busy||remaining>0} title={t('asr.results.downloadNamed',{name:'transcript.'+format})} key={format} onClick={()=>void download(format)}><Download size={14}/><span>{format.toUpperCase()}<small>{t(format==='srt'||format==='vtt'?'video.subtitleFormat':'video.detailFormat')}</small></span></button>)}</div></details></div>
  <div className="transcript-redaction-controls"><label className="redaction-toggle"><input type="checkbox" checked={preferences.redact} onChange={event=>updatePreferences({...preferences,redact:event.target.checked})}/>{t('transcriptExport.redact')}</label>
   <details ref={rules} onToggle={event=>{if(event.currentTarget.open&&formats.current)formats.current.open=false}}><summary role="button" aria-label={t('transcriptExport.replacements')} title={t('transcriptExport.replacements')}><Settings2 size={16}/></summary><div className="transcript-rule-editor"><p>{t('transcriptExport.help')}</p><label>{t('transcriptExport.rulesLabel')}<textarea aria-label={t('transcriptExport.rulesLabel')} rows={5} value={preferences.rules} spellCheck={false} placeholder={t('transcriptExport.example')} onChange={event=>updatePreferences({...preferences,rules:event.target.value})}/></label><button className="button" disabled={busy} onClick={()=>updatePreferences({...defaultTranscriptExportPreferences})}>{t('transcriptExport.reset')}</button></div></details>
  </div>

  {error?<p className="error" role="alert">{error}</p>:null}
 </div>
}
