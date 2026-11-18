import {useEffect,useRef,useState} from 'react'
import {useTranslation} from 'react-i18next'
import type {Artifact,DocumentResult} from '../lib/types'
import {artifactUrl} from '../lib/api'
import {AudioTransport} from './AudioTransport'
import './document.css'
export function DocumentResults({jobId,result,artifacts,active=true}:{jobId:string;result:DocumentResult;artifacts?:Artifact[];active?:boolean}){
 const {t}=useTranslation()
 const [index,setIndex]=useState(0)
 const [error,setError]=useState('')
 const [requested,setRequested]=useState(false)
 const frames=useRef(new Map<string,HTMLIFrameElement>())
 useEffect(()=>()=>{for(const frame of frames.current.values())frame.remove();frames.current.clear()},[jobId])
 const download=(mode:string,sectionId?:string)=>{
  setError('');setRequested(true)
  const params=new URLSearchParams({mode,...(sectionId?{section_id:sectionId}:{})})
  let frame=frames.current.get(mode)
  if(!frame){frame=document.createElement('iframe');frame.hidden=true;frame.title=t('document.downloadWindow');document.body.appendChild(frame);frames.current.set(mode,frame)}
  const target=frame
  target.onload=()=>{
   try{
    const document=target.contentDocument
    if(!document)throw new Error('Download navigation failed')
    if(document.URL==='about:blank'||!document.body?.textContent)return
    const problem=JSON.parse(document.body.textContent) as {status?:number;detail?:string;retry_after_seconds?:number}
    if(problem.status===401)window.dispatchEvent(new Event('audio-intel:unauthorized'))
    const detail=problem.detail||t('document.downloadError')
    setError(problem.retry_after_seconds?t('errors.retryAfter',{detail,seconds:problem.retry_after_seconds}):detail)
    setRequested(false)
   }catch{setError(t('document.downloadError'));setRequested(false)}
  }
  target.onerror=()=>{setError(t('document.downloadError'));setRequested(false)}
  target.src=`/api/v1/jobs/${jobId}/document/download?${params}`
 }
 const item=result.sections[index]||result.sections[0]
 const totalBytes=artifacts?.reduce((sum,a)=>sum+Math.ceil(a.size_bytes/3)*4,0)
 const estimate=totalBytes?` · ≈ ${(totalBytes/1e6).toFixed(1)} MB`:''
 return <section className="document-results">
  <label>{t('document.selectAudio')}<select value={index} onChange={e=>setIndex(Number(e.target.value))}>{result.sections.map((s,i)=><option key={s.id} value={i}>{s.index}. {s.title}</option>)}</select></label>
  {item?<><AudioTransport active={active} key={`${jobId}:${item.id}`} src={artifactUrl(jobId,item.artifact_name)} duration={item.duration}/><a className="button" href={artifactUrl(jobId,item.artifact_name)}>{t('tts.results.download',{format:'MP3'})} · {item.title}</a></>:null}
  <div className="document-downloads"><a className="button primary" href={`/api/v1/jobs/${jobId}/document/download?mode=reader`} target="_blank" rel="noreferrer">{t('document.openBook')}</a>
   <details><summary>{t('document.bookDownloads')}</summary>
    <a className="button" onClick={e=>{e.preventDefault();download('html_complete')}} href={`/api/v1/jobs/${jobId}/document/download?mode=html_complete`}>{t('document.fullHtml')}{estimate}</a>
    <a className="button" onClick={e=>{e.preventDefault();download('html_sections')}} href={`/api/v1/jobs/${jobId}/document/download?mode=html_sections`}>{t('document.sectionHtmlZip',{count:result.sections.length})}{estimate}</a>
    {item&&<a className="button" onClick={e=>{e.preventDefault();download('html_section',item.id)}} href={`/api/v1/jobs/${jobId}/document/download?mode=html_section&section_id=${encodeURIComponent(item.id)}`}>{t('document.oneHtml')}</a>}
   </details></div>
  <div className="document-downloads"><a className="button primary" href={`/api/v1/jobs/${jobId}/document/download?mode=sections`} onClick={event=>{event.preventDefault();download('sections')}}>{t('document.zip')}</a><a className="button" href={`/api/v1/jobs/${jobId}/document/download?mode=complete`} onClick={event=>{event.preventDefault();download('complete')}}>{t('document.complete')}</a></div>
  {error?<p role="alert">{error}</p>:requested?<p role="status">{t('document.downloadRequested')}</p>:null}
  <details><summary>{t('document.downloadHelp')}</summary><p>{t('document.streamNote')}</p></details>
 </section>
}
