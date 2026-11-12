import {useEffect,useState} from 'react'
import {useTranslation} from 'react-i18next'
import {request} from '../lib/api'
import {optimizationPath,type CallEstimate,type CallUsage,type RewriteMode} from '../lib/optimization'

export function OptimizationBudget({jobId,runId,mode,planning,defaultLimit,limit,onLimit,onReady,revision,onEstimate}:{jobId?:string;runId?:string;mode:RewriteMode;planning:boolean;defaultLimit:number;revision?:number;limit?:number;onLimit:(limit:number)=>void;onReady:(ready:boolean)=>void;onEstimate?:(fingerprint:string)=>void}){
 const {t}=useTranslation()
 const [estimate,setEstimate]=useState<CallEstimate>()
 const [usage,setUsage]=useState<CallUsage>()
 const [error,setError]=useState('')
 const [reload,setReload]=useState(0)
 useEffect(()=>{
  if(!jobId)return
  const abort=new AbortController()
  setEstimate(undefined);setUsage(undefined);setError('');onReady(!planning)
  const root=optimizationPath(jobId)
  const load=async()=>{
   try{
    if(planning){
     const next=await request<CallEstimate>(`${root}/estimate`,{method:'POST',signal:abort.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({rewrite_mode:mode,...(runId?{run_id:runId}:{})})})
     if(!Number.isSafeInteger(next.minimum_limit))throw new Error(t('optimization.estimateError'))
     if(!abort.signal.aborted){setEstimate(next);onEstimate?.(next.fingerprint);if(defaultLimit<next.recommended_limit)onLimit(next.recommended_limit)}
    }
    if(runId){const next=await request<CallUsage>(`${root}/usage?run_id=${runId}`,{signal:abort.signal});if(!abort.signal.aborted)setUsage(next)}
   }catch(cause){if(!abort.signal.aborted)setError((cause as Error).message)}
  }
  void load()
  return()=>abort.abort()
 },[jobId,runId,mode,planning,defaultLimit,revision,reload,onLimit,onReady,onEstimate,t])
 const effective=limit??defaultLimit
 const valid=Number.isSafeInteger(effective)&&effective>=Math.max(estimate?.minimum_limit??1,usage?.max_llm_calls??1)
 useEffect(()=>{onReady(!planning||Boolean(estimate&&valid))},[planning,estimate,valid,onReady])
 return <section className="optimization-budget">
  <strong>{t('optimization.callBudget')}</strong>
  {planning?<>
   {!estimate&&!error?<p role="status">{t('optimization.estimating')}</p>:null}
   {estimate?<p>{t('optimization.estimateSummary',{count:estimate.estimated_calls,recommended:estimate.recommended_limit})}</p>:null}
   <label>{t('optimization.runCallLimit')}<input type="number" min={Math.max(estimate?.minimum_limit??1,usage?.max_llm_calls??1)} step={1} value={effective||''} aria-invalid={!valid} onChange={event=>onLimit(Number(event.target.value))}/></label>
   <small>{t('optimization.temporaryLimit')}</small>
   {estimate&&effective<estimate.recommended_limit?<p role="status">{t('optimization.lowCallBudget')}</p>:null}
  </>:null}
  {usage?<details><summary>{t('optimization.usageDetails')}</summary>{usage.recorded?<>
   <p>{t('optimization.usageSummary',{used:usage.calls_used,limit:usage.max_llm_calls,remaining:usage.remaining_calls})}</p>
   {usage.estimate?<p>{t('optimization.estimateSummary',{count:usage.estimate.estimated_calls,recommended:usage.estimate.recommended_limit})}</p>:null}
   {Object.entries(usage.stages).map(([stage,count])=><p key={stage}>{t(`optimization.callStages.${stage}`,{defaultValue:stage})}: {count}</p>)}
   <p>{t('optimization.failedCalls',{count:usage.failed_or_interrupted})}</p>
  </>:<p>{t('optimization.usageUnrecorded')}</p>}</details>:null}
  {error?<p role="alert">{error} <button className="button" onClick={()=>setReload(n=>n+1)}>{t('common.actions.retry')}</button></p>:null}
 </section>
}
