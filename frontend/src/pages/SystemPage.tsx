import {LoaderCircle,RefreshCw} from 'lucide-react'
import {useEffect,useState} from 'react'
import type {Health} from '../lib/types'
import {formatLocalDateTime,workerStateLabel} from '../lib/presentation'
import {useTranslation} from 'react-i18next'
import {resolvedLocale} from '../i18n'

function Meter({value,label}:{value:number;label:string}){const percent=Math.max(0,Math.min(100,Math.round(value)));return <div className="meter" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><div><b>{label}</b><span>{percent}%</span></div><i aria-hidden="true"><span style={{width:`${percent}%`}}/></i></div>}
const gib=(value=0)=>Math.round(value/1073741824)

function ModelGroup({label,models}:{label:string;models:Health['models']}){
 const {t}=useTranslation()
 const installed=models.filter(model=>model.installed).length
 const hasIssue=installed!==models.length
 const [open,setOpen]=useState(hasIssue)
 useEffect(()=>{if(hasIssue)setOpen(true)},[hasIssue])
 if(!models.length)return null
 return <details className="model-group" open={open} onToggle={event=>setOpen(event.currentTarget.open)}><summary><span>{label}</span><strong>{t('system.installedCount',{installed,total:models.length})}</strong>{hasIssue?<em>{t('system.needsAttention')}</em>:null}</summary><div className="model-list">{models.map(model=><div key={model.name}><i className={model.installed?'installed':''}/><span><b>{model.name}</b><small>{model.device}</small></span><em>{t(`system.modelStates.${model.state}`)}</em></div>)}</div></details>
}

export function SystemPage({health,systemError,retry}:{health?:Health;systemError:string;retry:()=>Promise<void>}){
 const {t}=useTranslation()
 const locale=resolvedLocale()
 const hardware=health?.hardware
 const memory=hardware?.memory_total?(hardware.memory_used||0)/hardware.memory_total*100:0
 const disk=hardware?.disk_total?(hardware.disk_used||0)/hardware.disk_total*100:0
 const gpu=hardware?.gpu
 const ttsModels=health?.models.filter(model=>/TTS/i.test(model.name))||[]
 const asrModels=health?.models.filter(model=>!/TTS/i.test(model.name))||[]
 if(!health)return <section className="page-pad system hud-page" data-module="SYSTEM_CORE / SYS_04"><div className="page-heading"><div><h1 tabIndex={-1}>{t('system.title')}</h1><p>{t('system.subtitle')}</p></div></div><div className={`system-resource-state ${systemError?'error':'loading'}`} role={systemError?'alert':'status'}>{systemError?<RefreshCw/>:<LoaderCircle className="spin"/>}<h2>{systemError?t('system.loadFailed'):t('system.loading')}</h2><p>{systemError||t('system.loadingDetail')}</p>{systemError?<button className="button" onClick={()=>void retry()}>{t('system.retry')}</button>:null}</div></section>
 return <section className="page-pad system hud-page" data-module="SYSTEM_CORE / SYS_04"><div className="page-heading"><div><h1 tabIndex={-1}>{t('system.title')}</h1><p>{t('system.subtitle')}</p></div></div>{health.deployment.profile==='cpu'?<div className="notice" role="status"><strong>{t('system.cpuProfile')}</strong><span>{t('system.cpuProfileDetail')}</span></div>:null}<div className="resource-grid"><div className="resource-card"><small>CPU</small><strong>{Math.round(hardware?.cpu_percent||0)}%</strong><Meter value={hardware?.cpu_percent||0} label={t('system.cpuUsage')}/></div><div className="resource-card"><small>{t('system.memory')}</small><strong>{gib(hardware?.memory_used)} / {gib(hardware?.memory_total)} GB</strong><Meter value={memory} label={t('system.systemMemory')}/></div><div className="resource-card"><small>GPU</small><strong>{gpu?`${gpu.memory_used_mib} / ${gpu.memory_total_mib} MiB`:t('system.gpuNotDetected')}</strong>{gpu?.memory_free_mib!==undefined?<p className="gpu-memory-detail">{t('system.gpuAvailable',{value:gpu.memory_free_mib})}{gpu.memory_system_reserved_mib!==undefined?<span title={t('system.gpuReservedHint')}> · {t('system.gpuReserved',{value:gpu.memory_system_reserved_mib})}</span>:null}</p>:null}<Meter value={gpu?.memory_total_mib?(gpu.memory_used_mib/gpu.memory_total_mib*100):0} label={gpu?.name||t('system.gpuUnavailable')}/></div><div className="resource-card"><small>{t('system.projectDisk')}</small><strong>{gib(hardware?.disk_used)} / {gib(hardware?.disk_total)} GB</strong><Meter value={disk} label={t('system.projectDisk')}/></div></div><div className="system-columns"><section><h2>{t('system.modelStatus')}</h2>{health.models.length?<div className="model-groups"><ModelGroup label={t('system.asrComponents')} models={asrModels}/><ModelGroup label="TTS" models={ttsModels}/></div>:<p className="muted">{t('system.noModels')}</p>}</section><section><h2>{t('system.workers')}</h2><div className="worker-list">{health.workers.length?health.workers.map(worker=><div key={worker.id}><i className={worker.state!=='stopped'?'installed':''}/><span><b>{worker.kind.toUpperCase()} Worker</b><small>{worker.current_job_id||t('system.waiting')} · {formatLocalDateTime(worker.heartbeat_at,locale,t)}</small></span><em>{workerStateLabel(worker.state,t)}</em></div>):<p className="muted">{t('system.noWorkers')}</p>}</div><h2>{t('system.localDirectories')}</h2><div className="paths">{Object.entries(health.storage).map(([key,value])=><div key={key}><b>{key}</b><code>{value}</code></div>)}</div></section></div></section>
}
