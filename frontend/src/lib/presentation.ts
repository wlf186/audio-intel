import type {ComputeCapability,JobSummary} from './types'

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

export type JobFailurePresentation={title:string;advice:string}
export function jobFailurePresentation(job:Pick<JobSummary,'error_code'|'error_message'>):JobFailurePresentation{
 const code=(job.error_code||'').toLocaleLowerCase()
 const message=(job.error_message||'').toLocaleLowerCase()
 if(code.includes('outofmemory')||code.includes('memoryerror')||message.includes('out of memory')||message.includes('显存不足'))return{title:'显存或内存不足',advice:'释放计算资源后重试，或重新提交为 CPU / 较小模型。'}
 if(code==='workerprocessexit'||message.includes('worker process'))return{title:'工作进程异常退出',advice:'请先检查系统状态与服务日志，再重试任务。'}
 if(code.includes('valueerror')||code.includes('validation')||code.includes('invalid'))return{title:'输入或任务参数无法处理',advice:'请检查输入文件和任务设置后重新提交。'}
 return{title:'任务处理失败',advice:'可以重试；若持续失败，请查看技术详情和服务日志。'}
}
