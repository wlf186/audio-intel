import {useEffect,useMemo,useRef,useState} from 'react'
import {useTranslation} from 'react-i18next'
import {createIdempotencyKey,formatTime,request} from '../lib/api'
import {optimizationPath,type OptimizationSummary} from '../lib/optimization'
import type {JobResult,Segment} from '../lib/types'
import {Modal} from './Modal'

type Range={start:number;end:number;timing_precision:string;source_refs:{segment_id:number;start_offset:number;end_offset:number}[]}
type Catalogue={topics:{id:string;title:string;summary:string;summary_method?:string;ranges:Range[]}[];unclassified:Range[];no_substantive_content:boolean}
type Response={run_id:string;result:Catalogue|null;error:string|null}
type Estimate={recommended_limit:number;minimum_limit:number;fingerprint:string;estimated_calls:number}

function SourceEvidence({range,segments,label}:{range:Range;segments:Map<number,Segment>;label:string}){
 const [open,setOpen]=useState(false)
 const text=useMemo(()=>open?range.source_refs.map(ref=>{const segment=segments.get(ref.segment_id);return segment?Array.from(segment.text).slice(ref.start_offset,ref.end_offset).join(''):''}).join(' '):'',[open,range,segments])
 return <details onToggle={event=>setOpen(event.currentTarget.open)}><summary>{label}</summary>{open?<p>{text}</p>:null}</details>
}

