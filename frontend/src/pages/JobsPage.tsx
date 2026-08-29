import {useCallback,useEffect,useMemo,useRef,useState,type Dispatch,type SetStateAction} from 'react'
import {Check,ChevronLeft,ChevronRight,ChevronsLeft,ChevronsRight,Copy,Eye,LoaderCircle,RotateCcw,Search,Trash2,XCircle} from 'lucide-react'
import {api,size} from '../lib/api'
import type {Job,JobHistoryQuery,JobListResponse,JobState} from '../lib/types'
import {progressPresentation} from '../lib/jobs'
import {formatLocalDateTime} from '../lib/presentation'
import {ConfirmDialog} from '../components/ConfirmDialog'

const kindLabels:Record<JobHistoryQuery['kind'],string>={all:'全部',asr:'ASR',tts:'TTS'}
const stateLabels:Record<JobState,string>={queued:'等待处理',running:'正在处理',succeeded:'已完成',failed:'失败',cancelled:'已取消'}
const pageSizes=[25,50,100] as const

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

function matchesQuery(job:Job,query:JobHistoryQuery){
 if(query.kind!=='all'&&job.kind!==query.kind)return false
 if(query.state!=='all'&&job.state!==query.state)return false
 const search=query.search.trim().toLocaleLowerCase()
 return !search||job.id.toLocaleLowerCase().includes(search)||job.display_name.toLocaleLowerCase().includes(search)
}

