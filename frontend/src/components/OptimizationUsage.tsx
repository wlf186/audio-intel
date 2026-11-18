import {useEffect,useState} from 'react'
import {useTranslation} from 'react-i18next'
import {request} from '../lib/api'
import {optimizationPath,type CallUsage,type OptimizationSummary,type TokenUsage} from '../lib/optimization'

function duration(ms:number,locale:string){
 const unit=(value:number,name:string)=>new Intl.NumberFormat(locale,{style:'unit',unit:name,unitDisplay:'short'}).format(value)
 if(ms>0&&ms<1000)return unit(ms/1000,'second')
 const seconds=Math.floor(ms/1000)
 if(seconds>=3600)return unit(Math.floor(seconds/3600),'hour')+' '+unit(Math.floor(seconds%3600/60),'minute')
 if(seconds>=60)return unit(Math.floor(seconds/60),'minute')+' '+unit(seconds%60,'second')
 return unit(seconds,'second')
}

function tokenCount(tokens:TokenUsage|undefined){
 if(!tokens)return undefined
 if(tokens.total_recorded!==undefined)return {value:tokens.total_tokens,complete:tokens.total_recorded===tokens.attempts}
 // Older servers only recorded input and output separately.
 const {prompt_tokens:input,completion_tokens:output,attempts}=tokens
 return {value:input==null&&output==null?null:(input??0)+(output??0),complete:tokens.input_recorded===attempts&&tokens.output_recorded===attempts}
}

export function OptimizationUsage({jobId,run}:{jobId:string;run:OptimizationSummary}){
 const {t,i18n}=useTranslation()
 const [usage,setUsage]=useState<CallUsage>()
 const [error,setError]=useState('')
 const [reload,setReload]=useState(0)
 const active=run.state==='running'||run.state==='pausing'||run.summary_status?.state==='running'||run.topic_state==='running'
 const revision=active?undefined:run.revision
 useEffect(()=>{
  const abort=new AbortController()
  let timer:ReturnType<typeof setTimeout>|undefined
  const fetchUsage=async()=>{
   try{
    const value=await request<CallUsage>(optimizationPath(jobId)+'/usage?run_id='+encodeURIComponent(run.id),{signal:abort.signal})
    if(!abort.signal.aborted){setUsage(value);setError('')}
   }catch(cause){if(!abort.signal.aborted)setError((cause as Error).message)}
   if(active&&!abort.signal.aborted)timer=setTimeout(()=>void fetchUsage(),2000)
  }
  void fetchUsage()
  return ()=>{abort.abort();clearTimeout(timer)}
 },[jobId,run.id,active,revision,reload])
 if(error)return <p role="alert">{error} <button className="button" onClick={()=>{setError('');setReload(n=>n+1)}}>{t('common.actions.retry')}</button></p>
 if(!usage)return <p role="status">{t('optimization.metrics.loading')}</p>

 const unknown=t('optimization.metrics.unrecorded')
 const number=(value:number)=>value.toLocaleString(i18n.language)
 const lowerBound=(value:string)=>t('optimization.metrics.atLeast',{value})
 const time=usage.active_duration_ms==null?unknown:duration(usage.active_duration_ms,i18n.language)
 const tokens=usage.token_usage?.attempts!=null?usage.token_usage:undefined
 const count=tokenCount(tokens)
 let tokenLabel=unknown
 if(usage.recorded&&tokens){
  if(count?.value!=null)tokenLabel=count.complete?number(count.value):lowerBound(number(count.value))
  else if(!tokens.attempts)tokenLabel=t('optimization.metrics.noCalls')
  else tokenLabel=t(usage.in_progress_calls===tokens.attempts?'optimization.metrics.awaiting':'optimization.metrics.notReturned')
 }
 return <section className="optimization-usage" aria-label={t('optimization.metrics.overview')}>
  <dl className="optimization-metrics">
   <div><dt>{t('optimization.metrics.duration')}</dt><dd>{usage.timing_partial&&usage.active_duration_ms!=null?lowerBound(time):time}</dd></div>
   <div><dt>{t('optimization.metrics.calls')}</dt><dd>{usage.calls_used==null?unknown:t('optimization.metrics.callCount',{count:usage.calls_used})}</dd></div>
   <div><dt>{t('optimization.metrics.tokens')}</dt><dd>{tokenLabel}</dd></div>
  </dl>
  {usage.recorded?<details className="optimization-breakdown"><summary>{t('optimization.metrics.breakdown')}</summary>
   {usage.max_llm_calls!=null?<p>{t('optimization.usageSummary',{used:usage.calls_used,limit:usage.max_llm_calls,remaining:usage.remaining_calls})}</p>:null}
   {Object.keys(usage.stages).length>0?<dl>{Object.entries(usage.stages).map(([stage,n])=><div key={stage}><dt>{t('optimization.callStages.'+stage,{defaultValue:t('optimization.metrics.otherStage')})}</dt><dd>{number(n)}</dd></div>)}</dl>:null}
   {tokens?<><p>{t('optimization.tokenInput',{tokens:tokens.prompt_tokens==null?t('optimization.unknownTokens'):number(tokens.prompt_tokens),known:tokens.input_recorded,total:tokens.attempts})}</p>
    <p>{t('optimization.tokenOutput',{tokens:tokens.completion_tokens==null?t('optimization.unknownTokens'):number(tokens.completion_tokens),known:tokens.output_recorded,total:tokens.attempts})}</p>
    {tokens.total_recorded!=null&&tokens.attempts>0?<p>{t('optimization.metrics.coverage',{known:tokens.total_recorded,total:tokens.attempts})}</p>:null}</>:null}
   {usage.in_progress_calls?<p>{t('optimization.metrics.inFlight',{count:usage.in_progress_calls})}</p>:null}
   {usage.failed_or_interrupted>0?<p>{t('optimization.failedCalls',{count:usage.failed_or_interrupted})}</p>:null}
  </details>:null}
 </section>
}
