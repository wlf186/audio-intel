import {useCallback,useEffect,useMemo,useRef,useState,type Dispatch,type SetStateAction} from 'react'
import {Check,ChevronLeft,ChevronRight,ChevronsLeft,ChevronsRight,CircleAlert,Copy,Eye,LoaderCircle,RotateCcw,Search,Trash2,XCircle} from 'lucide-react'
import {api,size} from '../lib/api'
import type {JobHistoryQuery,JobListResponse,JobState,JobSummary} from '../lib/types'
import {progressPresentation} from '../lib/jobs'
import {formatLocalDateTime,jobFailurePresentation} from '../lib/presentation'
import {ConfirmDialog} from '../components/ConfirmDialog'
import {Modal} from '../components/Modal'
import {useTranslation} from 'react-i18next'
import type {TFunction} from 'i18next'
import {resolvedLocale} from '../i18n'

const kinds:JobHistoryQuery['kind'][]=['all','asr','tts']
const states:JobState[]=['queued','running','succeeded','failed','cancelled']
const pageSizes=[25,50,100] as const

function formatDuration(seconds:number){
 const value=Math.max(0,Math.floor(seconds))
 const hours=Math.floor(value/3600)
 const minutes=Math.floor(value%3600/60)
 const remaining=value%60
 return `${String(hours).padStart(2,'0')}:${String(minutes).padStart(2,'0')}:${String(remaining).padStart(2,'0')}`
}

function elapsed(job:JobSummary,now:number,t:TFunction){
 let seconds=job.processing_seconds||0
 if(job.state==='running'&&job.processing_as_of)seconds+=Math.max(0,(now-Date.parse(job.processing_as_of))/1000)
 if(seconds<1&&!job.started_at)return t('jobs.history.notStarted')
 return formatDuration(seconds)
}

function deviceName(job:JobSummary){
 const saved=job.compute_device_name||''
 if(saved)return saved
 const device=job.compute_device||(job.kind==='asr'?'gpu':'cpu')
 return device==='cpu'?'CPU':'GPU'
}

function compactSeconds(value:number,t:TFunction){if(value<60)return t('common.units.seconds',{count:Math.max(1,Math.round(value))});if(value<3600)return t('common.units.minutes',{count:Math.max(1,Math.round(value/60))});return t('common.units.hours',{count:(value/3600).toFixed(1)})}
function queueEstimate(job:JobSummary,t:TFunction){
 const parts:string[]=[]
 if(job.state==='queued'&&job.queue?.position)parts.push(t('jobs.history.queue',{position:job.queue.position,depth:job.queue.depth}))
 if(job.queue?.waiting_for==='gpu')parts.push(t('jobs.stages.waiting_for_gpu'))
 const range=job.estimate?.remaining_seconds
 if(range)parts.push(t('jobs.history.estimate',{lower:compactSeconds(range.lower,t),upper:compactSeconds(range.upper,t),confidence:t(`jobs.history.confidence.${job.estimate?.confidence||'low'}`)}))
 else if(job.estimate?.state==='warming_up'&&['queued','running'].includes(job.state))parts.push(t('jobs.history.etaWarming',{count:job.estimate.sample_count}))
 return parts.join(' · ')
}

function matchesQuery(job:JobSummary,query:JobHistoryQuery){
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
 liveJobs:JobSummary[]
 liveJobsReady:boolean
 query:JobHistoryQuery
 setQuery:Dispatch<SetStateAction<JobHistoryQuery>>
 refreshRecentJobs:()=>Promise<void>
 openJob:(job:JobSummary)=>void
 onJobUpdated:(job:JobSummary)=>void
 onJobsRemoved:(ids:string[])=>void
}

