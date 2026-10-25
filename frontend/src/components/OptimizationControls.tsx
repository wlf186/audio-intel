import {useState,type ReactNode} from 'react'
import {useTranslation} from 'react-i18next'
import {Sparkles} from 'lucide-react'
import type {OptimizationController} from '../hooks/useOptimization'
import {ActionPopover} from './ActionPopover'
import {ConfirmDialog} from './ConfirmDialog'
import './optimization.css'

export function OptimizationControls({controller,video,videoReading,onOriginal,onDetails,children}:{controller:OptimizationController;video:boolean;videoReading:boolean;onOriginal:()=>void;onDetails:()=>void;children?:ReactNode}){
 const {t}=useTranslation()
 const [confirm,setConfirm]=useState<'delete'|'regenerate'>()
 const {status,error,busy,view}=controller
 const current=status?.candidate
 const available=Boolean(status?.published||current?.completed_units)
 const active=current&&['queued','running','pausing'].includes(current.state)
 return <>
  <div className="optimization-toolbar">
   <div className="optimization-modes" role="group" aria-label={t('optimization.readingMode')}>
    <button aria-pressed={!view&&(!video||videoReading)} onClick={()=>{controller.setOptimized(false);onOriginal()}}>{t('optimization.original')}</button>
    <button aria-pressed={view} disabled={!available} onClick={()=>controller.setOptimized(true)}>{t('optimization.draft')}</button>
    {video?<button aria-pressed={!view&&!videoReading} onClick={()=>{controller.setOptimized(false);onDetails()}}>{t('video.details')}</button>:null}
   </div>
   <div className="optimization-actions">
    {status?.enabled&&!status.published&&!current?<button className="button" disabled={!status?.enabled||busy} onClick={()=>void controller.action('create')}><Sparkles size={15}/>{t('optimization.start')}</button>:null}
    {available?<button className="button" onClick={()=>controller.setCompare(true)}>{t('optimization.compare')}</button>:null}
    {current||status?.published?<ActionPopover label={t('optimization.more')} closeOnAction><button className="button" disabled={busy||!status?.enabled||Boolean(current&&current.state!=='failed')} onClick={()=>setConfirm('regenerate')}>{t('optimization.regenerate')}</button><button className="button danger-action" disabled={busy} onClick={()=>setConfirm('delete')}>{t('optimization.delete')}</button></ActionPopover>:null}
    {status&&!status.enabled?<a className="button" href="#system">{t('optimization.configure')}</a>:null}
   </div>
   {!view?children:null}
  </div>
  {current?<div className="optimization-progress" role="status"><div><strong>{t(`optimization.states.${current.state}`)}</strong><span>{t('optimization.progress',{done:current.completed_units,total:current.total_units,percent:Math.floor(current.progress*100)})}</span>{current.state==='running'?<small>{current.activity_chars?t('optimization.generating',{count:current.activity_chars}):t('optimization.waitingModel')}</small>:null}</div><progress value={current.completed_chars} max={Math.max(1,current.total_chars)}/><div className="optimization-progress-actions">{active?<button className="button" disabled={busy||current.state==='pausing'} onClick={()=>void controller.action('pause')}>{t('optimization.pause')}</button>:current.state==='paused'||current.state==='failed'?<button className="button" disabled={busy||!status?.enabled} onClick={()=>void controller.action('resume')}>{t('optimization.resume')}</button>:null}{status?.published&&current.completed_units>0?<button className="button" onClick={controller.previewCandidate}>{t('optimization.preview')}</button>:null}{status?.published&&controller.selected?.id===current.id?<button className="button" onClick={controller.showPublished}>{t('optimization.published')}</button>:null}</div>{current.error?<p className="error">{current.error}</p>:null}</div>:null}
  {error?<p className="error" role="alert">{error} <button className="button" onClick={controller.retry}>{t('common.actions.retry')}</button></p>:null}
  {confirm?<ConfirmDialog title={t(confirm==='delete'?'optimization.delete':'optimization.regenerate')} description={t(confirm==='delete'?'optimization.deleteHelp':'optimization.regenerateHelp')} confirmLabel={t(confirm==='delete'?'optimization.delete':'optimization.regenerate')} busy={busy} danger={confirm==='delete'} onClose={()=>setConfirm(undefined)} onConfirm={()=>{void controller.action(confirm);setConfirm(undefined)}}/>:null}
 </>
}
