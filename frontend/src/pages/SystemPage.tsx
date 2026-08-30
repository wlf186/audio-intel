import {BookOpen,LoaderCircle,RefreshCw} from 'lucide-react'
import {useEffect,useState} from 'react'
import type {Health} from '../lib/types'
import {systemPhase} from '../lib/systemStatus'
import {formatLocalDateTime,workerStateLabel} from '../lib/presentation'

function Meter({value,label}:{value:number;label:string}){const percent=Math.max(0,Math.min(100,Math.round(value)));return <div className="meter" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><div><b>{label}</b><span>{percent}%</span></div><i aria-hidden="true"><span style={{width:`${percent}%`}}/></i></div>}
const modelStateLabel=(state:Health['models'][number]['state'])=>({installed:'已安装',missing:'待下载',empty_marker:'标记为空',revision_mismatch:'版本不符',incomplete:'文件不完整'}[state])
const gib=(value=0)=>Math.round(value/1073741824)

function ModelGroup({label,models}:{label:string;models:Health['models']}){
 const installed=models.filter(model=>model.installed).length
 const hasIssue=installed!==models.length
 const [open,setOpen]=useState(hasIssue)
 useEffect(()=>{if(hasIssue)setOpen(true)},[hasIssue])
 if(!models.length)return null
 return <details className="model-group" open={open} onToggle={event=>setOpen(event.currentTarget.open)}><summary><span>{label}</span><strong>{installed} / {models.length} 已安装</strong>{hasIssue?<em>需要处理</em>:null}</summary><div className="model-list">{models.map(model=><div key={model.name}><i className={model.installed?'installed':''}/><span><b>{model.name}</b><small>{model.device}</small></span><em>{modelStateLabel(model.state)}</em></div>)}</div></details>
}

export function SystemPage({health,systemError,retry}:{health?:Health;systemError:string;retry:()=>Promise<void>}){
 const hardware=health?.hardware
 const memory=hardware?.memory_total?(hardware.memory_used||0)/hardware.memory_total*100:0
 const disk=hardware?.disk_total?(hardware.disk_used||0)/hardware.disk_total*100:0
 const gpu=hardware?.gpu
 const phase=systemPhase(health,systemError)
 const status=phase==='checking'?{tone:'checking',copy:'○ 正在检查服务状态'}:phase==='error'?{tone:'error',copy:'● 服务连接中断 · 状态未知'}:health?.offline?{tone:'ready',copy:'● 服务正常 · 离线模式已启用'}:{tone:'warning',copy:'● 服务正常 · 离线模式未启用'}
 const ttsModels=health?.models.filter(model=>/TTS/i.test(model.name))||[]
 const asrModels=health?.models.filter(model=>!/TTS/i.test(model.name))||[]
 return <section className="page-pad system hud-page" data-module="SYSTEM_CORE / SYS_04"><div className="page-heading"><div><h1 tabIndex={-1}>系统状态</h1><p>硬件资源、工作进程、模型与本地存储一览</p></div><div className="system-heading-actions"><span className="system-health" data-state={status.tone} role="status">{status.copy}</span><a className="button system-docs-link" href="/docs" target="_blank" rel="noreferrer"><BookOpen size={15}/>API 文档</a></div></div>{!health?<div className={`system-resource-state ${systemError?'error':'loading'}`} role={systemError?'alert':'status'}>{systemError?<RefreshCw/>:<LoaderCircle className="spin"/>}<h2>{systemError?'系统状态加载失败':'正在读取系统状态'}</h2><p>{systemError||'正在连接本地服务并读取硬件、模型和工作进程信息…'}</p>{systemError?<button className="button" onClick={()=>void retry()}>重新检查</button>:null}</div>:<><div className="resource-grid"><div className="resource-card"><small>CPU</small><strong>{Math.round(hardware?.cpu_percent||0)}%</strong><Meter value={hardware?.cpu_percent||0} label="CPU 使用率"/></div><div className="resource-card"><small>内存</small><strong>{gib(hardware?.memory_used)} / {gib(hardware?.memory_total)} GB</strong><Meter value={memory} label="系统内存"/></div><div className="resource-card"><small>GPU</small><strong>{gpu?`${gpu.memory_used_mib} / ${gpu.memory_total_mib} MiB`:'未检测到'}</strong><Meter value={gpu?.memory_total_mib?(gpu.memory_used_mib/gpu.memory_total_mib*100):0} label={gpu?.name||'GPU 不可用'}/></div><div className="resource-card"><small>项目磁盘</small><strong>{gib(hardware?.disk_used)} / {gib(hardware?.disk_total)} GB</strong><Meter value={disk} label="项目磁盘"/></div></div><div className="system-columns"><section><h2>模型状态</h2>{health.models.length?<div className="model-groups"><ModelGroup label="ASR 与公共组件" models={asrModels}/><ModelGroup label="TTS" models={ttsModels}/></div>:<p className="muted">未配置模型</p>}</section><section><h2>工作进程</h2><div className="worker-list">{health.workers.length?health.workers.map(worker=><div key={worker.id}><i className={worker.state!=='stopped'?'installed':''}/><span><b>{worker.kind.toUpperCase()} Worker</b><small>{worker.current_job_id||'等待任务'} · {formatLocalDateTime(worker.heartbeat_at)}</small></span><em>{workerStateLabel(worker.state)}</em></div>):<p className="muted">当前未发现工作进程</p>}</div><h2>本地目录</h2><div className="paths">{Object.entries(health.storage).map(([key,value])=><div key={key}><b>{key}</b><code>{value}</code></div>)}</div></section></div></>}</section>
}
