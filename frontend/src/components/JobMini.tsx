import type {JobSummary} from '../lib/types'
import {progressPresentation} from '../lib/jobs'
import {useTranslation} from 'react-i18next'
import type {TFunction} from 'i18next'
function compactSeconds(value:number,t:TFunction){if(value<60)return t('common.units.seconds',{count:Math.max(1,Math.round(value))});if(value<3600)return t('common.units.minutes',{count:Math.max(1,Math.round(value/60))});return t('common.units.hours',{count:(value/3600).toFixed(1)})}
function detail(job:JobSummary,t:TFunction){
 const parts:string[]=[]
 if(job.state==='queued'&&job.queue?.position)parts.push(t('jobs.mini.queue',{position:job.queue.position,depth:job.queue.depth}))
 if(job.queue?.waiting_for==='gpu')parts.push(t('jobs.mini.waitingGpu'))
 const range=job.estimate?.remaining_seconds
 if(range)parts.push(t('jobs.mini.remaining',{lower:compactSeconds(range.lower,t),upper:compactSeconds(range.upper,t)}))
 else if(job.estimate?.state==='warming_up'&&['queued','running'].includes(job.state))parts.push(t('jobs.mini.warming',{count:job.estimate.sample_count}))
 return parts.join(' · ')
}
export function JobMini({job,onOpen,isSelected=false}:{job:JobSummary;onOpen?:(job:JobSummary)=>void;isSelected?:boolean}){const {t}=useTranslation();const device=String(job.compute_device||(job.kind==='asr'?'gpu':'cpu')).toUpperCase();const stopping=job.state==='running'&&job.stage==='cancelling';const estimate=detail(job,t);const live=progressPresentation(job,t);return <button className={`job-mini ${job.state}${isSelected?' selected':''}`} aria-current={isSelected?'true':undefined} onClick={()=>onOpen?.(job)}><div><span className={`state-dot ${job.state}`}/><b>{job.display_name}</b><em>{device} · {live.percent}%{live.estimated?` ${t('jobs.estimated')}`:''}</em></div><p>{stopping?t('jobs.safelyStopping'):t(`jobs.states.${job.state}`)} · {stopping?t('jobs.stages.cancelling'):live.stage}</p>{!stopping&&live.detail?<small className="progress-activity">{live.detail}</small>:null}{!stopping&&estimate?<small className="queue-estimate">{estimate}</small>:null}<span className="progress"><i style={{width:`${job.progress*100}%`}}/></span></button>}
