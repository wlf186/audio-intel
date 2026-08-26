import type {Job} from '../lib/types'
const labels:Record<string,string>={queued:'等待处理',running:'正在处理',succeeded:'已完成',failed:'处理失败',cancelled:'已取消'}
function compactSeconds(value:number){if(value<60)return `${Math.max(1,Math.round(value))} 秒`;if(value<3600)return `${Math.max(1,Math.round(value/60))} 分钟`;return `${(value/3600).toFixed(1)} 小时`}
function detail(job:Job){
 const parts:string[]=[]
 if(job.state==='queued'&&job.queue?.position)parts.push(`队列第 ${job.queue.position} / ${job.queue.depth}`)
 if(job.queue?.waiting_for==='gpu')parts.push('等待 GPU 资源')
 const range=job.estimate?.remaining_seconds
 if(range)parts.push(`预计剩余 ${compactSeconds(range.lower)}–${compactSeconds(range.upper)}`)
 else if(job.estimate?.state==='warming_up'&&['queued','running'].includes(job.state))parts.push(`ETA 学习中 · ${job.estimate.sample_count}/5 样本`)
 return parts.join(' · ')
}
export function JobMini({job,onOpen,isSelected=false}:{job:Job;onOpen?:(job:Job)=>void;isSelected?:boolean}){const device=String(job.request.compute_device||job.result?.compute_device||(job.kind==='asr'?'gpu':'cpu')).toUpperCase();const stopping=job.state==='running'&&job.stage==='cancelling';const estimate=detail(job);return <button className={`job-mini ${job.state}${isSelected?' selected':''}`} aria-current={isSelected?'true':undefined} onClick={()=>onOpen?.(job)}><div><span className={`state-dot ${job.state}`}/><b>{job.display_name}</b><em>{device} · {Math.round(job.progress*100)}%</em></div><p>{stopping?'正在安全停止':labels[job.state]} · {stopping?'正在释放计算资源':job.stage.replaceAll('_',' ')}</p>{!stopping&&estimate?<small className="queue-estimate">{estimate}</small>:null}<span className="progress"><i style={{width:`${job.progress*100}%`}}/></span></button>}