export function TopicDirectory({jobId,run,enabled,result,onSeek,onUpdated,onRegenerate,trigger=0}:{jobId:string;run?:OptimizationSummary|null;enabled:boolean;result:JobResult;onSeek:(time:number)=>void;onUpdated:()=>void;trigger?:number;onRegenerate?:()=>void}){
 const {t}=useTranslation()
 const [open,setOpen]=useState(false),[data,setData]=useState<Response>(),[error,setError]=useState(''),[reload,setReload]=useState(0)
 const [dialog,setDialog]=useState(false),[estimate,setEstimate]=useState<Estimate>(),[limit,setLimit]=useState(100),[busy,setBusy]=useState(false)
 const [actionError,setActionError]=useState('')
 const actionVersion=useRef(0)
 const actionAbort=useRef<AbortController|undefined>(undefined)
 const pending=useRef<{identity:string;key:string}|undefined>(undefined)
 const segmentIndex=useMemo(()=>new Map((result.segments||[]).map(segment=>[segment.id,segment])),[result.segments])
 const triggered=useRef(trigger)
 const runId=run?.id,state=run?.topic_state||'not_requested'
 const active=state==='queued'||state==='running'
 const root=optimizationPath(jobId)+'/topics'
 const regenerate=state==='completed'
 useEffect(()=>{if(trigger!==triggered.current){triggered.current=trigger;if(runId){if(onRegenerate)onRegenerate();else setDialog(true)}}},[trigger,runId])
 useEffect(()=>{actionVersion.current++;setOpen(false);setData(undefined);setError('');setDialog(false);setBusy(false);setActionError('');pending.current=undefined;return()=>{actionVersion.current++;actionAbort.current?.abort()}},[jobId,runId])
 useEffect(()=>{
  if(!open||!runId)return
  const abort=new AbortController();setError('')
  void request<Response>(`${root}?run_id=${runId}`,{signal:abort.signal}).then(value=>{if(!abort.signal.aborted)setData(value)}).catch(cause=>{if(!abort.signal.aborted)setError((cause as Error).message)})
  return()=>abort.abort()
 },[open,runId,root,state,reload])
 useEffect(()=>{
  if(!dialog||!runId)return
  const abort=new AbortController();setEstimate(undefined);setActionError('')
  void request<Estimate>(root+'/estimate',{method:'POST',signal:abort.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({run_id:runId,regenerate})}).then(value=>{if(!abort.signal.aborted){setEstimate(value);setLimit(Math.max(value.recommended_limit,run?.calls_limit||100))}}).catch(cause=>{if(!abort.signal.aborted)setActionError((cause as Error).message)})
  return()=>abort.abort()
 },[dialog,runId,root,regenerate,run?.calls_limit,reload])
 if(!run)return null
 async function action(pause=false){
  setBusy(true);setActionError('')
  const version=actionVersion.current,abort=new AbortController();actionAbort.current=abort
  try{
   const body={run_id:runId,...(!pause?{regenerate,max_llm_calls:limit,estimate_fingerprint:estimate?.fingerprint}:{})}
   const identity=JSON.stringify(body)
   if(pending.current?.identity!==identity)pending.current={identity,key:createIdempotencyKey()}
   await request(root+(pause?'/pause':''),{method:'POST',signal:abort.signal,headers:{'Content-Type':'application/json',...(!pause?{'Idempotency-Key':pending.current!.key}:{})},body:JSON.stringify(body)})
   if(abort.signal.aborted||version!==actionVersion.current)return
   pending.current=undefined;setDialog(false);onUpdated();setReload(n=>n+1)
  }catch(cause){if(!abort.signal.aborted&&version===actionVersion.current)setActionError((cause as Error).message)}finally{if(version===actionVersion.current)setBusy(false)}
 }
 const catalogue=data&&data.run_id===runId?data.result:undefined
 const intervals=(ranges:Range[])=>ranges.map((range,index)=><div className="topic-range" key={index}>
  <button className="button" onClick={()=>onSeek(range.start)}>{formatTime(range.start,false)}–{formatTime(range.end,false)}{range.timing_precision==='segment'?` · ${t('optimization.topics.coarse')}`:''}</button>
  <SourceEvidence range={range} segments={segmentIndex} label={t('optimization.topics.source')}/>
 </div>)
 const modal=dialog?<Modal title={t('optimization.topics.title')} closeLabel={t('common.actions.close')} onClose={()=>setDialog(false)}>
   <p>{t('optimization.topics.help')}</p>
   {estimate?<p>{t('optimization.estimateSummary',{count:estimate.estimated_calls,recommended:estimate.recommended_limit})}</p>:!actionError?<p role="status">{t('optimization.estimating')}</p>:null}
   <label>{t('optimization.topics.limit')}<input type="number" value={limit} min={Math.max(estimate?.minimum_limit||1,run.calls_limit||1)} onChange={e=>setLimit(Number(e.target.value))}/></label>
   {actionError?<p role="alert">{actionError} <button className="button" disabled={busy} onClick={()=>setReload(n=>n+1)}>{t('optimization.topics.retry')}</button></p>:null}
   <div className="modal-actions"><button className="button" onClick={()=>setDialog(false)}>{t('common.actions.cancel')}</button><button className="primary" disabled={!estimate||busy||!Number.isSafeInteger(limit)||limit<Math.max(estimate.minimum_limit,run.calls_limit||1)} onClick={()=>void action()}>{t('optimization.topics.confirm')}</button></div>
  </Modal>:null
 if(state==='not_requested')return modal
 return <section className="topic-directory">
  <div className="topic-directory-tools"><button className="button" aria-expanded={open} onClick={()=>setOpen(v=>!v)}>{t('optimization.topics.source')}</button>
   {active?<><span role="status">{t('optimization.topics.discovering')} · {run.topic_completed_nodes||0}/{run.topic_total_nodes||0}</span><button className="button" disabled={busy} onClick={()=>void action(true)}>{t('optimization.topics.pause')}</button></>:<button className="button" disabled={!enabled||busy||run.calls_limit==null} onClick={()=>onRegenerate?onRegenerate():setDialog(true)}>{t(onRegenerate?'reading.regenerateAll':state==='completed'?'optimization.topics.regenerate':state==='paused'?'optimization.topics.resume':'optimization.topics.generate')}</button>}
  </div>
  {open?<div className="topic-catalogue" aria-busy={!data&&!error}>
   {error?<p role="alert">{error} <button className="button" onClick={()=>setReload(v=>v+1)}>{t('optimization.topics.retry')}</button></p>:!data?<p role="status">{t('optimization.topics.loading')}</p>:null}
   {data?.error?<p role="alert">{data.error}</p>:null}
   {catalogue?.no_substantive_content?<p>{t('optimization.topics.empty')}</p>:null}
   {catalogue?.topics.map(topic=><article key={topic.id}><h3>{topic.title}</h3><p>{topic.summary_method==='source_excerpt'?<small>{t('optimization.sourceInsight')} · </small>:null}{topic.summary}</p>{intervals(topic.ranges)}</article>)}
   {catalogue?.unclassified.length?<aside><p>{t('optimization.topics.unclassified')}</p>{intervals(catalogue.unclassified)}</aside>:null}
  </div>:null}
  {actionError&&!dialog?<p role="alert">{actionError}</p>:null}
  {modal}
 </section>
}
