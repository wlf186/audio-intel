import {memo,useDeferredValue,useLayoutEffect,useMemo,useRef,useState} from 'react'
import {useTranslation} from 'react-i18next'
import type {Job} from '../lib/types'
import type {OptimizationController} from '../hooks/useOptimization'
import type {OptimizationParagraph} from '../lib/optimization'
import {useReaderBookmark} from '../hooks/useReaderBookmark'
import {checkpointWorkspace,readOptimizationChanges,readReaderBookmark,saveOptimizationChanges,readSourceDetails,saveSourceDetails} from '../lib/workspaceSession'
import {transcriptDiff,type TextChange} from '../lib/transcriptDiff'
import {readingIndex,revealReadingNode} from '../lib/readerNavigation'
import {formatTime} from '../lib/api'
import {Frame} from './VideoTranscript'
import {highlightText,TranscriptSearchNavigation} from './TranscriptSearch'
import './optimization.css'

function changedText(parts:TextChange[],side:'original'|'edited',query:string){
 return parts.map((part,index)=>part.changed?side==='original'?<del key={index}>{highlightText(part.text,query)}</del>:<ins key={index}>{highlightText(part.text,query)}</ins>:<span key={index}>{highlightText(part.text,query)}</span>)
}
const Pair=memo(function Pair({paragraph,compare,showChanges,showSources,query,onSeek,onCompare}:{paragraph:OptimizationParagraph;compare:boolean;showChanges:boolean;showSources:boolean;query:string;onSeek:(time:number)=>void;onCompare:()=>void}){
 const {t}=useTranslation()
 const [detailed,setDetailed]=useState(false)
 const [selectedSource,setSelectedSource]=useState<number>()
 const diff=useMemo(()=>compare&&showChanges&&paragraph.text!==null?transcriptDiff(paragraph.original_text,paragraph.text):null,[compare,showChanges,paragraph.original_text,paragraph.text])
 const wholeRewrite=Boolean(diff&&(diff.edited.reduce((n,p)=>n+(p.changed?p.text.length:0),0)>Math.max(1,paragraph.text?.length||0)*.6||diff.original.reduce((n,p)=>n+(p.changed?p.text.length:0),0)>Math.max(1,paragraph.original_text.length)*.6))
 const showDiff=diff&&(!wholeRewrite||detailed)&&!query.trim()
 const hasReview=paragraph.review_outcome&&['fact','uncertain','unavailable'].includes(paragraph.review_outcome)
 return <article className={`optimization-paragraph${compare?' optimization-pair':''}`} data-search-owner={paragraph.id} data-reader-id={paragraph.id} data-reader-time={paragraph.start} data-reader-end={paragraph.end} data-reader-source-ids={paragraph.segment_ids.join(',')} data-reader-selected-source={selectedSource}>
  <header><strong>{paragraph.speaker_label||paragraph.speaker||t('optimization.unknownSpeaker')}</strong><button className="optimization-time" onClick={()=>onSeek(paragraph.start)}>{formatTime(paragraph.start,false)}–{formatTime(paragraph.end,false)}</button><small>{t('optimization.sourceCount',{count:paragraph.merged_source_count||paragraph.segment_ids.length})}</small>{paragraph.review_outcome==='fact'?<small className="optimization-review-badge">{t('optimization.factConcern')}</small>:null}</header>
  {wholeRewrite?<div className="optimization-rewrite-note"><span>{t('optimization.wholeRewrite')}</span><button className="button" aria-pressed={detailed} onClick={()=>setDetailed(v=>!v)}>{t('optimization.detailedChanges')}</button></div>:null}
  {compare?<div className="optimization-pair-columns"><div><small>{t('optimization.original')}</small><p>{showDiff?changedText(diff!.original,'original',query):highlightText(paragraph.original_text,query)}</p></div><div><small>{t('optimization.draft')}</small><p>{paragraph.omitted?t('optimization.omitted'):showDiff?changedText(diff!.edited,'edited',query):highlightText(paragraph.text||'',query)}</p></div></div>:<><p>{highlightText(paragraph.text||'',query)}</p><button className="button optimization-compare-paragraph" onClick={event=>{event.currentTarget.closest('article')?.scrollIntoView({block:'start'});checkpointWorkspace();onCompare()}}>{t('optimization.compareParagraph')}</button></>}
  {showSources?<div className="optimization-sources" aria-label={t('optimization.sourceDetails')}>{paragraph.sources?.map(source=><div className="optimization-source" key={`${source.segment_id}:${source.start_offset}`} data-source-id={source.segment_id} data-source-time={source.start}><button className="optimization-time" onClick={()=>{setSelectedSource(source.segment_id);onSeek(source.start)}} aria-label={t('optimization.listenSource',{time:formatTime(source.start,false)})}>{formatTime(source.start,false)}</button><p>{source.text}</p></div>)}</div>:null}
  {hasReview?<details className="optimization-review-details"><summary>{t('optimization.checkDetails')}</summary><p>{paragraph.review_outcome==='fact'?paragraph.review_details?.issues?.map(issue=>issue.reason).join('；')||t('optimization.factConcern'):t(paragraph.review_outcome==='uncertain'?'optimization.uncertainReviewHelp':'optimization.unavailableReviewHelp')}</p></details>:null}
 </article>
})

