import {createPortal} from 'react-dom'
import {useEffect,useId,useRef,useState,type ReactNode} from 'react'
import {useTranslation} from 'react-i18next'
import {Sparkles} from 'lucide-react'
import type {OptimizationController} from '../hooks/useOptimization'
import type {OptimizationSummary} from '../lib/optimization'
import type {ReadingMode} from '../lib/workspaceSession'
import {ConfirmDialog} from './ConfirmDialog'
import {Modal} from './Modal'
import {OptimizationSetup,type SetupAction} from './OptimizationSetup'
import {OptimizationUsage} from './OptimizationUsage'
import './optimization.css'

type Props={
 controller:OptimizationController;video:boolean;videoReading:boolean;topicsSupported?:boolean;regenerateTrigger?:number
 onOriginal:()=>void;onDetails:()=>void;onModeChange:(mode:ReadingMode)=>void;onRequestTopics?:()=>void
 actionsTarget?:HTMLElement|null;tools?:ReactNode;children?:ReactNode
}

function RunProgress({run,description,children}:{run:OptimizationSummary;description:string;children:ReactNode}){
 const {t}=useTranslation()
 const counts=run.summary_status?.counts
 let done=run.completed_chars,total=run.total_chars
 let message:'optimization.characterProgress'|'optimization.checkProgress'|'optimization.metrics.summaryCount'|'optimization.metrics.topicCount'='optimization.characterProgress'
 let stage:`optimization.states.${OptimizationSummary['state']}`|'optimization.checking'|'optimization.summarizing'|'optimization.topics.discovering'=`optimization.states.${run.state}`
 if(run.phase==='review'){
  done=run.reviewed_parts??0;total=run.total_parts??0;message='optimization.checkProgress'
  if(run.state==='running')stage='optimization.checking'
 }else if(run.phase==='summaries'){
  done=(counts?.group?.processed??0)+(counts?.scene?.processed??0)
  total=(counts?.group?.total??0)+(counts?.scene?.total??0)
  message='optimization.metrics.summaryCount'
  if(run.state==='running')stage='optimization.summarizing'
 }else if(run.phase==='topics'){
  done=run.topic_completed_nodes??0;total=run.topic_total_nodes??0;message='optimization.metrics.topicCount'
  if(run.state==='running')stage='optimization.topics.discovering'
 }
 return <div className="optimization-workflow">
  <div className="optimization-status-summary" role="status"><strong>{t(stage)}</strong><span>{description}</span></div>
  {total>0?<div className="optimization-progress-line"><span>{t(message,{done,total})}</span><progress value={done} max={total} aria-label={t(stage)}/></div>:null}
  {children}
 </div>
}

