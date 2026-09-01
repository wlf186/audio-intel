import type {ComputeCapability,JobSummary} from './types'
import type {TFunction} from 'i18next'

export function formatLocalDateTime(value:string|undefined,locale:string,t:TFunction){
 if(!value)return t('common.fallback.noHeartbeat')
 const date=new Date(value)
 if(Number.isNaN(date.getTime()))return t('common.fallback.unknownTime')
 return new Intl.DateTimeFormat(locale,{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(date)
}

export function computeUnavailableReason(capability:ComputeCapability|undefined,t:TFunction,fallback?:string){
 if(capability?.unavailable_reason_code==='gpu_runtime_not_installed')return t('errors.gpuRuntimeNotInstalled')
 if(capability?.unavailable_reason_code==='insufficient_gpu_memory'){
  const required=capability.minimum_memory_mib
  const detected=capability.total_memory_mib
  if(required&&detected)return t('errors.gpuMemoryDetected',{required,detected})
  if(required)return t('errors.gpuMemoryRequired',{required})
 }
 return capability?.unavailable_reason||fallback||t('errors.gpuFallback')
}

export function workerStateLabel(value:string,t:TFunction){
 return ['idle','running','stopping','stopped','starting','failed'].includes(value)?t(`system.workerStates.${value}` as 'system.workerStates.idle'):value
}

export type JobFailurePresentation={title:string;advice:string}
export function jobFailurePresentation(job:Pick<JobSummary,'error_code'|'error_message'>,t:TFunction):JobFailurePresentation{
 const code=(job.error_code||'').toLocaleLowerCase()
 const message=(job.error_message||'').toLocaleLowerCase()
 if(code.includes('outofmemory')||code.includes('memoryerror')||message.includes('out of memory')||message.includes('\u663e\u5b58\u4e0d\u8db3'))return{title:t('jobs.failure.memoryTitle'),advice:t('jobs.failure.memoryAdvice')}
 if(code==='workerprocessexit'||message.includes('worker process'))return{title:t('jobs.failure.workerTitle'),advice:t('jobs.failure.workerAdvice')}
 if(code.includes('valueerror')||code.includes('validation')||code.includes('invalid'))return{title:t('jobs.failure.inputTitle'),advice:t('jobs.failure.inputAdvice')}
 return{title:t('jobs.failure.defaultTitle'),advice:t('jobs.failure.defaultAdvice')}
}
