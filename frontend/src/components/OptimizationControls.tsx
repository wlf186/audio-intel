import {useState,type ReactNode} from 'react'
import {useTranslation} from 'react-i18next'
import {Sparkles,MoreHorizontal} from 'lucide-react'
import type {OptimizationController} from '../hooks/useOptimization'
import {ActionPopover} from './ActionPopover'
import {ConfirmDialog} from './ConfirmDialog'
import {Modal} from './Modal'
import {readRewriteMode,saveRewriteMode,rewriteModes,type RewriteMode} from '../lib/optimization'
import type {ReadingMode} from '../lib/workspaceSession'
import './optimization.css'

export function OptimizationControls({controller,video,videoReading,onOriginal,onDetails,onModeChange,tools,children}:{controller:OptimizationController;video:boolean;videoReading:boolean;onOriginal:()=>void;onDetails:()=>void;onModeChange:(mode:ReadingMode)=>void;tools?:ReactNode;children?:ReactNode}){
 const {t}=useTranslation()
 const [confirm,setConfirm]=useState<'delete'|'regenerate'|'create'>()
 const [rewriteMode,setRewriteMode]=useState<RewriteMode>(readRewriteMode)
 const {status,error,busy,view}=controller
 const current=status?.candidate
 const available=Boolean(status?.published||current?.completed_units)
 const active=current&&['queued','running','pausing'].includes(current.state)
 return <>
  <div className="optimization-toolbar">
   <div className="optimization-modes" role="group" aria-label={t('optimization.readingMode')}>
    <button aria-pressed={!view} onClick={()=>onModeChange('original')}>{t('optimization.original')}</button>
    <button aria-pressed={controller.mode==='optimized'} disabled={!available} onClick={()=>onModeChange('optimized')}>{t('optimization.draft')}</button>
    <button aria-pressed={controller.compare} disabled={!available} onClick={()=>onModeChange('comparison')}>{t('optimization.compare')}</button>
   </div>
   {tools}
   <div className="optimization-actions">
    {status?.enabled&&!status.published&&!current?<button className="button" disabled={!status?.enabled||busy} onClick={()=>{setRewriteMode(readRewriteMode());setConfirm('create')}}><Sparkles size={15}/>{t('optimization.start')}</button>:null}
    {video?<button className="button" aria-pressed={!videoReading} onClick={()=>{videoReading?onDetails():onOriginal()}}>{t(videoReading?'optimization.textReading':'video.reading')}</button>:null}
    {current||status?.published?<ActionPopover label={t('optimization.more')} icon={<MoreHorizontal size={18}/>} className="optimization-more" closeOnAction><button className="button" disabled={busy||!status?.enabled||Boolean(current&&current.state!=='failed'&&!current.requires_regeneration)} onClick={()=>{setRewriteMode(readRewriteMode());setConfirm('regenerate')}}>{t('optimization.regenerate')}</button><button className="button danger-action" disabled={busy} onClick={()=>setConfirm('delete')}>{t('optimization.delete')}</button></ActionPopover>:null}
    {status&&!status.enabled?<a className="button" href="#system">{t('optimization.configure')}</a>:null}
   </div>
   {children}
  </div>
  {current?<div className="optimization-progress" role="status"><div><strong>{t(`optimization.states.${current.state}`)}</strong><span>{t('optimization.progress',{done:current.completed_units,total:current.total_units,percent:Math.floor(current.progress*100)})}</span>{current.needs_review_units?<small>{t('optimization.retainedProgress',{count:current.needs_review_units})}</small>:null}{current.state==='running'?<small>{current.activity_chars?t('optimization.generating',{count:current.activity_chars}):t('optimization.waitingModel')}</small>:null}</div><progress value={current.completed_chars} max={Math.max(1,current.total_chars)}/><div className="optimization-progress-actions">{active?<button className="button" disabled={busy||current.state==='pausing'} onClick={()=>void controller.action('pause')}>{t('optimization.pause')}</button>:current.state==='paused'||current.state==='failed'?<button className="button" disabled={busy||!status?.enabled||current.requires_regeneration} onClick={()=>void controller.action('resume')}>{t('optimization.resume')}</button>:null}{status?.published&&current.completed_units>0?<button className="button" onClick={controller.previewCandidate}>{t('optimization.preview')}</button>:null}{status?.published&&controller.selected?.id===current.id?<button className="button" onClick={controller.showPublished}>{t('optimization.published')}</button>:null}</div>{current.requires_regeneration?<p className="notice">{t('optimization.oldPolicy')}</p>:null}{current.error?<p className="error">{current.error}</p>:null}</div>:null}

  {status?.published?.review_state?<div className="optimization-review-progress" role="status"><span><strong>{t('optimization.readyForReading')}</strong> · {t(`optimization.reviewStates.${status.published.review_state}`)} <small>{t('optimization.reviewProgress',{done:status.published.reviewed_units||0,total:status.published.review_total_units||status.published.total_units})}</small>{status.published.uncertain_units?<small> · {t('optimization.uncertainCount',{count:status.published.uncertain_units})}</small>:null}{status.published.unavailable_units?<small> · {t('optimization.unavailableCount',{count:status.published.unavailable_units})}</small>:null}{status.published.basic_cleanup_units?<small> · {t('optimization.basicCount',{count:status.published.basic_cleanup_units})}</small>:null}</span>{['queued','running'].includes(status.published.review_state)?<button className="button" disabled={busy} onClick={()=>void controller.action('pause','rewrite','review')}>{t('optimization.pauseReview')}</button>:(['paused','unavailable'].includes(status.published.review_state)||status.published.review_state==='completed'&&Boolean(status.published.unavailable_units))?<button className="button" disabled={busy||!status.enabled} onClick={()=>void controller.action('resume','rewrite','review')}>{t(status.published.review_state==='completed'?'optimization.retryUnavailableReview':'optimization.resumeReview')}</button>:null}</div>:null}
  {error?<p className="error" role="alert">{error} <button className="button" onClick={controller.retry}>{t('common.actions.retry')}</button></p>:null}
  {confirm==='delete'?<ConfirmDialog title={t('optimization.delete')} description={t('optimization.deleteHelp')} confirmLabel={t('optimization.delete')} busy={busy} danger onClose={()=>setConfirm(undefined)} onConfirm={()=>{void controller.action('delete');setConfirm(undefined)}}/>:confirm?<Modal className="optimization-mode-modal" title={t(confirm==='create'?'optimization.start':'optimization.regenerate')} closeLabel={t('common.actions.close')} onClose={()=>setConfirm(undefined)}>
   <fieldset className="optimization-mode-choices"><legend>{t('optimization.chooseMode')}</legend>{rewriteModes.map(mode=><label key={mode}><input type="radio" name="rewrite-mode" value={mode} checked={rewriteMode===mode} onChange={()=>{setRewriteMode(mode);saveRewriteMode(mode)}}/><span><strong>{t(`optimization.rewriteModes.${mode}.label`)}</strong><small>{t(`optimization.rewriteModes.${mode}.help`)}</small></span></label>)}</fieldset>
   {confirm==='regenerate'?<p>{t('optimization.regenerateHelp')}</p>:null}
   <div className="modal-actions"><button className="button" onClick={()=>setConfirm(undefined)}>{t('common.actions.cancel')}</button><button className="primary" disabled={busy} onClick={()=>{void controller.action(confirm,rewriteMode);setConfirm(undefined)}}>{t(confirm==='create'?'optimization.begin':'optimization.regenerate')}</button></div>
  </Modal>:null}
 </>
}
