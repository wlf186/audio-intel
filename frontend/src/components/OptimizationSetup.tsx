import {useEffect,useState} from 'react'
import {useTranslation} from 'react-i18next'
import {request} from '../lib/api'
import type {OptimizationController} from '../hooks/useOptimization'
import {needsServiceNotice,optimizationSettingsPath,readAnalyzeTopics,readRewriteMode,rewriteModes,saveAnalyzeTopics,saveRewriteMode,type OptimizationConfiguration,type OptimizationSummary} from '../lib/optimization'
import {OptimizationBudget,type BudgetChoice} from './OptimizationBudget'
import {SettingToggleRow} from './SettingToggleRow'

export type SetupAction='create'|'regenerate'|'resume'|'summaryRetry'
type Props={
 jobId:string;controller:OptimizationController;action:SetupAction;run?:OptimizationSummary|null
 topicsSupported:boolean;requestTopics:boolean;onClose:()=>void;onBack?:()=>void
}

export function OptimizationSetup({jobId,controller,action,run,topicsSupported,requestTopics,onClose,onBack}:Props){
 const {t}=useTranslation()
 const create=action==='create'||action==='regenerate'
 const [connection,setConnection]=useState<OptimizationConfiguration>()
 const [error,setError]=useState('')
 const [reload,setReload]=useState(0)
 const [mode,setMode]=useState(()=>run?.rewrite_mode??readRewriteMode())
 const [topics,setTopics]=useState(()=>requestTopics||readAnalyzeTopics())
 const [plan,setPlan]=useState<BudgetChoice>()
 const [estimateVersion,setEstimateVersion]=useState(0)
 useEffect(()=>{
  if(!create)return
  const abort=new AbortController()
  setError('')
  void request<OptimizationConfiguration>(optimizationSettingsPath,{signal:abort.signal})
   .then(value=>{if(!abort.signal.aborted)setConnection(value)})
   .catch(cause=>{if(!abort.signal.aborted)setError((cause as Error).message)})
  return ()=>abort.abort()
 },[create,reload])
 const label=t(action==='create'?'optimization.begin':action==='resume'?'optimization.metrics.raiseAndResume':action==='summaryRetry'?'optimization.retrySummaries':'optimization.regenerate')
 const enabled=Boolean(controller.status?.enabled&&(!create||connection?.enabled))
 const submit=async()=>{
  if(!plan||!enabled||controller.busy)return
  if(await controller.action(action,mode,'workflow',plan.limit,plan.fingerprint,create&&topicsSupported&&topics))onClose()
  else{setPlan(undefined);setEstimateVersion(v=>v+1)}
 }
 return <>
  <div className="optimization-dialog-body">
   {onBack?<button className="optimization-back" onClick={onBack}>{t('optimization.metrics.back')}</button>:null}
   <h3>{label}</h3>
   {create?<><p className="optimization-intro">{t(action==='regenerate'?'optimization.regenerateHelp':'optimization.workflowHelp')}</p>
    {connection?<div className="optimization-connection-info"><span>{t('optimization.model')} · {connection.model}</span>
     {needsServiceNotice(connection.base_url)?<p>{connection.base_url}<br/>{t('optimization.metrics.serviceNotice')}</p>:null}</div>:
     error?<p role="alert">{error} <button className="button" onClick={()=>setReload(n=>n+1)}>{t('common.actions.retry')}</button></p>:<p role="status">{t('common.states.loading')}</p>}
    <fieldset className="optimization-mode-choices"><legend>{t('optimization.chooseMode')}</legend>
     {rewriteModes.map(value=><label key={value} className={mode===value?'selected':''}>
      <input type="radio" name="rewrite-mode" value={value} checked={mode===value} onChange={()=>{setPlan(undefined);setMode(value);saveRewriteMode(value)}}/>
      <span><strong>{t(`optimization.rewriteModes.${value}.label`)}</strong><small>{t(`optimization.rewriteModes.${value}.help`)}</small></span>
     </label>)}
    </fieldset>
    {topicsSupported?<SettingToggleRow id="optimization-analyze-topics" label={t('optimization.topics.option')} description={t('reading.rememberTopics')} help={t('optimization.topics.help')} checked={topics} onChange={value=>{setPlan(undefined);setTopics(value);saveAnalyzeTopics(value)}}/>:null}
   </>:<p className="optimization-intro">{t('optimization.metrics.resumeBudget',{used:run?.calls_used??0})}</p>}
   {(connection||!create)&&controller.status?.enabled?<OptimizationBudget key={[mode,topics,estimateVersion].join('-')} jobId={jobId} runId={create?undefined:run?.id}
    mode={mode} analyzeTopics={create&&topicsSupported&&topics} adjust={!create} onPlan={setPlan}/>:null}
   {!controller.status?.enabled?<a className="button" href="#system" onClick={onClose}>{t('optimization.configure')}</a>:null}
  </div>
  <div className="optimization-dialog-footer"><button className="button" onClick={onClose}>{t('common.actions.cancel')}</button>
   <button className="primary" disabled={!enabled||!plan||controller.busy} onClick={()=>void submit()}>{label}</button></div>
 </>
}
