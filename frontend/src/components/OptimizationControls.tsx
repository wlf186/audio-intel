import {useEffect,useState,type ReactNode} from 'react'
import {useTranslation} from 'react-i18next'
import {Sparkles} from 'lucide-react'
import type {OptimizationController} from '../hooks/useOptimization'
import {ConfirmDialog} from './ConfirmDialog'
import {Modal} from './Modal'
import {request} from '../lib/api'
import {optimizationSettingsPath,needsServiceNotice,type OptimizationConfiguration,readRewriteMode,saveRewriteMode,rewriteModes,type RewriteMode} from '../lib/optimization'
import type {ReadingMode} from '../lib/workspaceSession'
import './optimization.css'

export function OptimizationControls({controller,video,videoReading,onOriginal,onDetails,onModeChange,tools,children}:{controller:OptimizationController;video:boolean;videoReading:boolean;onOriginal:()=>void;onDetails:()=>void;onModeChange:(mode:ReadingMode)=>void;tools?:ReactNode;children?:ReactNode}){
 const {t}=useTranslation()
 const [open,setOpen]=useState(false)
 const [connection,setConnection]=useState<OptimizationConfiguration>()
 const [connectionError,setConnectionError]=useState('')
 const [connectionRetry,setConnectionRetry]=useState(0)
 useEffect(()=>{if(!open)return;const abort=new AbortController();setConnection(undefined);setConnectionError('');void request<OptimizationConfiguration>(optimizationSettingsPath,{signal:abort.signal}).then(setConnection).catch(error=>{if(!abort.signal.aborted)setConnectionError((error as Error).message)});return()=>abort.abort()},[open,connectionRetry])
 const [deleting,setDeleting]=useState(false)
 const [rewriteMode,setRewriteMode]=useState<RewriteMode>(readRewriteMode)
 const {status,error,busy,view}=controller
 const current=status?.candidate,published=status?.published
 const active=current&&['queued','running','pausing'].includes(current.state)
 const checking=current?.phase==='review'
 const summarizing=current?.phase==='summaries'
 const counts=current?.summary_status?.counts
 const summaryDone=(counts?.group?.processed||0)+(counts?.scene?.processed||0)
 const summaryTotal=(counts?.group?.total||0)+(counts?.scene?.total||0)
 const publishedSummaries=published?.summary_status
 const auxActive=publishedSummaries&&['queued','running'].includes(publishedSummaries.state)
 const missingSummaries=publishedSummaries&&['paused','partial'].includes(publishedSummaries.state)
 const summaryProgress=(value:typeof publishedSummaries)=>t('optimization.summaryProgress',{done:(value?.counts.group?.processed||0)+(value?.counts.scene?.processed||0),total:(value?.counts.group?.total||0)+(value?.counts.scene?.total||0),failed:(value?.counts.group?.failed||0)+(value?.counts.scene?.failed||0)})
 const fallbackCounts=publishedSummaries?.counts
 const keywordCount=(fallbackCounts?.group?.keyword_ready||0)+(fallbackCounts?.scene?.keyword_ready||0)
 const sourceCount=(fallbackCounts?.group?.extractive_ready||0)+(fallbackCounts?.scene?.extractive_ready||0)
 const canGenerate=status?.enabled&&!busy&&(!current||current.state==='failed'||current.requires_regeneration)
 const action=current||published?'regenerate':'create'
 const progress=current?<div className="optimization-workflow" role="status">
  <div><strong>{t(current.state==='running'&&summarizing?'optimization.summarizing':current.state==='running'&&checking?'optimization.checking':`optimization.states.${current.state}`)}</strong><span>{summarizing?t('optimization.summaryProgress',{done:summaryDone,total:summaryTotal,failed:(counts?.group?.failed||0)+(counts?.scene?.failed||0)}):checking?t('optimization.checkProgress',{done:current.reviewed_parts||0,total:current.total_parts||0}):t('optimization.characterProgress',{done:current.completed_chars,total:current.total_chars})}</span></div>
  <progress value={summarizing?summaryDone:checking?current.reviewed_parts||0:current.completed_chars} max={Math.max(1,summarizing?summaryTotal:checking?current.total_parts||0:current.total_chars)}/>
  {active?<button className="button" disabled={busy||current.state==='pausing'} onClick={()=>void controller.action('pause')}>{t('optimization.pause')}</button>:<button className="button" disabled={busy||!status?.enabled||current.requires_regeneration} onClick={()=>void controller.action('resume')}>{t('optimization.resume')}</button>}
  {current.error?<p className="error">{current.error}</p>:null}
 </div>:null
 return <>
  <div className="optimization-toolbar">
   <div className="optimization-modes" role="group" aria-label={t('optimization.readingMode')}>
    <button aria-pressed={!view} onClick={()=>onModeChange('original')}>{t('optimization.original')}</button>
    <button aria-pressed={controller.mode==='optimized'} disabled={!published} onClick={()=>onModeChange('optimized')}>{t('optimization.draft')}</button>
    <button aria-pressed={controller.compare} disabled={!published} onClick={()=>onModeChange('comparison')}>{t('optimization.compare')}</button>
   </div>
   {video?<div className="optimization-modes reading-layout-switch" role="group" aria-label={t('optimization.layout')}><button aria-pressed={videoReading} onClick={onOriginal}>{t('video.reading')}</button><button aria-pressed={!videoReading} onClick={onDetails}>{t('optimization.textReading')}</button></div>:null}
   <div className="optimization-actions"><button className="button" disabled={!status} onClick={()=>{setRewriteMode(published?.rewrite_mode||readRewriteMode());setOpen(true)}}><Sparkles size={15}/>{t('optimization.title')}</button>{children}</div>
  </div>
  <div className="optimization-filter-row">{tools}</div>
  {progress}
  {published?<div className="optimization-status-summary" role="status"><span>{t('optimization.states.succeeded')} · {t('optimization.currentMode',{mode:t(`optimization.rewriteModes.${published.rewrite_mode||'rewrite'}.label`)})}</span>{publishedSummaries?.version?<span>{summaryProgress(publishedSummaries)}{keywordCount||sourceCount?` · ${t('optimization.summaryFallbackCount',{keywords:keywordCount,source:sourceCount})}`:''}{auxActive||missingSummaries?` · ${t(publishedSummaries.state==='paused'?'optimization.summaryStates.paused':publishedSummaries.state==='partial'?'optimization.summaryStates.partial':publishedSummaries.state==='queued'?'optimization.summaryStates.queued':'optimization.summaryStates.running')}`:''}</span>:<span>{t('optimization.legacySummaries')}</span>}{auxActive||missingSummaries?<button className="button" disabled={busy||!status?.enabled} onClick={()=>void controller.action(auxActive?'summaryPause':'summaryRetry')}>{t(auxActive?'optimization.pauseSummaries':'optimization.retrySummaries')}</button>:null}{Boolean(published.needs_review_units||published.uncertain_units||published.unavailable_units)?<details><summary>{t('optimization.checkDetails')}</summary><p>{t('optimization.checkWarnings',{facts:published.needs_review_units||0,uncertain:published.uncertain_units||0,unavailable:published.unavailable_units||0})}</p></details>:null}</div>:null}
  {controller.insightsError?<p className="error" role="alert">{controller.insightsError} <button className="button" onClick={controller.retry}>{t('common.actions.retry')}</button></p>:null}
  {error?<p className="error" role="alert">{error} <button className="button" onClick={controller.retry}>{t('common.actions.retry')}</button></p>:null}
  {open?<Modal className="optimization-mode-modal" title={t('optimization.title')} closeLabel={t('common.actions.close')} onClose={()=>setOpen(false)}>
   <div className="optimization-dialog-intro"><strong>{t(published?'optimization.regenerate':'optimization.begin')}</strong><p>{t(published?'optimization.regenerateHelp':'optimization.workflowHelp')}</p>{connection?<small>{t('optimization.model')}: {connection.model} · {connection.base_url}</small>:connectionError?<p role="alert">{connectionError} <button className="button" onClick={()=>setConnectionRetry(n=>n+1)}>{t('common.actions.retry')}</button></p>:<p role="status">{t('common.states.loading')}</p>}<small>{t('optimization.summaryHelp')}</small>{video?<small>{t('optimization.sceneScope')}</small>:null}</div>
   {connection&&needsServiceNotice(connection.base_url)?<p className="optimization-service-notice">{t('optimization.serviceNotice')}</p>:null}
   {current?progress:null}
   <fieldset className="optimization-mode-choices" disabled={Boolean(current&&!canGenerate)}><legend>{t('optimization.chooseMode')}</legend>{rewriteModes.map(mode=><label key={mode} className={rewriteMode===mode?'selected':''}><input type="radio" name="rewrite-mode" value={mode} checked={rewriteMode===mode} onChange={()=>{setRewriteMode(mode);saveRewriteMode(mode)}}/><span><strong>{t(`optimization.rewriteModes.${mode}.label`)}</strong><small>{t(`optimization.rewriteModes.${mode}.help`)}</small></span></label>)}</fieldset>
   {status&&!status.enabled?<a className="button" href="#system" onClick={()=>setOpen(false)}>{t('optimization.configure')}</a>:null}
   <div className="modal-actions">{current||published?<details className="optimization-management"><summary>{t('optimization.more')}</summary><button className="button danger-action" disabled={busy} onClick={()=>{setOpen(false);setDeleting(true)}}>{t('optimization.delete')}</button></details>:null}<button className="button" onClick={()=>setOpen(false)}>{t('common.actions.cancel')}</button><button className="primary" disabled={!canGenerate||!connection?.enabled} onClick={()=>{void controller.action(action,rewriteMode);setOpen(false)}}>{t(published||current?'optimization.regenerate':'optimization.begin')}</button></div>
  </Modal>:null}
  {deleting?<ConfirmDialog title={t('optimization.delete')} description={t('optimization.deleteHelp')} confirmLabel={t('optimization.delete')} busy={busy} danger onClose={()=>setDeleting(false)} onConfirm={()=>{void controller.action('delete');setDeleting(false)}}/>:null}
 </>
}