export function OptimizationControls({controller,video,topicsSupported=false,onRequestTopics,regenerateTrigger=0,videoReading,onOriginal,onDetails,onModeChange,tools,children,actionsTarget}:Props){
 const {t}=useTranslation()
 const [open,setOpen]=useState(false)
 const [setup,setSetup]=useState<SetupAction>()
 const [readable,setReadable]=useState(false)
 const [requestTopics,setRequestTopics]=useState(false)
 const [deleting,setDeleting]=useState(false)
 const stateId=useId()
 const trigger=useRef(regenerateTrigger)
 const {status,error,busy,jobId}=controller
 const current=status?.candidate,published=status?.published
 const run=readable&&published?published:current??published
 const history=Boolean(current||published)
 const canGenerate=Boolean(!current||current.state==='failed'||current.requires_regeneration)
 const show=()=>{setSetup(undefined);setReadable(false);setRequestTopics(false);setOpen(true)}
 useEffect(()=>{
  if(trigger.current===regenerateTrigger)return
  trigger.current=regenerateTrigger
  setSetup('regenerate');setRequestTopics(true);setOpen(true)
 },[regenerateTrigger])
 const active=current&&['queued','running','pausing'].includes(current.state)
 const summaries=run?.summary_status
 const retrySummary=summaries?.state==='paused'||summaries?.state==='partial'
 const activeSummary=summaries?.state==='queued'||summaries?.state==='running'
 const keywords=(summaries?.counts.group?.keyword_ready??0)+(summaries?.counts.scene?.keyword_ready??0)
 const extracts=(summaries?.counts.group?.extractive_ready??0)+(summaries?.counts.scene?.extractive_ready??0)
 const action:SetupAction|undefined=!status?undefined:!history?'create':
  setup==='regenerate'&&canGenerate?'regenerate':setup==='resume'&&current?.budget_error?'resume':
  setup==='summaryRetry'&&retrySummary?'summaryRetry':undefined
 const description=run?.rewrite_mode?t('optimization.currentMode',{mode:t(`optimization.rewriteModes.${run.rewrite_mode}.label`)})+' · '+run.model:run?.model??''
 const openDraft=()=>{setOpen(false);onModeChange('optimized')}
 const close=()=>setOpen(false)
 const actions=<div className="optimization-actions">
  {jobId?<button className="button" aria-haspopup="dialog" aria-label={t('optimization.title')} aria-describedby={current?stateId:undefined} aria-expanded={open} onClick={show}>
   <Sparkles size={15}/>{t('optimization.title')}{current?<span id={stateId} className="optimization-trigger-state">{t(`optimization.states.${current.state}`)}</span>:null}
  </button>:null}
  {topicsSupported&&published&&(!published.topic_state||published.topic_state==='not_requested')&&published.calls_limit!=null?<button className="button" disabled={!status?.enabled||busy} onClick={onRequestTopics}>{t('optimization.topics.generate')}</button>:null}
  {children}
 </div>
 return <>
  <div className="optimization-toolbar">
   <div className="optimization-modes" role="group" aria-label={t('optimization.readingMode')}>
    <button aria-pressed={!controller.view} onClick={()=>onModeChange('original')}>{t('optimization.original')}</button>
    <button aria-pressed={controller.mode==='optimized'} disabled={!published} onClick={()=>onModeChange('optimized')}>{t('optimization.draft')}</button>
    <button aria-pressed={controller.compare} disabled={!published} onClick={()=>onModeChange('comparison')}>{t('optimization.compare')}</button>
   </div>
   {video?<div className="optimization-modes reading-layout-switch" role="group" aria-label={t('optimization.layout')}>
    <button aria-label={t('video.reading')} title={t('video.reading')} aria-pressed={videoReading} onClick={onOriginal}>{t('reading.pictureLayout')}</button>
    <button aria-label={t('optimization.textReading')} title={t('optimization.textReading')} aria-pressed={!videoReading} onClick={onDetails}>{t('reading.textLayout')}</button>
   </div>:null}
   {actionsTarget?createPortal(actions,actionsTarget):actions}
  </div>
  {tools?<div className="optimization-filter-row">{tools}</div>:null}
  {open?<Modal focusTitle className="optimization-mode-modal optimization-dialog" title={t('optimization.title')} closeLabel={t('common.actions.close')} onClose={close}>
   {error||controller.insightsError?<p className="error" role="alert">{error||controller.insightsError} <button className="button" onClick={controller.retry}>{t('common.actions.retry')}</button></p>:null}
   {status?.followup?<p role="status">{status.followup.error||t('uploadOptions.pending')}</p>:null}
   {!status&&!error?<p role="status">{t('common.states.loading')}</p>:null}
   {action&&jobId?<OptimizationSetup key={action} action={action} jobId={jobId} controller={controller}
    run={action==='resume'?current:published??current} requestTopics={requestTopics} topicsSupported={topicsSupported}
    onBack={history?()=>setSetup(undefined):undefined} onClose={close}/>:run&&jobId?<>
    <div className="optimization-dialog-body">
     {current&&published?<div className="optimization-run-switch" role="group" aria-label={t('optimization.metrics.version')}>
      <button aria-pressed={!readable} onClick={()=>setReadable(false)}>{t('optimization.metrics.current')}</button>
      <button aria-pressed={readable} onClick={()=>setReadable(true)}>{t('optimization.metrics.readable')}</button>
     </div>:null}
     {run.published?<div className="optimization-status-summary"><strong>{t('optimization.states.succeeded')}</strong><span>{description}</span></div>:<RunProgress run={run} description={description}>
      {active?<button className="button" disabled={busy||run.state==='pausing'} onClick={()=>void controller.action('pause')}>{t('optimization.pause')}</button>:null}
     </RunProgress>}
     <OptimizationUsage key={run.id} jobId={jobId} run={run}/>
     {run.error?<p className="error" role="alert">{run.error}</p>:null}
     {run.requires_regeneration?<p>{t('optimization.oldPolicy')}</p>:null}
     {run.published&&Boolean(run.needs_review_units||run.uncertain_units||run.unavailable_units)?<p className="optimization-attention">{t('optimization.checkWarnings',{facts:run.needs_review_units??0,uncertain:run.uncertain_units??0,unavailable:run.unavailable_units??0})}</p>:null}
     {run.published&&summaries?.version&&Boolean(activeSummary||retrySummary||keywords||extracts)?<div className="optimization-summary-report">
      {activeSummary||retrySummary?<><p>{t('optimization.summaryProgress',{
       done:(summaries.counts.group?.processed??0)+(summaries.counts.scene?.processed??0),
       total:(summaries.counts.group?.total??0)+(summaries.counts.scene?.total??0),
       failed:(summaries.counts.group?.failed??0)+(summaries.counts.scene?.failed??0)})}</p>
       <button className="button" disabled={busy||!status?.enabled} onClick={()=>{
        if(retrySummary&&run.calls_limit!=null)setSetup('summaryRetry')
        else void controller.action(activeSummary?'summaryPause':'summaryRetry')
       }}>{t(activeSummary?'optimization.pauseSummaries':'optimization.retrySummaries')}</button></>:null}
      {keywords||extracts?<small>{t('optimization.summaryFallbackCount',{keywords,source:extracts})}</small>:null}
     </div>:null}
     <details className="optimization-management"><summary>{t('optimization.metrics.manage')}</summary><button className="button danger-action" disabled={busy} onClick={()=>{setDeleting(true);close()}}>{t('optimization.delete')}</button></details>
    </div>
    <div className="optimization-dialog-footer">
     {!status?.enabled?<a className="button" href="#system" onClick={close}>{t('optimization.configure')}</a>:null}
     {canGenerate&&status?.enabled?<button className="button" disabled={busy} onClick={()=>setSetup('regenerate')}>{t('optimization.regenerate')}</button>:null}
     {!run.published&&!run.requires_regeneration&&['paused','failed'].includes(run.state)?<button className="primary" disabled={!status?.enabled||busy} onClick={()=>{
      if(run.budget_error)setSetup('resume')
      else void controller.action('resume')
     }}>{t(run.budget_error?'optimization.metrics.raiseAndResume':'optimization.resume')}</button>:null}
     {run.published?<button className="primary" onClick={openDraft}>{t('optimization.metrics.readDraft')}</button>:null}
    </div>
   </>:null}
  </Modal>:null}
  {deleting?<ConfirmDialog title={t('optimization.delete')} description={t('optimization.deleteHelp')} confirmLabel={t('optimization.delete')} busy={busy} danger
   onClose={()=>{setDeleting(false);show()}} onConfirm={()=>{void controller.action('delete').then(ok=>{setDeleting(false);if(!ok)show()})}}/>:null}
 </>
}
