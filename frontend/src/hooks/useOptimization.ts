import {useCallback,useEffect,useMemo,useRef,useState} from 'react'
import {createIdempotencyKey,request} from '../lib/api'
import {optimizationPath,type OptimizationStatus,type OptimizationResult,type OptimizationInsights,type RewriteMode} from '../lib/optimization'
import {clearOptimizationBookmarks,checkpointWorkspace,readOptimizationView,saveOptimizationView,type ReadingMode} from '../lib/workspaceSession'

export function useOptimization(jobId:string|undefined,active:boolean,sourceVersion?:string){
 const [status,setStatus]=useState<OptimizationStatus>()
 const [error,setError]=useState('')
 const [busy,setBusy]=useState(false)
 const [mode,setModeState]=useState<ReadingMode>('original')
 const view=mode!=='original'
 const compare=mode==='comparison'
 const [paragraphFilter,setParagraphFilter]=useState('all')
 const [result,setResult]=useState<OptimizationResult>()
 const [resultError,setResultError]=useState('')
 const [insights,setInsights]=useState<OptimizationInsights>()
 const [insightsError,setInsightsError]=useState('')
 const [reload,setReload]=useState(0)
 const [resultReload,setResultReload]=useState(0)
 const generation=useRef(0)
 const statusEpoch=useRef(0)
 const pendingKey=useRef<string|undefined>(undefined)
 const pendingRequest=useRef('')
 const actionController=useRef<AbortController|undefined>(undefined)
 useEffect(()=>{
  generation.current++;pendingKey.current=undefined;setStatus(undefined);setResult(undefined);setInsights(undefined);setInsightsError('');setError('');setResultError('');setBusy(false);setParagraphFilter('all');setModeState(jobId?readOptimizationView(jobId):'original')
  return()=>{generation.current++;actionController.current?.abort()}
 },[jobId])
 useEffect(()=>{
  if(!jobId||!active)return
  let disposed=false
  let timer:ReturnType<typeof setTimeout>|undefined
  const controller=new AbortController()
  const poll=async()=>{
   const epoch=statusEpoch.current
   try{const next=await request<OptimizationStatus>(optimizationPath(jobId),{signal:controller.signal});if(!disposed&&epoch===statusEpoch.current){setStatus(next);setError('')}}
   catch(cause){if(!disposed&&epoch===statusEpoch.current)setError((cause as Error).message)}
   finally{if(!disposed)timer=setTimeout(()=>void poll(),2000)}
  }
  void poll()
  return()=>{disposed=true;controller.abort();clearTimeout(timer)}
 },[jobId,active,reload])
 useEffect(()=>{if(status&&!status.published&&jobId)clearOptimizationBookmarks(jobId);if(status&&!status.published&&mode!=='original'){setModeState('original');if(jobId)saveOptimizationView(jobId,'original')}},[status,mode,jobId])
 const selected=status?.published
 const runId=selected?.id
 const completed=selected?.completed_units
 const published=selected?.published
 const revision=selected?.summary_status?.version?undefined:selected?.revision
 const insightVersion=selected?.summary_status?.version
 const insightRevision=selected?.summary_status?.revision
 useEffect(()=>{
  if(!jobId||!active||!runId||!insightVersion)return
  let disposed=false
  const controller=new AbortController()
  void request<OptimizationInsights>(`${optimizationPath(jobId)}/summaries?run_id=${runId}`,{signal:controller.signal}).then(next=>{if(!disposed){setInsights(next);setInsightsError('')}}).catch(cause=>{if(!disposed)setInsightsError((cause as Error).message)})
  return()=>{disposed=true;controller.abort()}
 },[jobId,active,runId,insightVersion,insightRevision,resultReload])
 useEffect(()=>{
  if(!jobId||!active||(!view&&!compare)||!runId)return
  let disposed=false
  const controller=new AbortController()
  setResultError('')
  void request<OptimizationResult>(`${optimizationPath(jobId)}/result?run_id=${runId}`,{signal:controller.signal}).then(next=>{if(!disposed){const display=(p:typeof next.paragraphs[number])=>({...p,candidate_text:p.candidate_text===p.original_text?null:p.candidate_text,text:p.candidate_text??p.text});checkpointWorkspace();setResult({...next,revision,paragraphs:next.paragraphs.map(display),scenes:next.scenes.map(scene=>({...scene,paragraphs:scene.paragraphs.map(display)}))})}}).catch(cause=>{if(!disposed)setResultError((cause as Error).message)})
  return()=>{disposed=true;controller.abort()}
 },[jobId,active,view,compare,runId,completed,published,revision,sourceVersion,resultReload])
 const setMode=useCallback((value:ReadingMode)=>{setModeState(value);if(jobId)saveOptimizationView(jobId,value)},[jobId])
 const setOptimized=useCallback((value:boolean)=>setMode(value?'optimized':'original'),[setMode])
 const setCompare=useCallback((value:boolean)=>setMode(value?'comparison':'optimized'),[setMode])
 const action=async(kind:'create'|'regenerate'|'pause'|'resume'|'delete'|'summaryRetry'|'summaryPause',rewriteMode:RewriteMode='rewrite',phase:'workflow'|'generation'|'review'='workflow')=>{
  if(!jobId||busy)return
  const version=generation.current
  statusEpoch.current++
  const controller=new AbortController();actionController.current=controller
  setBusy(true);setError('')
  try{
   const root=optimizationPath(jobId)
   if(kind==='delete'){
    await request(`${root}?purge=true`,{method:'DELETE',signal:controller.signal})
    if(version===generation.current){setStatus(current=>current?{...current,published:null,candidate:null}:undefined);setResult(undefined);setOptimized(false)}
   }else{
    const create=kind==='create'||kind==='regenerate'
    const auxiliary=kind==='summaryRetry'||kind==='summaryPause'
    const idempotent=create||kind==='summaryRetry'
    const path=kind==='summaryRetry'?'summaries/retry':kind==='summaryPause'?'summaries/pause':kind
    const identity=JSON.stringify({kind,rewriteMode})
    if(idempotent&&(!pendingKey.current||pendingRequest.current!==identity)){pendingKey.current=createIdempotencyKey();pendingRequest.current=identity}
    const next=await request<OptimizationStatus>(create?root:`${root}/${path}`,{method:'POST',signal:controller.signal,headers:{'Content-Type':'application/json',...(idempotent?{'Idempotency-Key':pendingKey.current!}:{})},body:JSON.stringify(create?{regenerate:kind==='regenerate',rewrite_mode:rewriteMode}:{run_id:auxiliary?status?.published?.id:status?.candidate?.id,phase})})
    if(version===generation.current){setStatus(next);pendingKey.current=undefined}
   }
  }catch(cause){if(!controller.signal.aborted&&version===generation.current)setError((cause as Error).message)}
  finally{if(version===generation.current){statusEpoch.current++;setBusy(false);setReload(n=>n+1)}}
 }
 const invalidate=()=>{
  statusEpoch.current++;generation.current++;actionController.current?.abort()
  setBusy(false);setStatus(current=>current?{...current,published:null,candidate:null}:undefined)
  setResult(undefined);setInsights(undefined);setInsightsError('');setResultError('');setError('');setMode('original');setParagraphFilter('all')
  setReload(n=>n+1)
 }
 const currentInsights=insights?.run_id===runId?insights:undefined
 const readingResult=useMemo(()=>{
  if(!result||result.run_id!==runId)return undefined
  if(!currentInsights)return result
  const paragraph=(p:OptimizationResult['paragraphs'][number])=>({...p,group_summary:currentInsights.groups[String(p.ordinal)]})
  return {...result,summary_status:currentInsights.summary_status,paragraphs:result.paragraphs.map(paragraph),scenes:result.scenes.map(scene=>({...scene,title_summary:currentInsights.scenes[scene.id],paragraphs:scene.paragraphs.map(paragraph)}))}
 },[result,runId,currentInsights])
 return{insights:currentInsights,insightsError,invalidate,paragraphFilter,setParagraphFilter,status,error,busy,mode,setMode,view,setOptimized,compare,setCompare,action,selected,result:readingResult,resultError,retry:()=>{setReload(n=>n+1);setResultReload(n=>n+1)}}
}
export type OptimizationController=ReturnType<typeof useOptimization>
