import {useCallback,useEffect,useRef,useState} from 'react'
import {createIdempotencyKey,request} from '../lib/api'
import {optimizationPath,type OptimizationStatus,type OptimizationResult} from '../lib/optimization'
import {readOptimizationView,saveOptimizationView} from '../lib/workspaceSession'

export function useOptimization(jobId:string|undefined,active:boolean,sourceVersion?:string){
 const [status,setStatus]=useState<OptimizationStatus>()
 const [error,setError]=useState('')
 const [busy,setBusy]=useState(false)
 const [view,setView]=useState(false)
 const [compare,setCompare]=useState(false)
 const [preview,setPreview]=useState<string>()
 const [result,setResult]=useState<OptimizationResult>()
 const [resultError,setResultError]=useState('')
 const [reload,setReload]=useState(0)
 const [resultReload,setResultReload]=useState(0)
 const generation=useRef(0)
 const statusEpoch=useRef(0)
 const pendingKey=useRef<string|undefined>(undefined)
 const actionController=useRef<AbortController|undefined>(undefined)
 useEffect(()=>{
  generation.current++;pendingKey.current=undefined;setStatus(undefined);setResult(undefined);setError('');setResultError('');setBusy(false);setCompare(false);setPreview(undefined);setView(jobId?readOptimizationView(jobId):false)
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
 const selected=(preview&&status?.candidate?.id===preview?status.candidate:status?.published||status?.candidate)
 const runId=selected?.id
 const completed=selected?.completed_units
 const published=selected?.published
 useEffect(()=>{
  if(!jobId||!active||(!view&&!compare)||!runId)return
  let disposed=false
  const controller=new AbortController()
  setResultError('')
  void request<OptimizationResult>(`${optimizationPath(jobId)}/result?run_id=${runId}`,{signal:controller.signal}).then(next=>{if(!disposed)setResult(next)}).catch(cause=>{if(!disposed)setResultError((cause as Error).message)})
  return()=>{disposed=true;controller.abort()}
 },[jobId,active,view,compare,runId,completed,published,sourceVersion,resultReload])
 const setOptimized=useCallback((value:boolean)=>{setView(value);if(jobId)saveOptimizationView(jobId,value)},[jobId])
 const action=async(kind:'create'|'regenerate'|'pause'|'resume'|'delete')=>{
  if(!jobId||busy)return
  const version=generation.current
  statusEpoch.current++
  const controller=new AbortController();actionController.current=controller
  setBusy(true);setError('')
  try{
   const root=optimizationPath(jobId)
   if(kind==='delete'){
    await request(`${root}?purge=true`,{method:'DELETE',signal:controller.signal})
    if(version===generation.current){setStatus(current=>current?{...current,published:null,candidate:null}:undefined);setResult(undefined);setOptimized(false);setCompare(false);setPreview(undefined)}
   }else{
    const create=kind==='create'||kind==='regenerate'
    if(create&&!pendingKey.current)pendingKey.current=createIdempotencyKey()
    const next=await request<OptimizationStatus>(create?root:`${root}/${kind}`,{method:'POST',signal:controller.signal,headers:{'Content-Type':'application/json',...(create?{'Idempotency-Key':pendingKey.current!}:{})},body:JSON.stringify(create?{regenerate:kind==='regenerate'}:{run_id:status?.candidate?.id})})
    if(version===generation.current){setStatus(next);pendingKey.current=undefined}
   }
  }catch(cause){if(!controller.signal.aborted&&version===generation.current)setError((cause as Error).message)}
  finally{if(version===generation.current){statusEpoch.current++;setBusy(false);setReload(n=>n+1)}}
 }
 return{status,error,busy,view,setOptimized,compare,setCompare,action,selected,result:result?.run_id===runId?result:undefined,resultError,retry:()=>{setReload(n=>n+1);setResultReload(n=>n+1)},previewCandidate:()=>{setPreview(status?.candidate?.id);setOptimized(true)},showPublished:()=>setPreview(undefined)}
}
export type OptimizationController=ReturnType<typeof useOptimization>
