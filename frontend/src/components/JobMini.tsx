import type {JobSummary} from '../lib/types'
import {progressPresentation} from '../lib/jobs'
const labels:Record<string,string>={queued:'等待处理',running:'正在处理',succeeded:'已完成',failed:'处理失败',cancelled:'已取消'}
function compactSeconds(value:number){if(value<60)return `${Math.max(1,Math.round(value))} 秒`;if(value<3600)return `${Math.max(1,Math.round(value/60))} 分钟`;return `${(value/3600).toFixed(1)} 小时`}
function detail(job:JobSummary){
 const parts:string[]=[]
 if(job.state==='queued'&&job.queue?.position)parts.push(`队列第 ${job.queue.position} / ${job.queue.depth}`)
 if(job.queue?.waiting_for==='gpu')parts.push('等待 GPU 资源')
 const range=job.estimate?.remaining_seconds
 if(range)parts.push(`预计剩余 ${compactSeconds(range.lower)}–${compactSeconds(range.upper)}`)
 else if(job.estimate?.state==='warming_up'&&['queued','running'].includes(job.state))parts.push(`ETA 学习中 · ${job.estimate.sample_count}/5 样本`)
 return parts.join(' · ')
}
export function JobMini({job,onOpen,isSelected=false}:{job:JobSummary;onOpen?:(job:JobSummary)=>void;isSelected?:boolean}){const device=String(job.compute_device||(job.kind==='asr'?'gpu':'cpu')).toUpperCase();const stopping=job.state==='running'&&job.stage==='cancelling';const estimate=detail(job);const live=progressPresentation(job);return <button className={`job-mini ${job.state}${isSelected?' selected':''}`} aria-current={isSelected?'true':undefined} onClick={()=>onOpen?.(job)}><div><span className={`state-dot ${job.state}`}/><b>{job.display_name}</b><em>{device} · {live.percent}%{live.estimated?' 估算':''}</em></div><p>{stopping?'正在安全停止':labels[job.state]} · {stopping?'正在释放计算资源':live.stage}</p>{!stopping&&live.detail?<small className="progress-activity">{live.detail}</small>:null}{!stopping&&estimate?<small className="queue-estimate">{estimate}</small>:null}<span className="progress"><i style={{width:`${job.progress*100}%`}}/></span></button>}
