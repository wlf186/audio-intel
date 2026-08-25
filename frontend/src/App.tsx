import {useCallback,useEffect,useRef,useState} from 'react'
import {AppShell,type Page} from './components/AppShell'
import {api} from './lib/api'
import type {Health,Job} from './lib/types'
import {AsrPage} from './pages/AsrPage'
import {TtsPage} from './pages/TtsPage'
import {JobsPage} from './pages/JobsPage'
import {SystemPage} from './pages/SystemPage'

const pages=new Set<Page>(['asr','tts','jobs','system'])
const jobLimit=100
function pageFromHash():Page{const value=location.hash.slice(1) as Page;return pages.has(value)?value:'asr'}
function sameJobSnapshot(current:Job,next:Job){return current.updated_at===next.updated_at&&current.state===next.state&&current.stage===next.stage&&current.progress===next.progress&&current.attempts===next.attempts&&current.error_message===next.error_message&&current.started_at===next.started_at&&current.finished_at===next.finished_at}

export default function App(){
 const [page,setPage]=useState<Page>(pageFromHash)
 const [jobs,setJobs]=useState<Job[]>([])
 const [health,setHealth]=useState<Health>()
 const [connectionError,setConnectionError]=useState('')
 const [selected,setSelected]=useState<Partial<Record<Job['kind'],string>>>({})
 const refreshSequence=useRef(0)
 const refresh=useCallback(async()=>{const sequence=++refreshSequence.current;try{const [nextJobs,nextHealth]=await Promise.all([api.jobs(),api.health()]);if(sequence!==refreshSequence.current)return;setJobs(current=>{const previous=new Map(current.map(job=>[job.id,job]));const merged=nextJobs.items.map(job=>{const existing=previous.get(job.id);return existing&&sameJobSnapshot(existing,job)?existing:job});return merged.length===current.length&&merged.every((job,index)=>job===current[index])?current:merged});setHealth(nextHealth);setConnectionError('')}catch(error){if(sequence===refreshSequence.current)setConnectionError((error as Error).message)}},[])
 useEffect(()=>{void refresh();const timer=setInterval(()=>void refresh(),2000);return()=>clearInterval(timer)},[refresh])
 useEffect(()=>{const onHash=()=>setPage(pageFromHash());addEventListener('hashchange',onHash);return()=>removeEventListener('hashchange',onHash)},[])
 const navigate=(next:Page)=>{setPage(next);if(location.hash!==`#${next}`)location.hash=next}
 const openJob=(job:Job)=>{if(job.state==='succeeded'){setSelected(current=>({...current,[job.kind]:job.id}));navigate(job.kind)}else navigate('jobs')}
 const onJobSubmitted=useCallback((job:Job)=>{refreshSequence.current+=1;setJobs(current=>[job,...current.filter(item=>item.id!==job.id)].slice(0,jobLimit));setConnectionError('')},[])
 const gpuAvailable=health?Boolean(health.hardware.gpu):undefined
 return <AppShell page={page} setPage={navigate} services={health?.services||[]} connectionError={connectionError}>
  {page==='asr'?<AsrPage jobs={jobs} onJobSubmitted={onJobSubmitted} selectedJobId={selected.asr} onSelect={openJob} gpuAvailable={gpuAvailable}/>:null}
  {page==='tts'?<TtsPage jobs={jobs} onJobSubmitted={onJobSubmitted} selectedJobId={selected.tts} onSelect={openJob} gpuAvailable={gpuAvailable}/>:null}
  {page==='jobs'?<JobsPage jobs={jobs} refresh={refresh} openJob={openJob}/>:null}
  {page==='system'?<SystemPage health={health}/>:null}
 </AppShell>
}
