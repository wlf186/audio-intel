import {createContext,useContext,useEffect,useMemo,useRef,useState,type ReactNode} from 'react'
import {useTranslation} from 'react-i18next'
import {request} from '../lib/api'
import {optimizationPath,type OptimizationSummary} from '../lib/optimization'
import type {Segment} from '../lib/types'
import {checkpointWorkspace,readMergedTopics,saveMergedTopics} from '../lib/workspaceSession'
import {Modal} from './Modal'
import './reading-workspace.css'
export type OutlineItem={id:string;kind:'scene'|'paragraph'|'block';title:string;number:number|null;start:number;end:number;primary_topic_id:string|null;related_topic_ids:string[];timing_precision?:string;occurrence_id?:string;paragraph_id?:string;scene_ids?:string[];frame_numbers?:number[];source_refs?:{segment_id:number;start_offset:number;end_offset:number;start:number;end:number}[]}
export type OutlineGroup={occurrence_id?:string;recurring?:boolean;topic_id:string|null;title:string;summary:string;primary_item_ids:string[];related_item_ids:string[]}
export type ReadingOutline={version:number;merged_groups?:OutlineGroup[];groups:OutlineGroup[];items:OutlineItem[]}
const ReadingContext=createContext<{outline?:ReadingOutline;grouped:boolean;tools:HTMLElement|null;navigationTools:HTMLElement|null;seek:(time:number)=>void;runId?:string;target?:{id:string;token:number}}>({grouped:false,tools:null,navigationTools:null,seek:()=>{}})
export const useReadingWorkspace=()=>useContext(ReadingContext)
export function readingScenes<T extends {id:string}>(scenes:T[],outline:ReadingOutline|undefined,grouped:boolean){
 if(!outline||!grouped)return scenes.map(scene=>({scene,group:undefined as OutlineGroup|undefined}))
 const indexed=new Map(scenes.map(scene=>[scene.id,scene])),seen=new Set<string>(),rows:{scene:T;group?:OutlineGroup}[]=[]
 for(const group of outline.groups){let first=true;for(const id of group.primary_item_ids){const scene=indexed.get(id);if(scene&&!seen.has(id)){rows.push({scene,group:first?group:undefined});seen.add(id);first=false}}if(first&&group.related_item_ids.length){/* Related-only groups are shown by the directory. */}}
 for(const scene of scenes)if(!seen.has(scene.id))rows.push({scene})
 return rows
}
export function TopicHeading({group}:{group:OutlineGroup}){
 const {outline,seek}=useReadingWorkspace(),{t}=useTranslation()
 return <header className="reading-topic-heading" data-topic-id={group.topic_id??'unclassified'}><h2>{group.title}{group.recurring?<small> · {t('reading.continued')}</small>:null}</h2>{group.summary?<p>{group.summary}</p>:null}{group.related_item_ids.map(id=>{const item=outline?.items.find(i=>i.id===id);return item?<button className="button" key={id} onClick={()=>seek(item.start)}>{t('reading.related')} · {item.title}</button>:null})}</header>
}
export function OrderedAudio({segments,render}:{segments:Segment[];render:(segment:Segment)=>ReactNode}){
 const {outline,grouped}=useReadingWorkspace()
 const rows=useMemo(()=>readingParagraphs(segments.map(segment=>({...segment,id:String(segment.id),segment,segment_ids:[segment.id]})),outline,grouped),[segments,outline,grouped])
 return <>{rows.map(({scene,group})=><div key={scene.id}>{group?<TopicHeading group={group}/>:null}{render(scene.segment)}</div>)}<RelatedTopics/></>
}
export function readingParagraphs<T extends {id:string;start:number;end:number;segment_ids:number[];scene_ids?:string[]}>(paragraphs:T[],outline:ReadingOutline|undefined,grouped:boolean){
 if(!outline||!grouped)return readingScenes(paragraphs,undefined,false)
 const items=new Map(outline.items.map(item=>[item.id,item]))
 const owners=new Map<string,string|null>()
 for(const paragraph of paragraphs){
  const direct=(paragraph.scene_ids?.length?paragraph.scene_ids:paragraph.segment_ids.map(String)).flatMap(id=>items.get(id)?[items.get(id)!]:[])
  const scores=new Map<string|null,number>()
  for(const item of direct.length?direct:outline.items){const weight=direct.length?1:Math.max(0,Math.min(item.end,paragraph.end)-Math.max(item.start,paragraph.start));if(weight)scores.set(item.primary_topic_id,(scores.get(item.primary_topic_id)||0)+weight)}
  const ranked=outline.groups.filter(group=>scores.has(group.topic_id)).sort((a,b)=>scores.get(b.topic_id)!-scores.get(a.topic_id)!)
  owners.set(paragraph.id,ranked[0]?.topic_id??null)
 }
 return readingScenes(paragraphs,{...outline,groups:outline.groups.map(group=>({...group,primary_item_ids:paragraphs.filter(p=>owners.get(p.id)===group.topic_id).map(p=>p.id)}))},true)
}
export function RelatedTopics(){
 const {outline,grouped}=useReadingWorkspace()
 return grouped?<>{outline?.groups.filter(group=>!group.primary_item_ids.length&&group.related_item_ids.length).map(group=><TopicHeading key={group.topic_id} group={group}/>)}</>:null
}
export function ReadingWorkspace({jobId,run,management,onSeek,notices,onOutlineReady,filterTools,children}:{jobId:string;run?:OptimizationSummary|null;management?:ReactNode;notices?:ReactNode;filterTools?:ReactNode;onOutlineReady?:(available:boolean)=>void;onSeek:(time:number)=>void;children:ReactNode}){
 const {t}=useTranslation(),[outline,setOutline]=useState<ReadingOutline>(),[loading,setLoading]=useState(false),[error,setError]=useState(''),[reload,setReload]=useState(0),[open,setOpen]=useState(true),[drawer,setDrawer]=useState(false),[grouped,setGrouped]=useState(()=>run?.id?readMergedTopics(jobId,run.id):false),[target,setTarget]=useState<{id:string;token:number}>(),[tools,setTools]=useState<HTMLElement|null>(null),[wide,setWide]=useState(false),[desktop,setDesktop]=useState(()=>matchMedia('(min-width:1024px) and (min-height:651px)').matches)
 const root=useRef<HTMLDivElement>(null)
 const state=run?.topic_state,runId=run?.id
 useEffect(()=>{setOutline(undefined);setError('');setGrouped(runId?readMergedTopics(jobId,runId):false);setTarget(undefined)},[jobId,runId])
 useEffect(()=>{const node=root.current;if(!node)return;const observer=new ResizeObserver(([entry])=>{setWide(entry.contentRect.width>=960);setDesktop(matchMedia('(min-width:1024px) and (min-height:651px)').matches)});observer.observe(node);return()=>observer.disconnect()},[])
 useEffect(()=>{
  if(!runId||!state||state==='not_requested')return
  const abort=new AbortController();setError('');setLoading(true)
  void request<{outline?:ReadingOutline|null}>(`${optimizationPath(jobId)}/topics?run_id=${runId}&include_outline=true&outline_version=2`,{signal:abort.signal}).then(data=>{if(!abort.signal.aborted&&data.outline){setOutline(data.outline);onOutlineReady?.(true)}}).catch(cause=>{if(!abort.signal.aborted)setError((cause as Error).message)}).finally(()=>{if(!abort.signal.aborted)setLoading(false)})
  return()=>abort.abort()
 },[jobId,runId,state,run?.summary_status?.revision,run?.topic_revision,reload,onOutlineReady])
 const projected=useMemo(()=>outline&&grouped&&outline.merged_groups?{...outline,groups:outline.merged_groups}:outline,[outline,grouped])
 const value=useMemo(()=>({outline:projected,runId,grouped,tools,navigationTools:desktop?tools:null,seek:onSeek,target}),[projected,runId,grouped,tools,desktop,onSeek,target])
 const navigate=(item:OutlineItem)=>{onSeek(item.start);setTarget({id:item.id,token:Date.now()});setDrawer(false)}
 const items=useMemo(()=>new Map(outline?.items.map(item=>[item.id,item])),[outline])
 const directory=<><nav aria-label={t('reading.directory')}>
 {error?<p role="alert">{error}<button className="button" onClick={()=>setReload(n=>n+1)}>{t('common.actions.retry')}</button></p>:!outline&&loading?<p role="status">{t('optimization.topics.loading')}</p>:!outline?<p>{t('reading.noTopics')}{state&&state!=='not_requested'?<button className="button" onClick={()=>setReload(n=>n+1)}>{t('common.actions.retry')}</button>:null}</p>:null}
 {projected?.groups.map(group=><details key={group.occurrence_id??group.topic_id??'unclassified'} open><summary>{group.title}</summary><ol>{group.primary_item_ids.map(id=>{const item=items.get(id);return item?<li key={id}><button aria-current={target?.id===item.id?'location':undefined} onClick={()=>navigate(item)}>{item.number?`${item.number}. `:''}{item.title}</button></li>:null})}{group.related_item_ids.map(id=>{const item=items.get(id);return item?<li key={'related:'+id}><button aria-current={target?.id===item.id?'location':undefined} onClick={()=>navigate(item)}>↗ {item.title}</button></li>:null})}</ol></details>)}
 </nav>{management}</>
 return <ReadingContext.Provider value={value}><div className="reading-workspace" ref={root} data-sidebar={wide&&open&&Boolean(outline)&&!drawer}>
 <div className="reading-workspace-tools">{state&&state!=='not_requested'?<button className="button" aria-expanded={wide?open&&Boolean(outline):drawer} onClick={()=>wide&&outline?setOpen(v=>!v):setDrawer(v=>!v)}>{t('reading.directory')}</button>:null}{outline?<label className="reading-merge-toggle" title={t('reading.mergeHelp')}><input type="checkbox" aria-description={t('reading.mergeHelp')} checked={grouped} onChange={e=>{checkpointWorkspace();setGrouped(e.target.checked);if(runId)saveMergedTopics(jobId,runId,e.target.checked)}}/>{t('reading.mergeRepeated')}</label>:null}<div className="optimization-filter-row">{filterTools}</div><div className="reading-reader-tools" ref={setTools}/>{notices?<details className="reader-options"><summary>{t('reading.mediaStatus')}</summary><div className="optimization-summary">{notices}</div></details>:null}</div>
 {wide&&open&&outline&&!drawer?<aside className="reading-directory">{directory}</aside>:null}
 <div className="reading-content">{children}</div>
 {!drawer&&!(wide&&open&&outline)?<div className="reading-management-standby">{management}</div>:null}
 {drawer?<Modal className="reading-directory-modal" title={t('reading.directory')} closeLabel={t('common.actions.close')} onClose={()=>setDrawer(false)}>{directory}</Modal>:null}
 </div></ReadingContext.Provider>
}