function pageNumbers(current:number,total:number){
 const values=new Set([1,total,current-2,current-1,current,current+1,current+2])
 return [...values].filter(value=>value>=1&&value<=total).sort((left,right)=>left-right)
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

type Props={
 liveJobs:Job[]
 liveJobsReady:boolean
 query:JobHistoryQuery
 setQuery:Dispatch<SetStateAction<JobHistoryQuery>>
 refreshRecentJobs:()=>Promise<void>
 openJob:(job:Job)=>void
 onJobUpdated:(job:Job)=>void
 onJobsRemoved:(ids:string[])=>void
}

export function JobsPage({liveJobs,liveJobsReady,query,setQuery,refreshRecentJobs,openJob,onJobUpdated,onJobsRemoved}:Props){
 const [page,setPage]=useState<JobListResponse>()
 const [search,setSearch]=useState(query.search)
 const [selected,setSelected]=useState<Set<string>>(()=>new Set())
 const [busy,setBusy]=useState(false)
 const [loading,setLoading]=useState(true)
 const [error,setError]=useState('')
 const [notice,setNotice]=useState('')
 const [copiedId,setCopiedId]=useState('')
 const [cancellingIds,setCancellingIds]=useState<Set<string>>(()=>new Set())
 const [hasNewJobs,setHasNewJobs]=useState(false)
 const [reloadVersion,setReloadVersion]=useState(0)
 const [now,setNow]=useState(()=>Date.now())
 const [pendingDelete,setPendingDelete]=useState<string[]>([])
 const selectAll=useRef<HTMLInputElement>(null)
 const copyResetTimer=useRef<number>(undefined)
 const requestSequence=useRef(0)
 const knownLiveIds=useRef<Set<string>|undefined>(undefined)

 const requestPage=useCallback(async()=>{
  const sequence=++requestSequence.current
  setLoading(true)
  try{
   const response=await api.jobs({kind:query.kind==='all'?undefined:query.kind,state:query.state==='all'?undefined:query.state,q:query.search.trim()||undefined,limit:query.limit,offset:query.offset})
   if(sequence!==requestSequence.current)return
   if(response.total>0&&query.offset>=response.total){setQuery(current=>({...current,offset:Math.floor((response.total-1)/query.limit)*query.limit}));return}
   setPage(response)
   setError('')
  }catch(cause){if(sequence===requestSequence.current)setError((cause as Error).message)}finally{if(sequence===requestSequence.current)setLoading(false)}
 },[query,setQuery])

 useEffect(()=>{void requestPage()},[requestPage,reloadVersion])
 useEffect(()=>{if(search===query.search)return;const timer=window.setTimeout(()=>setQuery(current=>({...current,search:search.trim(),offset:0})),300);return()=>clearTimeout(timer)},[query.search,search,setQuery])
 useEffect(()=>{if(!page?.items.some(job=>job.state==='running'&&job.started_at))return;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer)},[page?.items])
 useEffect(()=>{if(selectAll.current)selectAll.current.indeterminate=selected.size>0&&!page?.items.filter(job=>job.state!=='running').every(job=>selected.has(job.id))},[page?.items,selected])
 useEffect(()=>{const available=new Set((page?.items||[]).filter(job=>job.state!=='running').map(job=>job.id));setSelected(current=>{const next=new Set([...current].filter(id=>available.has(id)));return next.size===current.size?current:next})},[page?.items])
 useEffect(()=>()=>{if(copyResetTimer.current!==undefined)clearTimeout(copyResetTimer.current)},[])
 useEffect(()=>{
  const liveById=new Map(liveJobs.map(job=>[job.id,job]))
  setPage(current=>current?{...current,items:current.items.map(job=>liveById.get(job.id)||job)}:current)
  if(!liveJobsReady){knownLiveIds.current=undefined;return}
  const nextIds=new Set(liveJobs.map(job=>job.id))
  const knownIds=knownLiveIds.current
  if(!knownIds){knownLiveIds.current=nextIds;return}
  const added=liveJobs.some(job=>!knownIds.has(job.id)&&matchesQuery(job,query))
  for(const id of nextIds)knownIds.add(id)
  if(added)setHasNewJobs(true)
 },[liveJobs,liveJobsReady,query])

 const items=page?.items||[]
 const eligible=useMemo(()=>items.filter(job=>job.state!=='running'),[items])
 const allSelected=eligible.length>0&&eligible.every(job=>selected.has(job.id))
 const total=page?.total||0
 const totalPages=Math.max(1,Math.ceil(total/query.limit))
 const currentPage=Math.min(totalPages,Math.floor(query.offset/query.limit)+1)

 const updateQuery=(patch:Partial<JobHistoryQuery>)=>{setSelected(new Set());setError('');setNotice('');setHasNewJobs(false);setQuery(current=>({...current,...patch,offset:patch.offset??0}))}
 const toggle=(id:string)=>setSelected(current=>{const next=new Set(current);if(next.has(id))next.delete(id);else next.add(id);return next})
 const toggleAll=()=>setSelected(current=>allSelected?new Set([...current].filter(id=>!eligible.some(job=>job.id===id))):new Set(eligible.map(job=>job.id)))
 const copyJobId=async(id:string)=>{try{await copyText(id);setCopiedId(id);if(copyResetTimer.current!==undefined)clearTimeout(copyResetTimer.current);copyResetTimer.current=window.setTimeout(()=>setCopiedId(current=>current===id?'':current),2000)}catch{setError(current=>[current,`无法自动复制任务 ID，请手动复制：${id}`].filter(Boolean).join('；'))}}
 const refreshAll=async()=>{await Promise.all([requestPage(),refreshRecentJobs()])}
 const act=async(operation:()=>Promise<unknown>)=>{setError('');setNotice('');try{await operation();await refreshAll()}catch(cause){setError((cause as Error).message)}}
 const updateSnapshot=(snapshot:Job)=>{setPage(current=>current?{...current,items:current.items.map(job=>job.id===snapshot.id?snapshot:job)}:current);onJobUpdated(snapshot)}
 const cancelJob=async(job:Job)=>{
  setError('');setNotice('');setCancellingIds(current=>new Set(current).add(job.id))
  try{
   let snapshot=await api.cancel(job.id)
   updateSnapshot(snapshot)
   const deadline=Date.now()+4000
   while(snapshot.state==='running'&&Date.now()<deadline){await new Promise(resolve=>window.setTimeout(resolve,250));snapshot=await api.job(job.id);updateSnapshot(snapshot)}
   if(snapshot.state==='cancelled')setNotice('任务已安全停止，现在可以重试或永久删除。')
   else if(snapshot.state==='running')setNotice('停止请求已提交，系统仍在确认计算进程退出。')
  }catch(cause){setError((cause as Error).message)}finally{setCancellingIds(current=>{const next=new Set(current);next.delete(job.id);return next})}
 }
 const remove=(ids:string[])=>setPendingDelete(ids)
 const confirmRemove=async()=>{
  const requested=new Set(pendingDelete)
  const targets=items.filter(job=>requested.has(job.id)&&job.state!=='running')
  if(!targets.length){setPendingDelete([]);return}
  setBusy(true);setError('');setNotice('')
  try{
   const result=await api.removeMany(targets.map(job=>job.id))
   const deletedIds=result.deleted.map(item=>item.id)
   const deletedSet=new Set(deletedIds)
   setSelected(current=>new Set([...current].filter(id=>!deletedSet.has(id))))
   onJobsRemoved(deletedIds)
   await refreshAll()
   setNotice(`已删除 ${result.deleted_count} 个任务，释放 ${size(result.reclaimed_bytes)}；${result.database_compacted?'数据库已安全压缩':'数据库压缩未完成'}。`)
   if(result.failed.length)setError(`${result.failed_count} 个任务未删除：${result.failed.map(item=>item.message).join('；')}`)
   if(!result.database_compacted)setError(current=>[current,result.maintenance_error||'SQLite 安全压缩失败，请检查服务日志。'].filter(Boolean).join('；'))
   setPendingDelete([])
  }catch(cause){setError((cause as Error).message)}finally{setBusy(false)}
 }
 const showNewest=()=>{setHasNewJobs(false);if(query.offset===0)setReloadVersion(value=>value+1);else updateQuery({offset:0})}
 const goToPage=(value:number)=>updateQuery({offset:(Math.min(totalPages,Math.max(1,value))-1)*query.limit})
 const pages=pageNumbers(currentPage,totalPages)
 const hasFilters=query.kind!=='all'||query.state!=='all'||Boolean(query.search.trim())
 const deleteTargets=items.filter(job=>pendingDelete.includes(job.id)&&job.state!=='running')
 const queuedDeletes=deleteTargets.filter(job=>job.state==='queued').length

 return <section className="page-pad jobs-page hud-page" data-module="TASK_HISTORY / LOG_03">
  <div className="page-heading"><div><h1 tabIndex={-1}>任务记录</h1><p>{loading&&!page?'正在读取任务记录…':`共 ${total} 个匹配任务，输入、结果和导出文件均持久化在项目目录。`}</p></div><div className="filter" role="group" aria-label="任务类型筛选">{Object.entries(kindLabels).map(([value,label])=><button type="button" aria-pressed={query.kind===value} className={query.kind===value?'active':''} onClick={()=>updateQuery({kind:value as JobHistoryQuery['kind']})} key={value}>{label}</button>)}</div></div>
  <div className="job-query-bar">
   <label className="job-search"><span>搜索任务</span><span className="search-field"><Search aria-hidden="true"/><input value={search} maxLength={128} placeholder="任务名称或 ID" onChange={event=>setSearch(event.target.value)}/></span></label>
   <label><span>任务状态</span><select value={query.state} onChange={event=>updateQuery({state:event.target.value as JobHistoryQuery['state']})}><option value="all">全部状态</option>{Object.entries(stateLabels).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
   <label><span>每页数量</span><select value={query.limit} onChange={event=>updateQuery({limit:Number(event.target.value) as JobHistoryQuery['limit']})}>{pageSizes.map(value=><option key={value} value={value}>{value} 条</option>)}</select></label>
  </div>
  {hasNewJobs?<div className="new-jobs-banner" role="region" aria-label="新任务提示"><span>有新任务，当前页不会自动位移。</span><button type="button" onClick={showNewest}>返回第一页查看</button></div>:null}
  {selected.size?<div className="selection-bar" role="region" aria-label="批量任务操作"><span><b>{selected.size}</b> 个任务已选择</span><button disabled={busy} onClick={()=>void remove([...selected])}>{busy?<LoaderCircle className="spin"/>:<Trash2/>}{busy?'正在安全清理…':'永久删除所选任务'}</button></div>:null}
  {notice?<p className="notice" role="status">{notice}</p>:null}{error?<p className="error" role="alert">{error}</p>:null}{copiedId?<span className="sr-only" role="status" aria-live="polite">已复制完整任务 ID {copiedId}</span>:null}
  <div className="jobs-table" role="table" aria-label="任务记录" aria-busy={loading}><div className="table-head" role="row"><span className="select-cell" role="columnheader"><input ref={selectAll} type="checkbox" aria-label="全选当前页可操作任务" checked={allSelected} disabled={!eligible.length||busy} onChange={toggleAll}/></span><span role="columnheader">任务</span><span role="columnheader">类型</span><span role="columnheader">状态</span><span role="columnheader">创建时间</span><span role="columnheader">耗时</span><span role="columnheader">进度</span><span role="columnheader">操作</span></div>{items.map(job=>{
   const canDelete=job.state!=='running'
   const stopping=job.state==='running'&&(job.stage==='cancelling'||cancellingIds.has(job.id))
   const device=deviceName(job)
   const estimate=queueEstimate(job)
   const live=progressPresentation(job)
   const stageLabel=stopping?'正在释放计算资源':live.stage
   return <div className={`table-row ${selected.has(job.id)?'selected':''}`} role="row" key={job.id}>
    <span className="select-cell"><label><input type="checkbox" aria-label={`选择任务 ${job.display_name}`} checked={selected.has(job.id)} disabled={!canDelete||busy} title={canDelete?'选择任务':'运行中的任务需先取消'} onChange={()=>toggle(job.id)}/></label></span>
    <span className="job-name"><b>{job.display_name}</b><small className="job-meta"><span className="job-id" title={`完整任务 ID：${job.id}`}>任务 ID：{job.id.slice(0,12)}…</span><button type="button" className={`copy-job-id ${copiedId===job.id?'copied':''}`} aria-label={`${copiedId===job.id?'已复制':'复制'}完整任务 ID ${job.id}`} title={copiedId===job.id?'已复制完整任务 ID':'复制完整任务 ID'} onClick={()=>void copyJobId(job.id)}>{copiedId===job.id?<Check/>:<Copy/>}</button><span aria-hidden="true">·</span><span>{device}</span></small></span>
    <span className="kind" data-label="类型">{job.kind.toUpperCase()}</span>
    <span className={`status ${job.state}`} data-label="状态">{stopping?'正在安全停止':stateLabels[job.state]}</span>
    <span className="created" data-label="创建">{formatLocalDateTime(job.created_at)}</span>
    <span className="elapsed" data-label="耗时">{elapsed(job,now)}<small>{(job.attempts||0)>1?`${job.attempts} 次尝试`:'实际处理'}</small></span>
    <span className="job-progress-cell" data-label="进度"><span className="progress-summary">{live.percent}%{live.estimated?' 估算':''} · {stageLabel}</span>{['queued','running'].includes(job.state)?<progress max={100} value={live.percent} aria-label={`${job.display_name} 任务进度 ${live.percent}%`}/>:null}{live.detail?<small className="progress-activity">{live.detail}</small>:null}{estimate?<small className="queue-estimate">{estimate}</small>:null}</span>
    <span className="actions">{job.state==='succeeded'?<button title="查看结果" aria-label={`查看任务结果 ${job.display_name}`} onClick={()=>openJob(job)}><Eye/></button>:null}{['queued','running'].includes(job.state)?<button title={stopping?'正在安全停止':'取消任务'} aria-label={stopping?`正在安全停止 ${job.display_name}`:`取消任务 ${job.display_name}`} disabled={stopping} onClick={()=>void cancelJob(job)}>{stopping?<LoaderCircle className="spin"/>:<XCircle/>}</button>:null}{['failed','cancelled'].includes(job.state)?<button title="重试" aria-label={`重试任务 ${job.display_name}`} onClick={()=>void act(()=>api.retry(job.id))}><RotateCcw/></button>:null}{canDelete?<button title="永久删除" aria-label={`永久删除任务 ${job.display_name}`} disabled={busy} onClick={()=>void remove([job.id])}><Trash2/></button>:null}</span>
   </div>
  })}{loading&&!items.length?<div className="jobs-loading" role="status"><LoaderCircle className="spin"/>正在加载任务…</div>:!loading&&!items.length?<div className="jobs-empty"><p>{hasFilters?'没有匹配的任务':'还没有任务'}</p><span>{hasFilters?'请调整类型、状态或搜索条件。':'提交音频转写或语音合成后，任务会显示在这里。'}</span></div>:null}</div>
  {total>0?<nav className="job-pagination" aria-label="任务分页"><span>第 {currentPage} / {Math.ceil(total/query.limit)} 页 · 共 {total} 条</span><div><button aria-label="第一页" disabled={currentPage===1} onClick={()=>goToPage(1)}><ChevronsLeft/></button><button aria-label="上一页" disabled={currentPage===1} onClick={()=>goToPage(currentPage-1)}><ChevronLeft/></button>{pages.map((value,index)=><span key={value} className={`page-number-slot ${value===currentPage?'current':''}`}>{index>0&&value-pages[index-1]>1?<i aria-hidden="true">…</i>:null}<button aria-label={`第 ${value} 页`} aria-current={value===currentPage?'page':undefined} className={value===currentPage?'active':''} onClick={()=>goToPage(value)}>{value}</button></span>)}<button aria-label="下一页" disabled={currentPage===totalPages} onClick={()=>goToPage(currentPage+1)}><ChevronRight/></button><button aria-label="最后一页" disabled={currentPage===totalPages} onClick={()=>goToPage(totalPages)}><ChevronsRight/></button></div></nav>:null}
  {pendingDelete.length?<ConfirmDialog title="永久删除任务" description={`永久删除选中的 ${deleteTargets.length} 个任务？${queuedDeletes?`其中 ${queuedDeletes} 个排队任务会先取消。`:''} 输入、输出、临时文件和数据库记录都将被清除，且不可恢复。`} confirmLabel="永久删除" danger busy={busy} onClose={()=>setPendingDelete([])} onConfirm={()=>void confirmRemove()}/>:null}
 </section>
}
