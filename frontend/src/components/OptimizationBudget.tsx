import {useEffect,useState} from 'react'
import {useTranslation} from 'react-i18next'
import {request} from '../lib/api'
import {optimizationPath,type CallEstimate,type RewriteMode} from '../lib/optimization'

export type BudgetChoice={limit?:number;fingerprint:string}
type Props={
 jobId:string;runId?:string;mode:RewriteMode;analyzeTopics:boolean
 adjust:boolean;onPlan:(plan:BudgetChoice|undefined)=>void
}

export function OptimizationBudget({jobId,runId,mode,analyzeTopics,adjust,onPlan}:Props){
 const {t}=useTranslation()
 const [estimate,setEstimate]=useState<CallEstimate>()
 const [override,setOverride]=useState<number>()
 const [error,setError]=useState('')
 const [reload,setReload]=useState(0)
 const [expanded,setExpanded]=useState(adjust)

 useEffect(()=>{
  const abort=new AbortController()
  setEstimate(undefined)
  setError('')
  void request<CallEstimate>(optimizationPath(jobId)+'/estimate',{
   method:'POST',signal:abort.signal,headers:{'Content-Type':'application/json'},
   body:JSON.stringify({rewrite_mode:mode,...(analyzeTopics?{analyze_topics:true}:{}),...(runId?{run_id:runId}:{})}),
  }).then(value=>{
   if(![value.minimum_limit,value.recommended_limit,value.max_llm_calls].every(Number.isSafeInteger))throw new Error(t('optimization.estimateError'))
   if(!abort.signal.aborted)setEstimate(value)
  }).catch(cause=>{if(!abort.signal.aborted)setError((cause as Error).message)})
  return ()=>abort.abort()
 },[jobId,runId,mode,analyzeTopics,reload,t])

 const savedLimit=runId?estimate?.max_llm_calls:undefined
 const minimum=Math.max(1,estimate?.minimum_limit??1,savedLimit??1)
 const limit=override??(savedLimit!==undefined&&savedLimit>=minimum?savedLimit:estimate?.recommended_limit)
 const valid=limit!==undefined&&Number.isSafeInteger(limit)&&limit>=minimum&&limit<=2147483647
 const requestedLimit=runId&&limit!==savedLimit?limit:override
 useEffect(()=>{
  onPlan(estimate&&valid?{limit:requestedLimit,fingerprint:estimate.fingerprint}:undefined)
 },[estimate,requestedLimit,valid,onPlan])

 return <section className="optimization-budget">
  {!estimate&&!error?<p role="status">{t('optimization.estimating')}</p>:null}
  {error?<p role="alert">{error} <button className="button" onClick={()=>setReload(n=>n+1)}>{t('common.actions.retry')}</button></p>:null}
  {estimate?<><div className="optimization-budget-line">
   <span className="optimization-budget-estimate"><span>{t('optimization.metrics.estimatedCalls',{count:estimate.estimated_calls})}</span>
    <span aria-hidden="true"> · </span><span>{t('optimization.metrics.effectiveLimit',{limit})}</span></span>
   <button className="button" aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}>{t('optimization.metrics.adjust')}</button></div>
   {expanded?<div className="optimization-limit"><label>{t('optimization.runCallLimit')}
    <input type="number" min={minimum} max={2147483647} step={1} value={limit||''} aria-invalid={!valid} onChange={event=>setOverride(Number(event.target.value))}/></label>
    <small>{t('optimization.metrics.minimum',{minimum,recommended:estimate.recommended_limit})}</small></div>:null}
   {limit!==undefined&&limit<estimate.recommended_limit?<small role="status">{t('optimization.lowCallBudget')}</small>:null}
  </>:null}
 </section>
}
