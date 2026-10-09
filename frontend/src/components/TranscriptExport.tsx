import {useState} from 'react'
import {Download} from 'lucide-react'
import {useTranslation} from 'react-i18next'
import {artifactUrl} from '../lib/api'
import type {Job} from '../lib/types'

export function TranscriptExport({job}:{job:Job}){
 const {t}=useTranslation()
 const [busy,setBusy]=useState(false)
 const [error,setError]=useState('')
 const video=Boolean(job.result?.video)
 async function pdf(){
  setBusy(true);setError('')
  try{
   const url=`/api/v1/jobs/${job.id}/transcript.pdf`
   const response=await fetch(url,{method:'HEAD',credentials:'same-origin'})
   if(!response.ok)throw new Error(t('video.pdfFailed',{status:response.status}))
   const link=document.createElement('a');link.href=url;link.download='transcript.pdf';document.body.append(link);link.click();link.remove()
  }catch(cause){setError((cause as Error).message)}finally{setBusy(false)}
 }
 return <div className="transcript-export">
  <div className="transcript-export-actions"><button className="primary" disabled={busy} onClick={()=>void pdf()}><Download size={16}/>{busy?t('video.pdfPreparing'):t('video.pdf')}</button>
  {!video&&job.result?.artifacts?.length?<details className="transcript-export-details"><summary>{t('video.detailFormats')}</summary><div>{job.result.artifacts.map(item=><a className="button" title={t('asr.results.downloadNamed',{name:item.name})} key={item.name} href={artifactUrl(job.id,item.name)}><Download size={14}/><span>{item.name.split('.').pop()?.toUpperCase()}<small>{t(item.name.endsWith('.srt')||item.name.endsWith('.vtt')?'video.subtitleFormat':'video.detailFormat')}</small></span></a>)}</div></details>:null}</div>
  <small>{video?t('video.pdfVideoHelp'):t('video.pdfAudioHelp')}</small>
  {error?<p className="error" role="alert">{error}</p>:null}
 </div>
}