export function OptimizationReader({job,controller,query,speaker,onSeek,onModeChange,reveal,illustrated=true}:{reveal?:{sceneId?:string;time:number;token:number};illustrated?:boolean;job:Job;onModeChange:()=>void;controller:OptimizationController;query:string;speaker:string;onSeek:(time:number)=>void}){
 const {t}=useTranslation(),compare=controller.compare
 const key=`${job.id}:${compare?'comparison':'optimized'}${illustrated?'':':text'}`
 const bookmark=readReaderBookmark(key)
 const deferred=useDeferredValue(query)
 const {paragraphFilter:filter,setParagraphFilter:setFilter}=controller
 const [visible,setVisible]=useState(bookmark?.visibleCount||80)
 const [showChanges,setShowChanges]=useState(()=>readOptimizationChanges(job.id))
 const [showSources,setShowSources]=useState(()=>readSourceDetails(job.id))
 const root=useRef<HTMLDivElement>(null)
 const {result,resultError}=controller
 useReaderBookmark(key,root,{active:true,ready:Boolean(result),revision:result?.revision,visibleCount:visible,query:deferred,speakerFilter:speaker})
 const filtered=useMemo(()=>result?.paragraphs.filter(p=>(compare||!p.omitted)&&(speaker==='all'||p.speaker===speaker)&&(!compare||filter==='all'||(filter==='review'?p.review_outcome==='fact':p.text!==null&&p.text!==p.original_text))&&(!deferred.trim()||(compare?`${p.original_text} ${p.text||''}`:p.text||'').toLocaleLowerCase().includes(deferred.trim().toLocaleLowerCase())))||[],[result,speaker,deferred,compare,filter])
 const revealedToken=useRef(reveal?.token)
 useLayoutEffect(()=>{
  if(!reveal||!result||revealedToken.current===reveal.token)return
  if(deferred.trim()||speaker!=='all')return
  if(filter!=='all'){setFilter('all');return}
  const candidates=reveal.sceneId?filtered.filter(p=>p.scene_ids?.includes(reveal.sceneId!)):filtered
  const target=candidates[readingIndex(candidates,reveal.time)]
  if(target){
   const index=filtered.findIndex(p=>p.id===target.id)
   if(index>=visible){setVisible(index+1);return}
   const node=[...root.current?.querySelectorAll<HTMLElement>('[data-reader-id]')||[]].find(node=>node.dataset.readerId===target.id)
   if(node&&root.current){revealedToken.current=reveal.token;revealReadingNode(root.current,node)}
  }else{
   const node=[...root.current?.querySelectorAll<HTMLElement>('[data-scene-id]')||[]].find(node=>node.dataset.sceneId===reveal.sceneId)
   if(node&&root.current){revealedToken.current=reveal.token;revealReadingNode(root.current,node)}
  }
 },[reveal,result,deferred,speaker,filter,setFilter,filtered,visible])
 const shown=filtered.slice(0,visible)
 const byScene=useMemo(()=>{
  const map=new Map<string,OptimizationParagraph[]>()
  for(const p of shown){const id=p.scene_ids?.[0]||'';const bucket=map.get(id)||[];bucket.push(p);map.set(id,bucket)}
  return map
 },[shown])
 const pair=(p:OptimizationParagraph)=><Pair key={p.id} paragraph={p} compare={compare} showChanges={showChanges} showSources={showSources} query={deferred} onSeek={onSeek} onCompare={onModeChange}/>
 return <>
  {result?<div className="optimization-summary"><span>{t('optimization.readingCount',{count:filtered.length,total:result.paragraphs.length})}</span><div className="optimization-reading-options">
   {compare?<><label className="optimization-diff-toggle"><input type="checkbox" checked={showChanges} onChange={event=>{checkpointWorkspace();setShowChanges(event.target.checked);saveOptimizationChanges(job.id,event.target.checked)}}/>{t('optimization.showChanges')}</label><label>{t('optimization.paragraphFilter')}<select aria-label={t('optimization.paragraphFilter')} value={filter} onChange={event=>{setFilter(event.target.value);setVisible(80)}}><option value="all">{t('optimization.filterAll')}</option><option value="changed">{t('optimization.filterChanged')}</option><option value="review">{t('optimization.filterReview')}</option></select></label></>:null}
   <label className="optimization-diff-toggle"><input type="checkbox" checked={showSources} onChange={event=>{checkpointWorkspace();setShowSources(event.target.checked);saveSourceDetails(job.id,event.target.checked)}}/>{t('optimization.sourceDetails')}</label>
  </div></div>:null}
  <TranscriptSearchNavigation query={deferred} entries={filtered.map(p=>({id:p.id,texts:compare?[p.original_text,p.text||'']:[p.text||'']}))} root={root} onReveal={id=>setVisible(n=>Math.max(n,filtered.findIndex(p=>p.id===id)+1))}/>
  <div className="optimization-reader" ref={root} aria-busy={!result&&!resultError}>
   {resultError?<p className="error" role="alert">{resultError} <button className="button" onClick={controller.retry}>{t('common.actions.retry')}</button></p>:null}
   {!result?!resultError?<p role="status">{t('optimization.loading')}</p>:null:<>
    {illustrated&&result.scenes.length?result.scenes.map((scene,index)=>{
     const owned=byScene.get(scene.id)||[]
     if(!owned.length&&(deferred.trim()||speaker!=='all'||filter!=='all'))return null
     if(!owned.length&&result.paragraphs.some(p=>p.scene_ids?.[0]===scene.id&&!shown.includes(p)))return null
     return <section key={scene.id} className="optimization-scene" data-scene-id={scene.id}><h3>{t('video.scene',{number:index+1})} <small>{formatTime(scene.start,false)}–{formatTime(scene.end,false)}</small></h3><div className="optimization-scene-columns"><div className="optimization-scene-picture"><Frame jobId={job.id} scene={scene}/></div><div className="optimization-scene-prose">{owned.length?owned.map(pair):<p className="muted">{t('optimization.noSceneText')}</p>}</div></div></section>
    }):shown.map(pair)}
    {illustrated&&result.scenes.length?(byScene.get('')||[]).map(pair):null}
    {!filtered.length?<p>{t('asr.results.noMatches')}{filter!=='all'?<button className="button" onClick={()=>setFilter('all')}>{t('optimization.filterAll')}</button>:null}</p>:null}
    {visible<filtered.length?<button className="button" onClick={()=>setVisible(n=>n+80)}>{t('asr.results.loadMore')}</button>:null}
   </>}
  </div>
 </>
}
