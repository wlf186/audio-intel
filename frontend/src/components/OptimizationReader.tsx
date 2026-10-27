import {memo,useDeferredValue,useMemo,useRef,useState} from 'react'
import {useTranslation} from 'react-i18next'
import type {Job} from '../lib/types'
import type {OptimizationController} from '../hooks/useOptimization'
import type {OptimizationParagraph} from '../lib/optimization'
import {useReaderBookmark} from '../hooks/useReaderBookmark'
import {readReaderBookmark} from '../lib/workspaceSession'
import {transcriptDiff} from '../lib/transcriptDiff'
import {formatTime} from '../lib/api'
import {Frame} from './VideoTranscript'
import './optimization.css'

const Pair=memo(function Pair({paragraph,compare,onSeek}:{paragraph:OptimizationParagraph;compare:boolean;onSeek:(time:number)=>void}){
 const {t}=useTranslation()
 const diff=useMemo(()=>compare&&paragraph.text!==null?transcriptDiff(paragraph.original_text,paragraph.text):null,[compare,paragraph.original_text,paragraph.text])
 return <article className={`optimization-paragraph${compare?' optimization-pair':''}`} data-reader-id={paragraph.id} data-reader-time={paragraph.start}>
  <header><strong>{paragraph.speaker_label||paragraph.speaker}</strong><button className="optimization-time" onClick={()=>onSeek(paragraph.start)}>{formatTime(paragraph.start,false)}</button>{paragraph.review_status==='needs_review'?<small className="optimization-review-badge">{t('optimization.needsReviewBadge')}</small>:null}</header>
  {compare?<div className="optimization-pair-columns"><div><small>{t('optimization.original')}</small><p>{diff?diff.original.map((part,i)=>part.changed?<del key={i}>{part.text}</del>:part.text):paragraph.original_text}</p></div><div><small>{t('optimization.draft')}</small>{paragraph.text===null?<p className="muted">{t('optimization.pendingParagraph')}</p>:paragraph.omitted||paragraph.text===''?<p className="optimization-omitted">{t('optimization.omitted')}</p>:<p>{diff?diff.edited.map((part,i)=>part.changed?<ins key={i}>{part.text}</ins>:part.text):paragraph.text}</p>}</div></div>:<p>{paragraph.text??t('optimization.pendingParagraph')}</p>}
 </article>
})
export function OptimizationReader({job,controller,query,speaker,onSeek,onModeChange}:{job:Job;onModeChange:()=>void;controller:OptimizationController;query:string;speaker:string;onSeek:(time:number)=>void}){
 const {t}=useTranslation(),compare=controller.compare
 const key=`${job.id}:${compare?'comparison':'optimized'}`
 const bookmark=readReaderBookmark(key)
 const deferred=useDeferredValue(query)
 const {paragraphFilter:filter,setParagraphFilter:setFilter}=controller
 const [visible,setVisible]=useState(bookmark?.visibleCount||80)
 const root=useRef<HTMLDivElement>(null)
 const {result,resultError}=controller
 useReaderBookmark(key,root,{active:true,ready:Boolean(result)&&!resultError,visibleCount:visible,query:deferred,speakerFilter:speaker})
 const stats=useMemo(()=>{
  const values=result?.paragraphs||[]
  return{changed:values.filter(p=>p.text!==null&&p.text!==''&&p.text!==p.original_text).length,omitted:values.filter(p=>p.text==='').length,unchanged:values.filter(p=>p.text!==null&&p.text===p.original_text&&p.review_status!=='needs_review').length,needsReview:values.filter(p=>p.review_status==='needs_review').length}
 },[result])
 const filtered=useMemo(()=>result?.paragraphs.filter(p=>(compare||p.text!=='')&&(speaker==='all'||p.speaker===speaker)&&(!compare||filter==='all'||(filter==='review'?p.review_status==='needs_review':p.text!==null&&p.text!==p.original_text))&&(!deferred.trim()||`${p.text||''} ${compare?p.original_text:''}`.toLocaleLowerCase().includes(deferred.trim().toLocaleLowerCase())))||[],[result,speaker,deferred,compare,filter])
 const shown=filtered.slice(0,visible),ids=new Set(shown.map(p=>p.id))
 return <>
  {result?<div className="optimization-summary">
   <span><span className="optimization-mode-caption">{t('optimization.currentMode',{mode:t(controller.selected?.rewrite_mode?`optimization.rewriteModes.${controller.selected.rewrite_mode}.label`:'optimization.legacyMode')})}</span>{result.complete&&stats.needsReview>0?t(stats.needsReview===result.paragraphs.length?'optimization.allRetained':'optimization.completedWithReview',{count:stats.needsReview}):compare?t('optimization.changeSummary',stats):null}</span>
   {compare?<label>{t('optimization.paragraphFilter')}<select aria-label={t('optimization.paragraphFilter')} value={filter} onChange={e=>{setFilter(e.target.value);setVisible(80);root.current?.scrollTo(0,0)}}>
    <option value="all">{t('optimization.filterAll')}</option><option value="changed">{t('optimization.filterChanged')}</option><option value="review">{t('optimization.filterReview')}</option>
   </select></label>:stats.needsReview>0?<button className="button" onClick={()=>{onModeChange();setFilter('review');setVisible(80)}}>{t('optimization.viewNeedsReview',{count:stats.needsReview})}</button>:null}
  </div>:null}
  <div className="optimization-reader" ref={root} aria-busy={!result&&!resultError}>
   {resultError?<p className="error" role="alert">{resultError} <button className="button" onClick={controller.retry}>{t('common.actions.retry')}</button></p>:!result?<p role="status">{t('optimization.loading')}</p>:<>
    {!result.complete?<p className="notice" role="status">{t('optimization.partial')}</p>:null}
    {result.scenes.length?result.scenes.map((scene,index)=>{
     const values=scene.paragraphs.filter(p=>ids.has(p.id))
     const emptyScene=!compare&&scene.paragraphs.every(p=>p.text==='')&&!deferred.trim()&&speaker==='all'&&(scene.paragraphs.length===0||scene.paragraphs[0].ordinal<=(shown.at(-1)?.ordinal??Infinity))
     if(!values.length&&!emptyScene)return null
     return <section key={scene.id} className="optimization-scene"><h3>{t('video.scene',{number:index+1})}</h3><Frame jobId={job.id} scene={scene}/>{emptyScene?<p className="muted">{t('optimization.noSceneText')}</p>:values.map(p=><Pair key={p.id} paragraph={p} compare={compare} onSeek={onSeek}/>)}</section>
    }):shown.map(p=><Pair key={p.id} paragraph={p} compare={compare} onSeek={onSeek}/>)}
    {!filtered.length?<p>{t(!compare&&result.paragraphs.length>0&&result.paragraphs.every(p=>p.text==='')?'optimization.allOmitted':'asr.results.noMatches')}</p>:null}
    {visible<filtered.length?<button className="button" onClick={()=>setVisible(n=>n+80)}>{t('asr.results.loadMore')}</button>:null}
   </>}
  </div>
 </>
}
