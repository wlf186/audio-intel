import {useLayoutEffect,useMemo,useRef,useState,type ReactNode} from 'react'
import {useTranslation} from 'react-i18next'
import type {Job,JobResult,Segment} from '../lib/types'
import {readReaderBookmark} from '../lib/workspaceSession'
import {formatTime} from '../lib/api'
import {useReaderBookmark} from '../hooks/useReaderBookmark'
import {revealReadingNode,readingIndex} from '../lib/readerNavigation'
import {useReadingWorkspace,TopicHeading,type OutlineItem} from './ReadingWorkspace'
import {Frame} from './VideoTranscript'
import {highlightText,TranscriptSearchNavigation} from './TranscriptSearch'

function AssociatedFrames({job,block}:{job:Job;block:OutlineItem}){
 const {t}=useTranslation(),[index,setIndex]=useState(0)
 const scenes=frameIndex(job.result)
 const frames=(block.scene_ids||[]).flatMap(id=>{const scene=scenes.get(id);return scene?.image?[scene]:[]})
 if(!frames.length)return null
 const active=frames[Math.min(index,frames.length-1)]
 return <div className="reading-associated-frames"><div className="reading-frame-tools"><small>{t('reading.associatedFrames')}</small>{frames.length>1?<select aria-label={t('reading.associatedFrames')} value={index} onChange={e=>setIndex(Number(e.target.value))}>{frames.map((scene,i)=><option key={scene.id} value={i}>{t('video.scene',{number:block.frame_numbers?.[i]})} · {formatTime(scene.frame_time??scene.start,false)}</option>)}</select>:<small>{block.frame_numbers?.[0]}</small>}</div><Frame key={active.id} jobId={job.id} scene={active}/></div>
}
export function ReadingBlocks({job,query,speaker,illustrated=true,mode,render,texts,reveal,include}:{job:Job;query:string;speaker:string;illustrated?:boolean;mode:string;render:(block:OutlineItem)=>ReactNode;texts:(block:OutlineItem)=>string[];reveal?:{time:number;token:number};include?:(block:OutlineItem)=>boolean}){
 const {outline,runId,grouped,target}=useReadingWorkspace(),{t}=useTranslation(),root=useRef<HTMLDivElement>(null)
 const key=`${job.id}:${mode}:${runId||''}:${grouped?'merged':'ordered'}:${illustrated?'images':'text'}`
 const rows=useMemo(()=>{const items=new Map(outline?.items.map(b=>[b.id,b]));return (outline?.groups||[]).flatMap(group=>group.primary_item_ids.flatMap(id=>{const item=items.get(id);return item?[{item,group}]:[]}))},[outline])
 const needle=query.trim().toLocaleLowerCase()
 const filtered=rows.filter(({item})=>(!include||include(item))&&(speaker==='all'||item.source_refs?.some(ref=>segmentIndex(job.result).get(ref.segment_id)?.speaker===speaker))&&(!needle||[item.title,...texts(item)].some(text=>text.toLocaleLowerCase().includes(needle))))
 const [visible,setVisible]=useState(()=>readReaderBookmark(key)?.visibleCount||80)
 const directed=useRef(0),timed=useRef(0)
 useReaderBookmark(key,root,{active:true,ready:Boolean(outline),visibleCount:visible,query,speakerFilter:speaker})
 useLayoutEffect(()=>{
  if(!root.current)return
  if(target&&directed.current!==target.token){
   const at=filtered.findIndex(({item})=>item.id===target.id)
   if(at>=visible){setVisible(at+1);return}
   const node=[...root.current.querySelectorAll<HTMLElement>('[data-reading-block-id]')].find(n=>n.dataset.readingBlockId===target.id)
   if(node){directed.current=target.token;revealReadingNode(root.current,node)}
  }else if(reveal&&timed.current!==reveal.token){
   timed.current=reveal.token
   const item=outline?.items[readingIndex(outline.items,reveal.time)]
   const at=filtered.findIndex(row=>row.item.id===item?.id)
   if(at>=visible){timed.current=0;setVisible(at+1);return}
   const node=[...root.current.querySelectorAll<HTMLElement>('[data-reading-block-id]')].find(n=>n.dataset.readingBlockId===item?.id)
   if(node)revealReadingNode(root.current,node)
  }
 },[target,reveal,outline,query,speaker,visible])
 const entries=needle?filtered.map(({item})=>({id:item.id,texts:[item.title,...texts(item)]})):[]
 const seen=new Set<string>()
 return <><TranscriptSearchNavigation query={query} entries={entries} root={root} visibleRevision={visible} onReveal={id=>{const at=filtered.findIndex(({item})=>item.id===id);setVisible(n=>Math.max(n,at+1))}}/><div className="optimization-reader reading-blocks" ref={root}>
  {filtered.slice(0,visible).map(({item,group})=>{const groupKey=group.occurrence_id||group.topic_id||'unclassified',heading=!seen.has(groupKey);seen.add(groupKey)
   return <section key={item.id} className="reading-block" data-search-owner={item.id} data-reading-block-id={item.id} data-reader-id={item.id} data-reader-time={item.start} data-reader-end={item.end}>
    {heading?<TopicHeading group={group}/>:null}
    <h3 className="reading-block-title">{highlightText(item.title,query)} <small>{formatTime(item.start,false)}–{formatTime(item.end,false)}{item.timing_precision==='segment'?` · ${t('optimization.topics.coarse')}`:''}</small></h3>
    <div className="reading-block-body">{illustrated?<AssociatedFrames job={job} block={item}/>:null}
    <div className="reading-block-copy">{render(item)}</div></div>
   </section>
  })}
  {visible<filtered.length?<button className="button" onClick={()=>setVisible(n=>n+80)}>{t('asr.results.loadMore')}</button>:null}
  {!filtered.length?<p>{t('asr.results.noMatches')}</p>:null}
 </div></>
}
export function sourceSegments(job:Job,block:OutlineItem):Segment[]{
 const indexed=segmentIndex(job.result)
 return (block.source_refs||[]).flatMap(ref=>{const source=indexed.get(ref.segment_id);return source?[{...source,text:Array.from(source.text).slice(ref.start_offset,ref.end_offset).join(''),start:ref.start,end:ref.end,words:source.words?.filter(w=>w.end>ref.start&&w.start<ref.end)}]:[]})
}
export function OriginalBlocks({job,query,speaker,illustrated,render,reveal,children}:{job:Job;query:string;speaker:string;illustrated:boolean;render:(segment:Segment)=>ReactNode;reveal?:{time:number;token:number};children:ReactNode}){
 const {outline,runId,grouped}=useReadingWorkspace()
 return outline?.version===2?<ReadingBlocks key={`${runId}:${grouped}`} job={job} query={query} speaker={speaker} illustrated={illustrated} mode="original" reveal={reveal} texts={block=>sourceSegments(job,block).map(s=>s.text)} render={block=>sourceSegments(job,block).filter(s=>speaker==='all'||s.speaker===speaker).map((s,i)=><div key={i}>{render(s)}</div>)}/>:children
}

const segmentIndices=new WeakMap<JobResult,Map<number,Segment>>()
function segmentIndex(result:JobResult|undefined){if(!result)return new Map<number,Segment>();let index=segmentIndices.get(result);if(!index){index=new Map(result.segments?.map(s=>[s.id,s]));segmentIndices.set(result,index)}return index}
const frameIndices=new WeakMap<JobResult,Map<string,NonNullable<JobResult['video']>['scenes'][number]>>()
function frameIndex(result:JobResult|undefined){if(!result)return new Map<string,NonNullable<JobResult['video']>['scenes'][number]>();let index=frameIndices.get(result);if(!index){index=new Map(result.video?.scenes.map(s=>[s.id,s]));frameIndices.set(result,index)}return index}
