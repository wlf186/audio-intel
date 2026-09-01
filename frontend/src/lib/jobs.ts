import type {JobSummary} from './types'
import type {TFunction} from 'i18next'

export const workspaceJobLimit=5

export function newestJobsFirst<T extends JobSummary>(jobs:readonly T[]):T[]{
 return [...jobs].sort((left,right)=>{
  const leftTime=Date.parse(left.created_at)
  const rightTime=Date.parse(right.created_at)
  return Number.isFinite(leftTime)&&Number.isFinite(rightTime)?rightTime-leftTime:0
 })
}

export function visibleWorkspaceJobs<T extends JobSummary>(jobs:readonly T[],selectedJobId?:string):T[]{
 const recent=jobs.slice(0,workspaceJobLimit)
 if(!selectedJobId||recent.some(job=>job.id===selectedJobId))return recent
 const selected=jobs.find(job=>job.id===selectedJobId)
 return selected?[...recent,selected]:recent
}

const stageCodes=new Set(['queued','starting','loading_tts_model','waiting_for_gpu','preparing_voice_clone','synthesis','writing_output','decoding_audio','vad','diarization','transcription','alignment','merging','completed','succeeded','failed','cancelled','cancelling'])
const unitCodes=new Set(['text_chunk','audio_chunk','codec_frame','output_token','model_layer','item','batch'])

export function progressPresentation(job:JobSummary,t:TFunction){
 const detail=job.progress_detail
 const estimated=detail?.basis==='estimated'
 const stageCode=detail?.stage_code||job.stage
 const stage=stageCodes.has(stageCode)?t(`jobs.stages.${stageCode}` as 'jobs.stages.queued'):stageCode.replaceAll('_',' ')
 const parts:string[]=[]
 if(detail?.current!==undefined&&detail.total!==undefined&&detail.unit){
  const unit=unitCodes.has(detail.unit)?t(`jobs.units.${detail.unit}` as 'jobs.units.item'):detail.unit
  parts.push(t('jobs.progress.count',{unit,current:detail.current,total:detail.total}))
 }
 const activity=detail?.activity
 if(activity){
  const count=activity.total===undefined?`${activity.current}`:`${activity.current}/${activity.total}`
  const unit=unitCodes.has(activity.unit)?t(`jobs.units.${activity.unit}` as 'jobs.units.item'):activity.unit
  const estimateMark=activity.basis==='estimated'&&activity.total!==undefined?t('jobs.progress.estimatedTotal'):''
  parts.push(t('jobs.progress.currentBatch',{count,unit,estimateMark}))
 }
 return {percent:Math.round(job.progress*100),estimated,stage,detail:parts.join(' · ')}
}
