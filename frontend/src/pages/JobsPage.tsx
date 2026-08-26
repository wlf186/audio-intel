import {useEffect,useMemo,useRef,useState} from 'react'
import {Check,Copy,Eye,LoaderCircle,RotateCcw,Trash2,XCircle} from 'lucide-react'
import {api,size} from '../lib/api'
import type {Job} from '../lib/types'

const filterLabels:Record<string,string>={all:'全部',asr:'ASR',tts:'TTS',succeeded:'已完成',failed:'失败'}
const stateLabels:Record<string,string>={queued:'等待处理',running:'正在处理',succeeded:'已完成',failed:'失败',cancelled:'已取消'}

function formatDuration(seconds:number){
 const value=Math.max(0,Math.floor(seconds))
 const hours=Math.floor(value/3600)
 const minutes=Math.floor(value%3600/60)
 const remaining=value%60
 return `${String(hours).padStart(2,'0')}:${String(minutes).padStart(2,'0')}:${String(remaining).padStart(2,'0')}`
}

function elapsed(job:Job,now:number){
 let seconds=job.processing_seconds||0
 if(job.state==='running'&&job.processing_as_of)seconds+=Math.max(0,(now-Date.parse(job.processing_as_of))/1000)
 if(seconds<1&&!job.started_at)return '未开始'
 return formatDuration(seconds)
}

function deviceName(job:Job){
 const saved=job.compute_device_name||job.result?.compute_device_name||String(job.request.compute_device_name||'')
 if(saved)return saved
 const device=job.compute_device||job.result?.compute_device||job.request.compute_device||(job.kind==='asr'?'gpu':'cpu')
 return device==='cpu'?'CPU':'GPU'
}

function compactSeconds(value:number){if(value<60)return `${Math.max(1,Math.round(value))}秒`;if(value<3600)return `${Math.max(1,Math.round(value/60))}分钟`;return `${(value/3600).toFixed(1)}小时`}
function queueEstimate(job:Job){
 const parts:string[]=[]
 if(job.state==='queued'&&job.queue?.position)parts.push(`队列 ${job.queue.position}/${job.queue.depth}`)
 if(job.queue?.waiting_for==='gpu')parts.push('等待 GPU')
 const range=job.estimate?.remaining_seconds
 if(range)parts.push(`预计 ${compactSeconds(range.lower)}–${compactSeconds(range.upper)} · ${job.estimate?.confidence==='high'?'高':job.estimate?.confidence==='medium'?'中':'低'}置信度`)
 else if(job.estimate?.state==='warming_up'&&['queued','running'].includes(job.state))parts.push(`ETA 学习中 ${job.estimate.sample_count}/5`)
 return parts.join(' · ')
}

async function copyText(value:string){
 try{if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(value);return}}catch{}
 const textarea=document.createElement('textarea')
 const active=document.activeElement instanceof HTMLElement?document.activeElement:undefined
 textarea.value=value
 textarea.readOnly=true
 textarea.style.position='fixed'
 textarea.style.opacity='0'
 document.body.appendChild(textarea)
 try{textarea.focus();textarea.select();if(!document.execCommand('copy'))throw new Error('Copy command failed')}finally{textarea.remove();active?.focus()}
}

