import type {ComputeCapability} from './types'

const localDateTimeFormatter=new Intl.DateTimeFormat('zh-CN',{
 year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false,
})

export function formatLocalDateTime(value?:string){
 if(!value)return '暂无心跳'
 const date=new Date(value)
 return Number.isNaN(date.getTime())?'时间未知':localDateTimeFormatter.format(date)
}

export function computeUnavailableReason(capability?:ComputeCapability,fallback='当前模型无法使用 GPU，本次自动使用 CPU。'){
 if(capability?.unavailable_reason_code==='insufficient_gpu_memory'){
  const required=capability.minimum_memory_mib
  const detected=capability.total_memory_mib
  if(required&&detected)return `该模型至少需要 ${required} MiB 显存，当前检测到 ${detected} MiB；本次自动使用 CPU。`
  if(required)return `该模型至少需要 ${required} MiB 显存；本次自动使用 CPU。`
 }
 return capability?.unavailable_reason||fallback
}

const workerStateLabels:Record<string,string>={idle:'空闲',running:'运行中',stopping:'正在停止',stopped:'已停止',starting:'正在启动',failed:'异常'}
export function workerStateLabel(value:string){return workerStateLabels[value]||value}