export function JobsPage({liveJobs,liveJobsReady,query,setQuery,refreshRecentJobs,openJob,onJobUpdated,onJobsRemoved}:Props){
 const {t}=useTranslation()
 const locale=resolvedLocale()
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
 const [failureDetails,setFailureDetails]=useState<JobSummary>()
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
 const copyJobId=async(id:string)=>{try{await copyText(id);setCopiedId(id);if(copyResetTimer.current!==undefined)clearTimeout(copyResetTimer.current);copyResetTimer.current=window.setTimeout(()=>setCopiedId(current=>current===id?'':current),2000)}catch{setError(current=>[current,t('jobs.history.copyFailed',{id})].filter(Boolean).join(t('common.separator.semicolon')))}}
 const refreshAll=async()=>{await Promise.all([requestPage(),refreshRecentJobs()])}
 const act=async(operation:()=>Promise<unknown>)=>{setError('');setNotice('');try{await operation();await refreshAll()}catch(cause){setError((cause as Error).message)}}
 const updateSnapshot=(snapshot:JobSummary)=>{setPage(current=>current?{...current,items:current.items.map(job=>job.id===snapshot.id?snapshot:job)}:current);onJobUpdated(snapshot)}
 const cancelJob=async(job:JobSummary)=>{
  setError('');setNotice('');setCancellingIds(current=>new Set(current).add(job.id))
  try{
   let snapshot=await api.cancel(job.id)
   updateSnapshot(snapshot)
   const deadline=Date.now()+4000
   while(snapshot.state==='running'&&Date.now()<deadline){await new Promise(resolve=>window.setTimeout(resolve,250));snapshot=await api.job(job.id);updateSnapshot(snapshot)}
   if(snapshot.state==='cancelled')setNotice(t('jobs.history.cancelledNotice'))
   else if(snapshot.state==='running')setNotice(t('jobs.history.cancellingNotice'))
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
   setNotice(t('jobs.history.deletedNotice',{count:result.deleted_count,size:size(result.reclaimed_bytes,locale),database:t(result.database_compacted?'jobs.history.databaseCompacted':'jobs.history.databaseNotCompacted')}))
   if(result.failed.length)setError(t('jobs.history.deleteFailed',{count:result.failed_count,message:result.failed.map(item=>item.message).join(t('common.separator.semicolon'))}))
   if(!result.database_compacted)setError(current=>[current,result.maintenance_error||t('jobs.history.compactionFailed')].filter(Boolean).join(t('common.separator.semicolon')))
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
  <div className="page-heading"><div><h1 tabIndex={-1}>{t('jobs.history.title')}</h1><p>{loading&&!page?t('jobs.history.loading'):t('jobs.history.summary',{count:total})}</p></div><div className="filter" role="group" aria-label={t('jobs.history.kindFilter')}>{kinds.map(value=><button type="button" aria-pressed={query.kind===value} className={query.kind===value?'active':''} onClick={()=>updateQuery({kind:value})} key={value}>{t(`jobs.history.kind.${value}`)}</button>)}</div></div>
  <div className="job-query-bar">
   <label className="job-search"><span>{t('jobs.history.search')}</span><span className="search-field"><Search aria-hidden="true"/><input value={search} maxLength={128} placeholder={t('jobs.history.searchPlaceholder')} onChange={event=>setSearch(event.target.value)}/></span></label>
   <label><span>{t('jobs.history.status')}</span><select value={query.state} onChange={event=>updateQuery({state:event.target.value as JobHistoryQuery['state']})}><option value="all">{t('jobs.history.allStates')}</option>{states.map(value=><option key={value} value={value}>{t(`jobs.history.state.${value}`)}</option>)}</select></label>
   <label><span>{t('jobs.history.pageSize')}</span><select value={query.limit} onChange={event=>updateQuery({limit:Number(event.target.value) as JobHistoryQuery['limit']})}>{pageSizes.map(value=><option key={value} value={value}>{t('jobs.history.pageSizeOption',{count:value})}</option>)}</select></label>
  </div>
  {hasNewJobs?<div className="new-jobs-banner" role="region" aria-label={t('jobs.history.newJobsRegion')}><span>{t('jobs.history.newJobs')}</span><button type="button" onClick={showNewest}>{t('jobs.history.showNewest')}</button></div>:null}
  {selected.size?<div className="selection-bar" role="region" aria-label={t('jobs.history.batchActions')}><span><b>{selected.size}</b> {t('jobs.history.selected',{count:selected.size})}</span><button disabled={busy} onClick={()=>void remove([...selected])}>{busy?<LoaderCircle className="spin"/>:<Trash2/>}{busy?t('jobs.history.cleaning'):t('jobs.history.deleteSelected')}</button></div>:null}
  {notice?<p className="notice" role="status">{notice}</p>:null}{error?<p className="error" role="alert">{error}</p>:null}{copiedId?<span className="sr-only" role="status" aria-live="polite">{t('jobs.history.copiedId',{id:copiedId})}</span>:null}
  <div className="jobs-table" role="table" aria-label={t('jobs.history.title')} aria-busy={loading}><div className="table-head" role="row"><span className="select-cell" role="columnheader"><input ref={selectAll} type="checkbox" aria-label={t('jobs.history.selectAll')} checked={allSelected} disabled={!eligible.length||busy} onChange={toggleAll}/></span><span role="columnheader">{t('jobs.history.columns.task')}</span><span role="columnheader">{t('jobs.history.columns.kind')}</span><span role="columnheader">{t('jobs.history.columns.status')}</span><span role="columnheader">{t('jobs.history.columns.created')}</span><span role="columnheader">{t('jobs.history.columns.elapsed')}</span><span role="columnheader">{t('jobs.history.columns.progress')}</span><span role="columnheader">{t('jobs.history.columns.actions')}</span></div>{items.map(job=>{
   const canDelete=job.state!=='running'
   const stopping=job.state==='running'&&(job.stage==='cancelling'||cancellingIds.has(job.id))
   const device=deviceName(job)
   const estimate=queueEstimate(job,t)
   const live=progressPresentation(job,t)
   const stageLabel=stopping?t('jobs.stages.cancelling'):live.stage
   const failure=job.state==='failed'?jobFailurePresentation(job,t):undefined
   return <div className={`table-row ${selected.has(job.id)?'selected':''}`} role="row" key={job.id}>
    <span className="select-cell" role="cell"><label><input type="checkbox" aria-label={t('jobs.history.selectTask',{name:job.display_name})} checked={selected.has(job.id)} disabled={!canDelete||busy} title={canDelete?t('jobs.history.selectTaskShort'):t('jobs.history.cancelBeforeSelect')} onChange={()=>toggle(job.id)}/></label></span>
    <span className="job-name" role="rowheader"><b>{job.display_name}</b><small className="job-meta"><span className="job-id" title={t('jobs.history.fullId',{id:job.id})}>{t('jobs.history.shortId',{id:job.id.slice(0,12)})}</span><button type="button" className={`copy-job-id ${copiedId===job.id?'copied':''}`} aria-label={t(copiedId===job.id?'jobs.history.copiedFullIdWithValue':'jobs.history.copyFullIdWithValue',{id:job.id})} title={t(copiedId===job.id?'jobs.history.copiedFullId':'jobs.history.copyFullId')} onClick={()=>void copyJobId(job.id)}>{copiedId===job.id?<Check/>:<Copy/>}</button><span aria-hidden="true">·</span><span>{device}</span></small></span>
    <span className="kind" role="cell" data-label={t('jobs.history.columns.kind')}>{job.kind.toUpperCase()}</span>
    <span className={`status ${job.state}`} role="cell" data-label={t('jobs.history.columns.status')}>{stopping?t('jobs.safelyStopping'):t(`jobs.history.state.${job.state}`)}</span>
    <span className="created" role="cell" data-label={t('jobs.history.createdShort')}>{formatLocalDateTime(job.created_at,locale,t)}</span>
    <span className="elapsed" role="cell" data-label={t('jobs.history.columns.elapsed')}>{elapsed(job,now,t)}<small>{(job.attempts||0)>1?t('jobs.history.attempts',{count:job.attempts}):t('jobs.history.actualProcessing')}</small></span>
    <span className="job-progress-cell" role="cell" data-label={t('jobs.history.columns.progress')}><span className="progress-summary">{live.percent}%{live.estimated?` ${t('jobs.estimated')}`:''} · {stageLabel}</span>{['queued','running'].includes(job.state)?<progress max={100} value={live.percent} aria-label={t('jobs.history.taskProgress',{name:job.display_name,percent:live.percent})}/>:null}{failure?<small className="job-failure-summary"><b>{failure.title}</b><span>{failure.advice}</span></small>:null}{live.detail?<small className="progress-activity">{live.detail}</small>:null}{estimate?<small className="queue-estimate">{estimate}</small>:null}</span>
    <span className="actions" role="cell">{job.state==='succeeded'?<button title={t('jobs.history.viewResult')} aria-label={t('jobs.history.viewTaskResult',{name:job.display_name})} onClick={()=>openJob(job)}><Eye/></button>:null}{job.state==='failed'?<button title={t('jobs.history.viewFailure')} aria-label={t('jobs.history.viewTaskFailure',{name:job.display_name})} onClick={()=>setFailureDetails(job)}><CircleAlert/></button>:null}{['queued','running'].includes(job.state)?<button title={stopping?t('jobs.safelyStopping'):t('jobs.history.cancelTask')} aria-label={t(stopping?'jobs.history.stoppingNamed':'jobs.history.cancelNamed',{name:job.display_name})} disabled={stopping} onClick={()=>void cancelJob(job)}>{stopping?<LoaderCircle className="spin"/>:<XCircle/>}</button>:null}{['failed','cancelled'].includes(job.state)?<button title={t('common.actions.retry')} aria-label={t('jobs.history.retryNamed',{name:job.display_name})} onClick={()=>void act(()=>api.retry(job.id))}><RotateCcw/></button>:null}{canDelete?<button title={t('common.actions.deletePermanently')} aria-label={t('jobs.history.deleteNamed',{name:job.display_name})} disabled={busy} onClick={()=>void remove([job.id])}><Trash2/></button>:null}</span>
   </div>
  })}{loading&&!items.length?<div className="jobs-loading" role="status"><LoaderCircle className="spin"/>{t('jobs.history.loadingTasks')}</div>:!loading&&!items.length?<div className="jobs-empty"><p>{hasFilters?t('jobs.history.noMatches'):t('jobs.history.empty')}</p><span>{hasFilters?t('jobs.history.adjustFilters'):t('jobs.history.emptyHelp')}</span></div>:null}</div>
  {total>0?<nav className="job-pagination" aria-label={t('jobs.history.pagination')}><span>{t('jobs.history.pageSummary',{current:currentPage,total:Math.ceil(total/query.limit),count:total})}</span><div><button aria-label={t('jobs.history.firstPage')} disabled={currentPage===1} onClick={()=>goToPage(1)}><ChevronsLeft/></button><button aria-label={t('jobs.history.previousPage')} disabled={currentPage===1} onClick={()=>goToPage(currentPage-1)}><ChevronLeft/></button>{pages.map((value,index)=><span key={value} className={`page-number-slot ${value===currentPage?'current':''}`}>{index>0&&value-pages[index-1]>1?<i aria-hidden="true">…</i>:null}<button aria-label={t('jobs.history.pageNumber',{value})} aria-current={value===currentPage?'page':undefined} className={value===currentPage?'active':''} onClick={()=>goToPage(value)}>{value}</button></span>)}<button aria-label={t('jobs.history.nextPage')} disabled={currentPage===totalPages} onClick={()=>goToPage(currentPage+1)}><ChevronRight/></button><button aria-label={t('jobs.history.lastPage')} disabled={currentPage===totalPages} onClick={()=>goToPage(totalPages)}><ChevronsRight/></button></div></nav>:null}
  {pendingDelete.length?<ConfirmDialog title={t('jobs.history.deleteTitle')} description={t('jobs.history.deleteDescription',{count:deleteTargets.length,queued:queuedDeletes?t('jobs.history.queuedDelete',{count:queuedDeletes}):''})} confirmLabel={t('common.actions.deletePermanently')} danger busy={busy} onClose={()=>setPendingDelete([])} onConfirm={()=>void confirmRemove()}/>:null}
  {failureDetails?<Modal title={t('jobs.history.failureDetails')} closeLabel={t('jobs.history.closeFailure')} onClose={()=>setFailureDetails(undefined)}><div className="failure-details"><h3>{jobFailurePresentation(failureDetails,t).title}</h3><p>{jobFailurePresentation(failureDetails,t).advice}</p><dl><div><dt>{t('jobs.history.columns.task')}</dt><dd>{failureDetails.display_name}</dd></div><div><dt>{t('jobs.history.errorCode')}</dt><dd><code>{failureDetails.error_code||'UnknownError'}</code></dd></div></dl><label>{t('jobs.history.technicalDetails')}<textarea readOnly value={failureDetails.error_message||t('jobs.history.noErrorDetails')}/></label><div className="modal-actions"><button className="button" onClick={()=>setFailureDetails(undefined)}>{t('common.actions.close')}</button></div></div></Modal>:null}
 </section>
}
