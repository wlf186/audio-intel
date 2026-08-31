import {XCircle} from 'lucide-react'
import {size,type SubmissionProgress as Progress} from '../lib/api'

type Props={label:string;progress:Progress;onCancel?:()=>void}

export function SubmissionProgress({label,progress,onCancel}:Props){
 const uploading=progress.phase==='uploading'
 const title=progress.phase==='preparing'?`正在准备上传${label}`:uploading?`正在上传${label}`:`${label}上传完成，正在创建任务`
 const transferred=progress.totalBytes?`${size(progress.loadedBytes)} / ${size(progress.totalBytes)}`:progress.loadedBytes?`已发送 ${size(progress.loadedBytes)}`:''
 const value=uploading?progress.percent:undefined
 return <div className={`submission-progress ${progress.phase}`} role="region" aria-label={`${label}提交状态`}>
  <div><b aria-live="polite">{title}</b>{value!==undefined?<strong>{value}%</strong>:null}</div>
  <progress max={100} value={value} aria-label={`${label}上传进度`} aria-valuetext={progress.phase==='creating'?'上传完成，服务端正在创建任务':value!==undefined?`${value}%`:'正在准备上传'}/>
  <small>{progress.phase==='creating'?'服务端正在校验文件、持久化并写入任务队列，请保持页面打开。':transferred||'正在建立上传连接…'}</small>
  {onCancel&&progress.phase!=='creating'?<button type="button" className="cancel-upload" onClick={onCancel}><XCircle size={16}/>取消上传</button>:null}
 </div>
}
