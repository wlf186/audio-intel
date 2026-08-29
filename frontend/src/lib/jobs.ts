import type {JobSummary} from './types'

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

const stageLabels:Record<string,string>={
 queued:'等待处理',starting:'正在启动',loading_tts_model:'加载 TTS 模型',waiting_for_gpu:'等待 GPU',
 preparing_voice_clone:'准备克隆音色',synthesis:'语音合成',writing_output:'写入结果',
 decoding_audio:'解析音频',vad:'语音活动检测',diarization:'说话人分离',transcription:'语音转写',
 alignment:'字词对齐',merging:'合并说话人与时间戳',completed:'已完成',succeeded:'已完成',
 failed:'处理失败',cancelled:'已取消',cancelling:'正在释放计算资源',
}
const unitLabels:Record<string,string>={text_chunk:'文本分块',audio_chunk:'音频分块',codec_frame:'codec 帧',output_token:'输出 token',model_layer:'模型层',item:'处理单元',batch:'批次'}

export function progressPresentation(job:JobSummary){
 const detail=job.progress_detail
 const estimated=detail?.basis==='estimated'
 const stageCode=detail?.stage_code||job.stage
 const stage=stageLabels[stageCode]||stageCode.replaceAll('_',' ')
 const parts:string[]=[]
 if(detail?.current!==undefined&&detail.total!==undefined&&detail.unit){
  parts.push(`${unitLabels[detail.unit]||detail.unit} ${detail.current}/${detail.total}`)
 }
 const activity=detail?.activity
 if(activity){
  const count=activity.total===undefined?`${activity.current}`:`${activity.current}/${activity.total}`
  const estimateMark=activity.basis==='estimated'&&activity.total!==undefined?'（总量估算）':''
  parts.push(`当前批次 ${count} ${unitLabels[activity.unit]||activity.unit}${estimateMark}`)
 }
 return {percent:Math.round(job.progress*100),estimated,stage,detail:parts.join(' · ')}
}
