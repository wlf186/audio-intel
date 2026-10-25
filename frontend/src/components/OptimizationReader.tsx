import {useDeferredValue,useMemo,useRef,useState} from 'react'
import {useTranslation} from 'react-i18next'
import type {Job} from '../lib/types'
import type {OptimizationController} from '../hooks/useOptimization'
import type {OptimizationParagraph} from '../lib/optimization'
import {useReaderBookmark} from '../hooks/useReaderBookmark'
import {readReaderBookmark} from '../lib/workspaceSession'
import {Modal} from './Modal'
import {Frame} from './VideoTranscript'
import './optimization.css'

function Pair({paragraph,compare}:{paragraph:OptimizationParagraph;compare:boolean}){
 const {t}=useTranslation()
 return <article className={`optimization-paragraph${compare?' optimization-pair':''}`} data-reader-id={paragraph.id} data-reader-time={paragraph.start}>
  <strong>{paragraph.speaker_label||paragraph.speaker}</strong>
  {compare?<div className="optimization-pair-columns"><div><small>{t('optimization.original')}</small><p>{paragraph.original_text}</p></div><div><small>{t('optimization.draft')}</small><p>{paragraph.text??t('optimization.pendingParagraph')}</p></div></div>:<p>{paragraph.text??t('optimization.pendingParagraph')}</p>}
 </article>
}
export function OptimizationReader({job,controller,compare=false}:{job:Job;controller:OptimizationController;compare?:boolean}){
 const {t}=useTranslation()
 const bookmark=readReaderBookmark(`${job.id}:optimized`)
 const [query,setQuery]=useState(compare?'':bookmark?.query||'')
 const deferred=useDeferredValue(query)
 const [speaker,setSpeaker]=useState(compare?'all':bookmark?.speakerFilter||'all')
 const [visible,setVisible]=useState(compare?80:bookmark?.visibleCount||80)
 const root=useRef<HTMLDivElement>(null)
 const {result,resultError}=controller
 useReaderBookmark(compare?'':`${job.id}:optimized`,root,{active:!compare,ready:Boolean(result)&&!resultError,visibleCount:visible,query:deferred,speakerFilter:speaker})
 const filtered=useMemo(()=>result?.paragraphs.filter(p=>(speaker==='all'||p.speaker===speaker)&&(!deferred.trim()||`${p.text||''} ${compare?p.original_text:''}`.toLocaleLowerCase().includes(deferred.trim().toLocaleLowerCase())))||[],[result,speaker,deferred,compare])
 const shown=filtered.slice(0,visible)
 const ids=new Set(shown.map(p=>p.id))
 return <>
  <div className="optimization-search"><input aria-label={t('optimization.search')} placeholder={t('optimization.search')} value={query} onChange={e=>{setQuery(e.target.value);setVisible(80)}}/><select aria-label={t('asr.results.filterSpeaker')} value={speaker} onChange={e=>{setSpeaker(e.target.value);setVisible(80)}}><option value="all">{t('asr.results.allSpeakers')}</option>{job.result?.speakers?.map(p=><option key={p.id} value={p.id}>{p.label}</option>)}</select></div>
  <div className="optimization-reader" ref={root} aria-busy={!result&&!resultError}>
   {resultError?<p className="error" role="alert">{resultError} <button className="button" onClick={controller.retry}>{t('common.actions.retry')}</button></p>:!result?<p role="status">{t('optimization.loading')}</p>:<>
    {!result.complete?<p className="notice" role="status">{t('optimization.partial')}</p>:null}
    {result.scenes.length?result.scenes.map((scene,index)=>{
     const values=scene.paragraphs.filter(p=>ids.has(p.id))
     if(!values.length)return null
     return <section key={scene.id} className="optimization-scene"><h3>{t('video.scene',{number:index+1})}</h3><Frame jobId={job.id} scene={scene}/>{values.map(p=><Pair key={p.id} paragraph={p} compare={compare}/>)}</section>
    }):shown.map(p=><Pair key={p.id} paragraph={p} compare={compare}/>)}
    {!filtered.length?<p>{t('asr.results.noMatches')}</p>:null}
    {visible<filtered.length?<button className="button" onClick={()=>setVisible(n=>n+80)}>{t('asr.results.loadMore')}</button>:null}
   </>}
  </div>
 </>
}
export function OptimizationComparison({job,controller}:{job:Job;controller:OptimizationController}){
 const {t}=useTranslation()
 return <Modal title={t('optimization.compareTitle')} closeLabel={t('common.actions.close')} className="optimization-comparison" onClose={()=>controller.setCompare(false)}><OptimizationReader job={job} controller={controller} compare/></Modal>
}
