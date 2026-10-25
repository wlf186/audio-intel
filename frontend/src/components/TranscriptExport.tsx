import {useEffect,useRef,useState} from 'react'
import {Download,Settings2} from 'lucide-react'
import {useTranslation} from 'react-i18next'
import {HttpError,requestResponse} from '../lib/api'
import {loadTranscriptExportPreferences,saveTranscriptExportPreferences,parseTranscriptRules,type TranscriptExportPreferences} from '../lib/transcriptExportPreferences'
import type {Job,TranscriptExportFormat} from '../lib/types'
import {Modal} from './Modal'
import {ActionPopover} from './ActionPopover'
import {useCompactWorkspace} from '../hooks/useCompactWorkspace'
import {transcriptDownloadName,transcriptFilename} from '../lib/transcriptFilename'

export function TranscriptExport({job,formats=['pdf','json','txt','srt','vtt'],optimizationId,optimizedView=false}:{job:Job;formats?:TranscriptExportFormat[];optimizationId?:string;optimizedView?:boolean}){
 const {t}=useTranslation()
 const [textVersion,setTextVersion]=useState<'original'|'optimized'|'comparison'>('original')
 useEffect(()=>{setTextVersion(optimizedView&&optimizationId?'comparison':'original');setFormat('pdf')},[optimizedView,optimizationId])
 const availableFormats=formats.filter(value=>textVersion==='original'||value==='pdf'||value==='docx'||textVersion==='optimized'&&value==='txt')
 const [preferences,setPreferences]=useState(loadTranscriptExportPreferences)
 const updatePreferences=(next:TranscriptExportPreferences)=>{setPreferences(next);saveTranscriptExportPreferences(next);setError('')}
 const [undoRules,setUndoRules]=useState<string>()
 const [busy,setBusy]=useState(false)
 const [error,setError]=useState('')
 const [remaining,setRemaining]=useState(0)
 const controller=useRef<AbortController|undefined>(undefined)
 const releaseDownload=useRef<(()=>void)|undefined>(undefined)
 const compact=useCompactWorkspace()
 const [open,setOpen]=useState(false)
 const [format,setFormat]=useState<TranscriptExportFormat>('pdf')
 const parsedRules=parseTranscriptRules(preferences.rules)
 const ruleError=parsedRules.error?t('transcriptExport.ruleError',{line:parsedRules.errorLine,reason:t(`transcriptExport.errors.${parsedRules.error}`)}):''
 const blocked=busy||remaining>0||Boolean(preferences.redact&&ruleError)
 const video=Boolean(job.result?.video)
 useEffect(()=>()=>{controller.current?.abort();releaseDownload.current?.()},[])
 useEffect(()=>{if(!remaining)return;const timer=setTimeout(()=>setRemaining(value=>Math.max(0,value-1)),1000);return()=>clearTimeout(timer)},[remaining])
 async function download(format:TranscriptExportFormat){
  if(controller.current||remaining||!availableFormats.includes(format))return
  const parsed=preferences.redact?parseTranscriptRules(preferences.rules):{rules:[]}
  if(parsed.error){setError(t('transcriptExport.ruleError',{line:parsed.errorLine,reason:t(`transcriptExport.errors.${parsed.error}`)}));return}
  const active=new AbortController();controller.current=active
  setBusy(true);setError('')
  try{
   const response=await requestResponse(`/api/v1/jobs/${job.id}/transcript-export`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({format,text_version:textVersion,...(textVersion!=='original'?{optimization_id:optimizationId}:{}),redact:preferences.redact,replacements:parsed.rules}),signal:active.signal})
   const blob=await response.blob()
   if(active.signal.aborted)return
   releaseDownload.current?.()
   const url=URL.createObjectURL(blob)
   const timer=setTimeout(()=>URL.revokeObjectURL(url),60_000)
   releaseDownload.current=()=>{clearTimeout(timer);URL.revokeObjectURL(url)}
   const link=document.createElement('a');link.href=url;link.download=transcriptDownloadName(response,transcriptFilename(job.display_name,job.id,format));document.body.append(link);link.click();link.remove()
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
 const redaction=<label className="redaction-toggle"><input type="checkbox" checked={preferences.redact} onChange={event=>updatePreferences({...preferences,redact:event.target.checked})}/>{t('transcriptExport.redact')}</label>
 const editor=<div className="transcript-rule-editor"><p>{t('transcriptExport.help')}</p><label>{t('transcriptExport.rulesLabel')}<textarea aria-label={t('transcriptExport.rulesLabel')} aria-invalid={Boolean(ruleError)} aria-describedby={ruleError?'transcript-rule-error':undefined} rows={5} value={preferences.rules} spellCheck={false} placeholder={t('transcriptExport.example')} onChange={event=>{setUndoRules(undefined);updatePreferences({...preferences,rules:event.target.value})}}/></label>{ruleError?<p id="transcript-rule-error" className="error" role="alert">{ruleError}</p>:null}<div className="rule-clear-actions"><button className="button" disabled={busy||!preferences.rules} onClick={()=>{setUndoRules(preferences.rules);updatePreferences({...preferences,rules:''})}}>{t('transcriptExport.clearRules')}</button>{undoRules!==undefined?<span role="status">{t('transcriptExport.cleared')} <button className="button" disabled={busy} onClick={()=>{updatePreferences({...preferences,rules:undoRules});setUndoRules(undefined)}}>{t('transcriptExport.undo')}</button></span>:null}</div></div>
 const label=busy?t('video.pdfPreparing'):remaining?t('video.pdfRetryIn',{seconds:remaining}):t(format==='pdf'?'video.pdf':'common.actions.download')
 const versionPicker=optimizationId?<label className="optimization-export-version">{t('optimization.exportVersion')}<select aria-label={t('optimization.exportVersion')} value={textVersion} disabled={busy} onChange={event=>{setTextVersion(event.target.value as typeof textVersion);setFormat('pdf')}}><option value="original">{t('optimization.original')}</option><option value="optimized">{t('optimization.draft')}</option><option value="comparison">{t('optimization.comparisonExport')}</option></select></label>:null
 return <div className="transcript-export">
  {!compact?versionPicker:null}
  {compact?<button className="primary export-mobile-trigger" onClick={()=>setOpen(true)}><Download size={16}/>{t('transcriptExport.open')}</button>:<>
   <div className="transcript-export-actions"><ActionPopover label={busy?t('video.pdfPreparing'):remaining?t('video.pdfRetryIn',{seconds:remaining}):t('transcriptExport.concise')} className="transcript-concise" icon={<Download size={16}/>} primary disabled={blocked} closeOnAction>{(['pdf','docx'] as const).filter(value=>availableFormats.includes(value)).map(value=><button className="button" data-format={value} key={value} title={t(video?'video.pdfVideoHelp':'video.pdfAudioHelp')} disabled={blocked} onClick={()=>void download(value)}><Download size={14}/><span>{value==='docx'?'Word (DOCX)':'PDF'}<small>{t(value==='docx'?'transcriptExport.editable':'transcriptExport.fixedLayout')}</small></span></button>)}</ActionPopover>
    {availableFormats.some(value=>!['pdf','docx'].includes(value))?<ActionPopover label={t('video.detailFormats')} className="transcript-export-details" closeOnAction>{(['json','txt','srt','vtt'] as const).filter(value=>availableFormats.includes(value)).map(value=><button className="button" title={t('asr.results.downloadNamed',{name:transcriptFilename(job.display_name,job.id,value)})} disabled={blocked} key={value} onClick={()=>void download(value)}><Download size={14}/><span>{value.toUpperCase()}<small>{t(value==='srt'||value==='vtt'?'video.subtitleFormat':'video.detailFormat')}</small></span></button>)}</ActionPopover>:null}
   </div><div className="transcript-redaction-controls">{redaction}<ActionPopover label={t('transcriptExport.replacements')} className="transcript-rules" icon={<Settings2 size={16}/>}>{editor}</ActionPopover></div>
  </>}
  {error&&(!compact||!open)?<p className="error" role="alert">{error}</p>:null}
  {!compact&&preferences.redact&&ruleError?<p className="error" role="note">{ruleError}</p>:null}
  {compact&&open?<Modal className="transcript-export-modal" title={t('transcriptExport.open')} closeLabel={t('common.actions.close')} onClose={()=>setOpen(false)}>
   <div className="modal-scroll-body">{versionPicker}<label>{t('transcriptExport.format')}<select value={format} onChange={event=>setFormat(event.target.value as TranscriptExportFormat)}>{(['pdf','docx','txt','srt','vtt','json'] as const).filter(value=>availableFormats.includes(value)).map(value=><option key={value} value={value}>{value==='docx'?'Word (DOCX)':value.toUpperCase()}</option>)}</select></label>{redaction}{editor}{error?<p className="error" role="alert">{error}</p>:null}</div>
   <div className="modal-actions"><button className="button" onClick={()=>setOpen(false)}>{t('common.actions.close')}</button><button className="primary" disabled={blocked} onClick={()=>void download(format)}><Download size={16}/>{label}</button></div>
  </Modal>:null}
 </div>
}
