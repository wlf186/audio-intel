import {memo,useDeferredValue,useLayoutEffect,useMemo,useRef,useState} from 'react'
import {useTranslation} from 'react-i18next'
import type {Job} from '../lib/types'
import type {OptimizationController} from '../hooks/useOptimization'
import type {OptimizationParagraph,OptimizationSource} from '../lib/optimization'
import {useReaderBookmark} from '../hooks/useReaderBookmark'
import {checkpointWorkspace,readOptimizationChanges,readReaderBookmark,saveOptimizationChanges} from '../lib/workspaceSession'
import {transcriptDiff,type TextChange} from '../lib/transcriptDiff'
import {formatTime} from '../lib/api'
import {Frame} from './VideoTranscript'
import './optimization.css'

function sourceRows(paragraph:OptimizationParagraph):OptimizationSource[]{
 return paragraph.sources?.length?paragraph.sources:[{segment_id:paragraph.segment_ids[0],text:paragraph.original_text,start:paragraph.start,end:paragraph.end,start_offset:0,end_offset:paragraph.original_text.length}]
}
function hiddenSourceMatches(original:string,sources:OptimizationSource[],needle:string){
 if(!needle||sources.length<=3)return false
 let cursor=0
 for(const [index,source] of sources.entries()){
  const start=original.indexOf(source.text,cursor)
  if(start<0)return false
  if(index===3)return original.toLocaleLowerCase().indexOf(needle,Math.max(0,start-needle.length+1))>=0
  cursor=start+source.text.length
 }
 return false
}
function changedText(parts:TextChange[],side:'original'|'edited'){
 return parts.map((part,index)=>part.changed?side==='original'?<del key={index}>{part.text}</del>:<ins key={index}>{part.text}</ins>:part.text)
}
// Diff the complete group, then project the source half back onto its original rows.
function sourceChanges(original:string,parts:TextChange[],sources:OptimizationSource[]){
 const spans: {start:number;end:number;changed:boolean}[]=[]
 let offset=0
 for(const part of parts){spans.push({start:offset,end:offset+part.text.length,changed:part.changed});offset+=part.text.length}
 let cursor=0
 return sources.map(source=>{
  const start=original.indexOf(source.text,cursor)
  if(start<0)return[{text:source.text,changed:false}]
  const end=start+source.text.length;cursor=end
  return spans.filter(span=>span.end>start&&span.start<end).map(span=>({text:original.slice(Math.max(start,span.start),Math.min(end,span.end)),changed:span.changed}))
 })
}
const Pair=memo(function Pair({paragraph,compare,showChanges,query,revealSourceId,revealTime,onSeek,sceneLinks,onScene}:{paragraph:OptimizationParagraph;compare:boolean;showChanges:boolean;query:string;revealSourceId?:number;revealTime?:number;onSeek:(time:number)=>void;sceneLinks:{id:string;number:number}[];onScene:(id:string)=>void}){
 const {t}=useTranslation()
 const sources=useMemo(()=>sourceRows(paragraph),[paragraph])
 const [expanded,setExpanded]=useState(()=>sources.slice(3).some(s=>s.segment_id===revealSourceId||revealTime!==undefined&&s.start<=revealTime&&s.end>revealTime))
 const [sourceOpen,setSourceOpen]=useState(false)
 const [selectedSource,setSelectedSource]=useState<number>()
 const needle=query.trim().toLocaleLowerCase()
 const queryExpands=hiddenSourceMatches(paragraph.original_text,sources,needle)
 const allSources=expanded||queryExpands
 const visibleSources=allSources?sources:sources.slice(0,3)
 const diff=useMemo(()=>compare&&showChanges&&paragraph.text!==null?transcriptDiff(paragraph.original_text,paragraph.text):null,[compare,showChanges,paragraph.original_text,paragraph.text])
 const sourceDiff=useMemo(()=>diff?sourceChanges(paragraph.original_text,diff.original,sources):null,[diff,paragraph.original_text,sources])
 const legacyReview=paragraph.review_status==='needs_review'&&!paragraph.review_outcome
 const fact=paragraph.review_outcome==='fact'
 const uncertain=paragraph.review_outcome==='uncertain'
 const unavailable=paragraph.review_outcome==='unavailable'
 const hasReview=legacyReview||fact||uncertain||unavailable
 const merged=paragraph.merged_source_count??new Set(paragraph.segment_ids).size
 const sourceList=<div className="optimization-sources">
  {visibleSources.map((source,index)=><div className="optimization-source" key={`${source.segment_id}:${source.start_offset}`} data-source-id={source.segment_id} data-source-time={source.start}>
   <button className="optimization-time" onClick={()=>{setSelectedSource(source.segment_id);onSeek(source.start)}} aria-label={t('optimization.listenSource',{time:formatTime(source.start,false)})}>{formatTime(source.start,false)}</button>
   <p>{sourceDiff?changedText(sourceDiff[index],'original'):source.text}</p>
  </div>)}
  {sources.length>3?<button className="button optimization-source-expand" disabled={queryExpands} aria-expanded={allSources} onClick={()=>setExpanded(value=>!value)}>{t(queryExpands?'optimization.matchExpanded':allSources?'optimization.collapseSources':'optimization.expandSources',{count:sources.length})}</button>:null}
 </div>
 return <article className={`optimization-paragraph${compare?' optimization-pair':''}`} data-reader-id={paragraph.id} data-reader-time={paragraph.start} data-reader-end={paragraph.end} data-reader-source-ids={paragraph.segment_ids.join(',')} data-reader-selected-source={selectedSource}>
  <header><strong>{paragraph.speaker_label||paragraph.speaker||t('optimization.unknownSpeaker')}</strong><button className="optimization-time" onClick={()=>{setSelectedSource(undefined);onSeek(paragraph.start)}}>{formatTime(paragraph.start,false)}–{formatTime(paragraph.end,false)}</button>{merged>1?<small className="optimization-merged">{t('optimization.mergedSources',{count:merged})}</small>:null}{legacyReview?<small className="optimization-review-badge">{t(paragraph.candidate_text?'optimization.candidateNeedsReview':'optimization.needsReviewBadge')}</small>:fact?<small className="optimization-review-badge">{t('optimization.factConcern')}</small>:null}{paragraph.generation_method==='basic_cleanup'||paragraph.generation_method==='mixed'?<small className="muted">{t('optimization.basicCleanup')}</small>:null}</header>
  {sceneLinks.length>1?<div className="optimization-scene-links" aria-label={t('optimization.relatedPictures')}>{sceneLinks.map(scene=><button className="button" key={scene.id} onClick={()=>onScene(scene.id)}>{t('video.scene',{number:scene.number})}</button>)}</div>:null}
  {compare?<div className="optimization-pair-columns"><div><small>{t('optimization.original')}</small>{sourceList}</div><div><small>{t('optimization.draft')}</small>{paragraph.text===null?<p className="muted">{t('optimization.pendingParagraph')}</p>:paragraph.omitted||paragraph.text===''?<p className="optimization-omitted">{t('optimization.omitted')}</p>:<p>{diff?changedText(diff.edited,'edited'):paragraph.text}</p>}</div></div>:<><p>{paragraph.text??t('optimization.pendingParagraph')}</p><button className="button optimization-source-toggle" aria-expanded={sourceOpen} onClick={()=>setSourceOpen(value=>!value)}>{t(sourceOpen?'optimization.hideSources':'optimization.viewSources',{count:sources.length})}</button>{sourceOpen?sourceList:null}</>}
  {hasReview?<details className="optimization-review-details"><summary>{t(uncertain?'optimization.uncertainReview':unavailable?'optimization.unavailableReview':'optimization.reviewDetails')}</summary><p>{paragraph.review_details?.issues?.map(issue=>issue.reason).join('；')||t(uncertain?'optimization.uncertainReviewHelp':unavailable?'optimization.unavailableReviewHelp':paragraph.review_reason==='invalid_output'?'optimization.invalidReview':'optimization.failedReview')}</p>{paragraph.review_details?.issues?.map((issue,index)=><blockquote key={index}><div>{t('optimization.original')}: {issue.source_quote}</div><div>{t('optimization.draft')}: {issue.candidate_quote}</div></blockquote>)}</details>:null}
 </article>
})