export function JobsPage({jobs,refresh,openJob,onJobUpdated}:{jobs:Job[];refresh:()=>void;openJob:(j:Job)=>void;onJobUpdated:(job:Job)=>void}){
 const [filter,setFilter]=useState('all')
 const [selected,setSelected]=useState<Set<string>>(()=>new Set())
 const [busy,setBusy]=useState(false)
 const [error,setError]=useState('')
 const [notice,setNotice]=useState('')
 const [copiedId,setCopiedId]=useState('')
 const [cancellingIds,setCancellingIds]=useState<Set<string>>(()=>new Set())
 const [now,setNow]=useState(()=>Date.now())
 const selectAll=useRef<HTMLInputElement>(null)
 const copyResetTimer=useRef<number>(undefined)
 const shown=useMemo(()=>jobs.filter(job=>filter==='all'||job.kind===filter||job.state===filter),[jobs,filter])
 const eligible=useMemo(()=>shown.filter(job=>job.state!=='running'),[shown])
 const selectedCount=selected.size
 const allSelected=eligible.length>0&&eligible.every(job=>selected.has(job.id))
 const partiallySelected=selectedCount>0&&!allSelected

 useEffect(()=>{if(!shown.some(job=>job.state==='running'&&job.started_at))return;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer)},[shown])
 useEffect(()=>{if(selectAll.current)selectAll.current.indeterminate=partiallySelected},[partiallySelected])
 useEffect(()=>{const available=new Set(jobs.filter(job=>job.state!=='running').map(job=>job.id));setSelected(current=>{const next=new Set([...current].filter(id=>available.has(id)));return next.size===current.size?current:next})},[jobs])
 useEffect(()=>()=>{if(copyResetTimer.current!==undefined)clearTimeout(copyResetTimer.current)},[])

 const changeFilter=(value:string)=>{setFilter(value);setSelected(new Set());setError('');setNotice('')}
 const toggle=(id:string)=>setSelected(current=>{const next=new Set(current);if(next.has(id))next.delete(id);else next.add(id);return next})
 const toggleAll=()=>setSelected(current=>{if(allSelected){const next=new Set(current);eligible.forEach(job=>next.delete(job.id));return next}return new Set(eligible.map(job=>job.id))})
 const copyJobId=async(id:string)=>{try{await copyText(id);setCopiedId(id);if(copyResetTimer.current!==undefined)clearTimeout(copyResetTimer.current);copyResetTimer.current=window.setTimeout(()=>setCopiedId(current=>current===id?'':current),2000)}catch{setError(current=>[current,`无法自动复制任务 ID，请手动复制：${id}`].filter(Boolean).join('；'))}}
 const act=async(operation:()=>Promise<unknown>)=>{setError('');setNotice('');try{await operation();refresh()}catch(cause){setError((cause as Error).message)}}
 const cancelJob=async(job:Job)=>{
  setError('');setNotice('');setCancellingIds(current=>new Set(current).add(job.id))
  try{
   let snapshot=await api.cancel(job.id)
   onJobUpdated(snapshot)
   const deadline=Date.now()+4000
   while(snapshot.state==='running'&&Date.now()<deadline){
    await new Promise(resolve=>window.setTimeout(resolve,250))
    snapshot=await api.job(job.id)
    onJobUpdated(snapshot)
   }
   if(snapshot.state==='cancelled')setNotice('任务已安全停止，现在可以重试或永久删除。')
   else if(snapshot.state==='running')setNotice('停止请求已提交，系统仍在确认计算进程退出。')
  }catch(cause){setError((cause as Error).message)}finally{
   setCancellingIds(current=>{const next=new Set(current);next.delete(job.id);return next})
  }
 }
 const remove=async(ids:string[])=>{
  const requested=new Set(ids)
  const targets=jobs.filter(job=>requested.has(job.id)&&job.state!=='running')
  if(!targets.length)return
  const queued=targets.filter(job=>job.state==='queued').length
  const detail=queued?`其中 ${queued} 个排队任务会先取消。`:''
  if(!confirm(`永久删除选中的 ${targets.length} 个任务？${detail}\n输入、输出、临时文件和数据库记录都将被清除，且不可恢复。`))return
  setBusy(true);setError('');setNotice('')
  try{
   const result=await api.removeMany(targets.map(job=>job.id))
   const deletedIds=new Set(result.deleted.map(item=>item.id))
   setSelected(current=>new Set([...current].filter(id=>!deletedIds.has(id))))
   setNotice(`已删除 ${result.deleted_count} 个任务，释放 ${size(result.reclaimed_bytes)}；${result.database_compacted?'数据库已安全压缩':'数据库压缩未完成'}。`)
   if(result.failed.length)setError(`${result.failed_count} 个任务未删除：${result.failed.map(item=>item.message).join('；')}`)
   if(!result.database_compacted)setError(current=>[current,result.maintenance_error||'SQLite 安全压缩失败，请检查服务日志。'].filter(Boolean).join('；'))
   refresh()
  }catch(cause){setError((cause as Error).message)}finally{setBusy(false)}
 }

 return <section className="page-pad jobs-page hud-page" data-module="TASK_HISTORY / LOG_03">
  <div className="page-heading"><div><h1>任务记录</h1><p>输入、结果和导出文件均持久化在项目目录，可选择任务永久清理。</p></div><div className="filter">{Object.entries(filterLabels).map(([value,label])=><button className={filter===value?'active':''} onClick={()=>changeFilter(value)} key={value}>{label}</button>)}</div></div>
  {selectedCount?<div className="selection-bar" role="region" aria-label="批量任务操作"><span><b>{selectedCount}</b> 个任务已选择</span><button disabled={busy} onClick={()=>void remove([...selected])}>{busy?<LoaderCircle className="spin"/>:<Trash2/>}{busy?'正在安全清理…':'永久删除所选任务'}</button></div>:null}
  {notice?<p className="notice" role="status">{notice}</p>:null}{error?<p className="error" role="alert">{error}</p>:null}{copiedId?<span className="sr-only" role="status" aria-live="polite">已复制完整任务 ID {copiedId}</span>:null}
  <div className="jobs-table"><div className="table-head"><span className="select-cell"><input ref={selectAll} type="checkbox" aria-label="全选当前筛选任务" checked={allSelected} disabled={!eligible.length||busy} onChange={toggleAll}/></span><span>任务</span><span>类型</span><span>状态</span><span>创建时间</span><span>耗时</span><span>进度</span><span>操作</span></div>{shown.map(job=>{
   const canDelete=job.state!=='running'
   const stopping=job.state==='running'&&(job.stage==='cancelling'||cancellingIds.has(job.id))
   const device=deviceName(job)
   const estimate=queueEstimate(job)
   const stageLabel=stopping?'正在释放计算资源':(job.progress_detail?.stage_code||job.stage.replaceAll('_',' '))
   return <div className={`table-row ${selected.has(job.id)?'selected':''}`} key={job.id}>
    <span className="select-cell"><input type="checkbox" aria-label={`选择任务 ${job.display_name}`} checked={selected.has(job.id)} disabled={!canDelete||busy} title={canDelete?'选择任务':'运行中的任务需先取消'} onChange={()=>toggle(job.id)}/></span>
    <span className="job-name"><b>{job.display_name}</b><small className="job-meta"><span className="job-id" title={`完整任务 ID：${job.id}`}>任务 ID：{job.id.slice(0,12)}…</span><button type="button" className={`copy-job-id ${copiedId===job.id?'copied':''}`} aria-label={`${copiedId===job.id?'已复制':'复制'}完整任务 ID ${job.id}`} title={copiedId===job.id?'已复制完整任务 ID':'复制完整任务 ID'} onClick={()=>void copyJobId(job.id)}>{copiedId===job.id?<Check/>:<Copy/>}</button><span aria-hidden="true">·</span><span>{device}</span><span aria-hidden="true">·</span><i>{elapsed(job,now)}</i></small></span>
    <span className="kind">{job.kind.toUpperCase()}</span>
    <span className={`status ${job.state}`}>{stopping?'正在安全停止':stateLabels[job.state]}</span>
    <span>{new Date(job.created_at).toLocaleString()}</span>
    <span className="elapsed">{elapsed(job,now)}<small>{(job.attempts||0)>1?`${job.attempts} 次尝试`:'实际处理'}</small></span>
    <span>{Math.round(job.progress*100)}% · {stageLabel}{estimate?<small className="queue-estimate">{estimate}</small>:null}</span>
    <span className="actions">{job.state==='succeeded'?<button title="查看结果" onClick={()=>openJob(job)}><Eye/></button>:null}{['queued','running'].includes(job.state)?<button title={stopping?'正在安全停止':'取消任务'} aria-label={stopping?`正在安全停止 ${job.display_name}`:undefined} disabled={stopping} onClick={()=>void cancelJob(job)}>{stopping?<LoaderCircle className="spin"/>:<XCircle/>}</button>:null}{['failed','cancelled'].includes(job.state)?<button title="重试" onClick={()=>void act(()=>api.retry(job.id))}><RotateCcw/></button>:null}{canDelete?<button title="永久删除" disabled={busy} onClick={()=>void remove([job.id])}><Trash2/></button>:null}</span>
   </div>
  })}</div>
 </section>
}
