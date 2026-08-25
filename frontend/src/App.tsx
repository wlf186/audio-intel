import {useCallback,useEffect,useRef,useState} from 'react'
import {AppShell,type Page} from './components/AppShell'
import {api} from './lib/api'
import type {AuthSession,Capabilities,Health,Job,JobResult,ResultRevealRequest,VoiceprintPerson} from './lib/types'
import {AsrPage} from './pages/AsrPage'
import {TtsPage} from './pages/TtsPage'
import {JobsPage} from './pages/JobsPage'
import {SystemPage} from './pages/SystemPage'
import {VoiceprintsPage} from './pages/VoiceprintsPage'
import {AuthGate} from './components/AuthGate'

const pages=new Set<Page>(['asr','tts','voiceprints','jobs','system'])
const jobLimit=100
function pageFromHash():Page{const value=location.hash.slice(1) as Page;return pages.has(value)?value:'asr'}
function sameJobSnapshot(current:Job,next:Job){return current.updated_at===next.updated_at&&current.state===next.state&&current.stage===next.stage&&current.progress===next.progress&&current.attempts===next.attempts&&current.error_message===next.error_message&&current.started_at===next.started_at&&current.finished_at===next.finished_at}

export default function App(){
 const [page,setPage]=useState<Page>(pageFromHash)
 const [jobs,setJobs]=useState<Job[]>([])
 const [health,setHealth]=useState<Health>()
 const [capabilities,setCapabilities]=useState<Capabilities>()
 const [voiceprints,setVoiceprints]=useState<VoiceprintPerson[]>([])
 const [connectionError,setConnectionError]=useState('')
 const [auth,setAuth]=useState<AuthSession>()
 const [authError,setAuthError]=useState('')
 const [selected,setSelected]=useState<Partial<Record<Job['kind'],string>>>({})
 const [reveal,setReveal]=useState<(ResultRevealRequest&{kind:Job['kind']})>()
 const refreshSequence=useRef(0)
 const revealSequence=useRef(0)
 const refreshVoiceprints=useCallback(async()=>{const response=await api.voiceprints();setVoiceprints(response.items)},[])
 const authenticated=auth?.authenticated===true
 const refresh=useCallback(async()=>{if(!authenticated)return;const sequence=++refreshSequence.current;try{const [nextJobs,nextHealth]=await Promise.all([api.jobs(),api.system()]);if(sequence!==refreshSequence.current)return;setJobs(current=>{const previous=new Map(current.map(job=>[job.id,job]));const merged=nextJobs.items.map(job=>{const existing=previous.get(job.id);return existing&&sameJobSnapshot(existing,job)?existing:job});return merged.length===current.length&&merged.every((job,index)=>job===current[index])?current:merged});setHealth(nextHealth);setConnectionError('')}catch(error){if(sequence===refreshSequence.current)setConnectionError((error as Error).message)}},[authenticated])
 useEffect(()=>{let active=true;void (async()=>{try{const status=await api.auth();const legacy=sessionStorage.getItem('audio-intel:key');if(status.required&&!status.authenticated&&legacy){try{await api.login(legacy);status.authenticated=true}finally{sessionStorage.removeItem('audio-intel:key')}}if(active)setAuth(status)}catch(error){if(active)setAuthError((error as Error).message)}})();const unauthorized=()=>setAuth(current=>current?{...current,authenticated:false}:{required:true,authenticated:false});addEventListener('audio-intel:unauthorized',unauthorized);return()=>{active=false;removeEventListener('audio-intel:unauthorized',unauthorized)}},[])
 useEffect(()=>{if(!authenticated)return;void refresh();const timer=setInterval(()=>void refresh(),2000);return()=>clearInterval(timer)},[authenticated,refresh])
 useEffect(()=>{if(!authenticated)return;void Promise.all([api.capabilities().then(setCapabilities),refreshVoiceprints()]).catch(error=>setConnectionError((error as Error).message))},[authenticated,refreshVoiceprints])
 const hasPendingVoiceprint=voiceprints.some(person=>person.samples.some(sample=>sample.state==='pending'))
 useEffect(()=>{if(!hasPendingVoiceprint)return;const timer=setInterval(()=>void refreshVoiceprints(),2000);return()=>clearInterval(timer)},[hasPendingVoiceprint,refreshVoiceprints])
 useEffect(()=>{const onHash=()=>setPage(pageFromHash());addEventListener('hashchange',onHash);return()=>removeEventListener('hashchange',onHash)},[])
 const navigate=(next:Page)=>{setPage(next);if(location.hash!==`#${next}`)location.hash=next}
 const openJob=(job:Job)=>{if(job.state==='succeeded'){setSelected(current=>({...current,[job.kind]:job.id}));setReveal({kind:job.kind,jobId:job.id,token:++revealSequence.current});navigate(job.kind)}else navigate('jobs')}
 const onRevealHandled=useCallback((token:number)=>setReveal(current=>current?.token===token?undefined:current),[])
 const onJobSubmitted=useCallback((job:Job)=>{refreshSequence.current+=1;setJobs(current=>[job,...current.filter(item=>item.id!==job.id)].slice(0,jobLimit));setConnectionError('')},[])
 const onJobUpdated=useCallback((snapshot:Job)=>setJobs(current=>current.map(job=>job.id===snapshot.id?snapshot:job)),[])
 const onJobResultUpdated=useCallback((jobId:string,result:JobResult)=>setJobs(current=>current.map(job=>job.id===jobId?{...job,result,updated_at:new Date().toISOString()}:job)),[])
 const gpuAvailable=health?Boolean(health.hardware.gpu):undefined
 const login=async(key:string)=>{await api.login(key);setAuth({required:true,authenticated:true});setAuthError('')}
 const logout=async()=>{await api.logout();setJobs([]);setHealth(undefined);setCapabilities(undefined);setVoiceprints([]);setAuth({required:true,authenticated:false})}
 return <><AppShell page={page} setPage={navigate} services={health?.services||[]} connectionError={connectionError} authRequired={auth?.required&&authenticated} onLogout={()=>void logout()}>
  {page==='asr'?<AsrPage jobs={jobs} onJobSubmitted={onJobSubmitted} onJobResultUpdated={onJobResultUpdated} selectedJobId={selected.asr} onSelect={openJob} gpuAvailable={gpuAvailable} maxSpeakers={capabilities?.asr.speaker_count.max||15} voiceprints={voiceprints} refreshVoiceprints={refreshVoiceprints} revealRequest={reveal?.kind==='asr'?reveal:undefined} onRevealHandled={onRevealHandled}/>:null}
  {page==='tts'?<TtsPage jobs={jobs} onJobSubmitted={onJobSubmitted} selectedJobId={selected.tts} onSelect={openJob} gpuAvailable={gpuAvailable} voiceprints={voiceprints} revealRequest={reveal?.kind==='tts'?reveal:undefined} onRevealHandled={onRevealHandled}/>:null}
  {page==='voiceprints'?<VoiceprintsPage people={voiceprints} refresh={refreshVoiceprints} onJobSubmitted={onJobSubmitted} gpuAvailable={gpuAvailable}/>:null}
  {page==='jobs'?<JobsPage jobs={jobs} refresh={refresh} openJob={openJob} onJobUpdated={onJobUpdated}/>:null}
  {page==='system'?<SystemPage health={health}/>:null}
 </AppShell>{!auth||!authenticated?<AuthGate loading={!auth&&!authError} error={authError} onLogin={login}/>:null}</>
}