export function OptimizationReader({job,controller,query,speaker,onSeek,onModeChange,illustrated=true}:{illustrated?:boolean;job:Job;onModeChange:()=>void;controller:OptimizationController;query:string;speaker:string;onSeek:(time:number)=>void}){
 const {t}=useTranslation(),compare=controller.compare
 const key=`${job.id}:${compare?'comparison':'optimized'}${illustrated?'':':text'}`
 const bookmark=readReaderBookmark(key)
 const deferred=useDeferredValue(query)
 const {paragraphFilter:filter,setParagraphFilter:setFilter}=controller
 const [visible,setVisible]=useState(bookmark?.visibleCount||80)
 const [showChanges,setShowChanges]=useState(()=>readOptimizationChanges(job.id))
 const [navigation,setNavigation]=useState<string>()
 const root=useRef<HTMLDivElement>(null)
 const {result,resultError}=controller
 useReaderBookmark(key,root,{active:true,ready:Boolean(result),revision:result?.revision,visibleCount:visible,query:deferred,speakerFilter:speaker})
 useLayoutEffect(()=>{
  if(!navigation)return
  const target=[...root.current?.querySelectorAll<HTMLElement>('[data-group-target],[data-scene-id]')||[]].find(node=>node.dataset.groupTarget===navigation||node.dataset.sceneId===navigation)
  if(target&&root.current){
   if(root.current.scrollHeight>root.current.clientHeight)root.current.scrollTop+=target.getBoundingClientRect().top-root.current.getBoundingClientRect().top
   else target.scrollIntoView({block:'start'})
   setNavigation(undefined)
  }
 },[navigation,visible])
 const stats=useMemo(()=>{
  const values=result?.paragraphs||[]
  return{retained:values.filter(p=>p.review_status==='needs_review'&&!p.review_outcome&&!p.candidate_text).length,candidates:values.filter(p=>p.review_status==='needs_review'&&Boolean(p.candidate_text)).length,changed:values.filter(p=>p.text!==null&&p.text!==''&&p.text!==p.original_text).length,omitted:values.filter(p=>p.text==='').length,unchanged:values.filter(p=>p.text!==null&&p.text===p.original_text&&p.review_status!=='needs_review').length,needsReview:values.filter(p=>p.review_status==='needs_review').length}
 },[result])
 const filtered=useMemo(()=>result?.paragraphs.filter(p=>(compare||p.text!=='')&&(speaker==='all'||p.speaker===speaker)&&(!compare||filter==='all'||(filter==='review'?p.review_status==='needs_review':p.text!==null&&p.text!==p.original_text))&&(!deferred.trim()||`${p.text||''} ${p.original_text}`.toLocaleLowerCase().includes(deferred.trim().toLocaleLowerCase())))||[],[result,speaker,deferred,compare,filter])
 const shown=filtered.slice(0,visible)
 const sceneNumbers=useMemo(()=>new Map(result?.scenes.map((scene,index)=>[scene.id,index+1])||[]),[result?.scenes])
 const sceneIds=useMemo(()=>{
  const values=new Map<string,string[]>()
  for(const paragraph of result?.paragraphs||[]){
   const ids=(result?.prompt_version||1)>=6?paragraph.scene_ids?.filter(id=>sceneNumbers.has(id))||[]:[]
   values.set(paragraph.id,ids.length?ids:(result?.scenes.filter(scene=>scene.paragraphs.some(p=>p.id===paragraph.id)).map(scene=>scene.id)||[]))
  }
  return values
 },[result,sceneNumbers])
 const navigate=(target:string,paragraphId?:string)=>{
  const index=paragraphId?filtered.findIndex(p=>p.id===paragraphId):-1
  if(index>=visible)setVisible(index+80)
  setNavigation(target)
 }
 const pair=(p:OptimizationParagraph)=><div key={p.id} data-group-target={p.id}><Pair paragraph={p} compare={compare} showChanges={showChanges} query={deferred} revealSourceId={bookmark?.sourceId} revealTime={bookmark?.time} onSeek={onSeek} sceneLinks={(illustrated?sceneIds.get(p.id)||[]:[]).map(id=>({id,number:sceneNumbers.get(id)!}))} onScene={id=>navigate(id)}/></div>
 return <>
  {result?<div className="optimization-summary">
   <span><span className="optimization-mode-caption">{t('optimization.currentMode',{mode:t(controller.selected?.rewrite_mode?`optimization.rewriteModes.${controller.selected.rewrite_mode}.label`:'optimization.legacyMode')})}</span>{result.review_state?(compare?t('optimization.changeSummary',stats):null):result.complete&&stats.needsReview>0?t(stats.retained===result.paragraphs.length?'optimization.allRetained':'optimization.completedWithReview',{count:stats.needsReview}):compare?t('optimization.changeSummary',stats):null}{stats.candidates>0?<small> · {t('optimization.candidateSummary',{count:stats.candidates,retained:stats.retained})}</small>:null}</span>
   <div className="optimization-reading-options">{compare?<><label className="optimization-diff-toggle"><input type="checkbox" checked={showChanges} onChange={event=>{checkpointWorkspace();setShowChanges(event.target.checked);saveOptimizationChanges(job.id,event.target.checked)}}/>{t('optimization.showChanges')}</label><label>{t('optimization.paragraphFilter')}<select aria-label={t('optimization.paragraphFilter')} value={filter} onChange={event=>{setFilter(event.target.value);setVisible(80);root.current?.scrollTo(0,0)}}><option value="all">{t('optimization.filterAll')}</option><option value="changed">{t('optimization.filterChanged')}</option><option value="review">{t('optimization.filterReview')}</option></select></label></>:stats.needsReview>0?<button className="button" onClick={()=>{onModeChange();setFilter('review');setVisible(80)}}>{t('optimization.viewNeedsReview',{count:stats.needsReview})}</button>:null}</div>
  </div>:null}
  <div className="optimization-reader" ref={root} aria-busy={!result&&!resultError}>
   {resultError?<p className="error" role="alert">{resultError} <button className="button" onClick={controller.retry}>{t('common.actions.retry')}</button></p>:null}
   {!result?!resultError?<p role="status">{t('optimization.loading')}</p>:null:<>
    {!result.complete?<p className="notice" role="status">{t('optimization.partial')}</p>:null}
    {illustrated&&result.scenes.length?result.scenes.map((scene,index)=>{
     const owned=shown.filter(p=>sceneIds.get(p.id)?.[0]===scene.id)
     const linked=shown.filter(p=>sceneIds.get(p.id)?.[0]!==scene.id&&sceneIds.get(p.id)?.includes(scene.id))
     const originals=result.paragraphs.filter(p=>sceneIds.get(p.id)?.[0]===scene.id)
     const emptyScene=!compare&&!linked.length&&!deferred.trim()&&speaker==='all'&&originals.every(p=>p.text==='')&&(originals.length===0||originals[0].ordinal<=(shown.at(-1)?.ordinal??Infinity))
     if(!owned.length&&!linked.length&&!emptyScene)return null
     return <section key={scene.id} className="optimization-scene" data-scene-id={scene.id}><h3>{t('video.scene',{number:index+1})}</h3><Frame jobId={job.id} scene={scene}/>{emptyScene?<p className="muted">{t('optimization.noSceneText')}</p>:null}{owned.map(pair)}{linked.map(p=><button key={p.id} className="button optimization-source-link" onClick={()=>navigate(p.id,p.id)}>{t('optimization.sharedPictureText',{speaker:p.speaker_label||p.speaker||t('optimization.unknownSpeaker'),time:formatTime(p.start,false)})}</button>)}</section>
    }):shown.map(pair)}
    {illustrated&&result.scenes.length?shown.filter(p=>!sceneIds.get(p.id)?.length).map(pair):null}
    {!filtered.length?<p>{t(!compare&&result.paragraphs.length>0&&result.paragraphs.every(p=>p.text==='')?'optimization.allOmitted':'asr.results.noMatches')}</p>:null}
    {visible<filtered.length?<button className="button" onClick={()=>setVisible(n=>n+80)}>{t('asr.results.loadMore')}</button>:null}
   </>}
  </div>
 </>
}
