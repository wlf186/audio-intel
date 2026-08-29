import {useCallback,useEffect,useMemo,useRef,useState} from 'react'
import {AppShell,type Page} from './components/AppShell'
import {api} from './lib/api'
import type {AuthSession,Capabilities,Health,HotwordList,Job,JobHistoryQuery,JobResult,ResourceState,ResultRevealRequest,VoiceprintPerson} from './lib/types'
import {AsrPage} from './pages/AsrPage'
import {TtsPage} from './pages/TtsPage'
import {JobsPage} from './pages/JobsPage'
import {SystemPage} from './pages/SystemPage'
import {VoiceprintsPage} from './pages/VoiceprintsPage'
import {HotwordsPage} from './pages/HotwordsPage'
import {AuthGate} from './components/AuthGate'
import {newestJobsFirst} from './lib/jobs'

const pages=new Set<Page>(['asr','tts','hotwords','voiceprints','jobs','system'])
const jobLimit=100
const initialJobHistoryQuery:JobHistoryQuery={kind:'all',state:'all',search:'',limit:25,offset:0}
function pageFromHash():Page{const value=location.hash.slice(1) as Page;return pages.has(value)?value:'asr'}
function sameJobSnapshot(current:Job,next:Job){return current.updated_at===next.updated_at&&current.state===next.state&&current.stage===next.stage&&current.progress===next.progress&&current.attempts===next.attempts&&current.error_message===next.error_message&&current.started_at===next.started_at&&current.finished_at===next.finished_at}

export default function App(){
 const [page,setPage]=useState<Page>(pageFromHash)
 const [jobs,setJobs]=useState<Job[]>([])
 const [health,setHealth]=useState<Health>()
 const [capabilities,setCapabilities]=useState<Capabilities>()
 const [voiceprints,setVoiceprints]=useState<VoiceprintPerson[]>([])
 const [hotwordLists,setHotwordLists]=useState<HotwordList[]>([])
 const [connectionError,setConnectionError]=useState('')
 const [systemError,setSystemError]=useState('')
 const [auth,setAuth]=useState<AuthSession>()
 const [authError,setAuthError]=useState('')
 const [voiceprintsState,setVoiceprintsState]=useState<ResourceState>('loading')
 const [hotwordsState,setHotwordsState]=useState<ResourceState>('loading')
 const [eventsConnected,setEventsConnected]=useState(false)
 const [selected,setSelected]=useState<Partial<Record<Job['kind'],string>>>({})
 const [pinnedJobs,setPinnedJobs]=useState<Partial<Record<Job['kind'],Job>>>({})
 const [jobHistoryQuery,setJobHistoryQuery]=useState<JobHistoryQuery>(initialJobHistoryQuery)
 const [reveal,setReveal]=useState<(ResultRevealRequest&{kind:Job['kind']})>()
 const refreshSequence=useRef(0)
 const revealSequence=useRef(0)
 const refreshVoiceprints=useCallback(async()=>{setVoiceprintsState(current=>current==='ready'?'ready':'loading');try{const response=await api.voiceprints();setVoiceprints(response.items);setVoiceprintsState('ready')}catch(error){setVoiceprintsState('error');throw error}},[])
 const refreshHotwordLists=useCallback(async()=>{setHotwordsState(current=>current==='ready'?'ready':'loading');try{const response=await api.hotwordLists();setHotwordLists(response.items);setHotwordsState('ready')}catch(error){setHotwordsState('error');throw error}},[])
 const refreshPeopleAndHotwords=useCallback(async()=>{await Promise.all([refreshVoiceprints(),refreshHotwordLists()])},[refreshHotwordLists,refreshVoiceprints])
 const authenticated=auth?.authenticated===true
 const mergeJobs=useCallback((items:Job[])=>setJobs(current=>{const previous=new Map(current.map(job=>[job.id,job]));const merged=newestJobsFirst(items).map(job=>{const existing=previous.get(job.id);return existing&&sameJobSnapshot(existing,job)?existing:job});return merged.length===current.length&&merged.every((job,index)=>job===current[index])?current:merged}),[])
 const workspaceJobs=useMemo(()=>newestJobsFirst([...jobs,...Object.values(pinnedJobs).filter((job):job is Job=>Boolean(job)&&!jobs.some(item=>item.id===job.id))]),[jobs,pinnedJobs])
 const refreshJobs=useCallback(async()=>{if(!authenticated)return;const sequence=++refreshSequence.current;try{const response=await api.jobs();if(sequence===refreshSequence.current){mergeJobs(response.items);setConnectionError('')}}catch(error){if(sequence===refreshSequence.current)setConnectionError((error as Error).message)}},[authenticated,mergeJobs])
 const refreshSystem=useCallback(async()=>{if(!authenticated)return;try{setHealth(await api.system());setSystemError('')}catch(error){setSystemError((error as Error).message)}},[authenticated])
 const refresh=useCallback(async()=>{await Promise.all([refreshJobs(),refreshSystem()])},[refreshJobs,refreshSystem])
 useEffect(()=>{let active=true;void (async()=>{try{const status=await api.auth();const legacy=sessionStorage.getItem('audio-intel:key');if(status.required&&!status.authenticated&&legacy){try{await api.login(legacy);status.authenticated=true}finally{sessionStorage.removeItem('audio-intel:key')}}if(active)setAuth(status)}catch(error){if(active)setAuthError((error as Error).message)}})();const unauthorized=()=>setAuth(current=>current?{...current,authenticated:false}:{required:true,authenticated:false});addEventListener('audio-intel:unauthorized',unauthorized);return()=>{active=false;removeEventListener('audio-intel:unauthorized',unauthorized)}},[])
 useEffect(()=>{if(!authenticated)return;void refresh()},[authenticated,refresh])
 useEffect(()=>{if(!authenticated)return;const timer=setInterval(()=>void refreshSystem(),page==='system'?2000:10000);return()=>clearInterval(timer)},[authenticated,page,refreshSystem])
 useEffect(()=>{if(!authenticated||capabilities?.events?.sse!==true){setEventsConnected(false);return}const source=new EventSource(capabilities.events.global_url);const snapshot=(event:MessageEvent<string>)=>{try{const payload=JSON.parse(event.data) as {jobs?:Job[]};if(payload.jobs)mergeJobs(payload.jobs);setEventsConnected(true);setConnectionError('')}catch{setConnectionError('任务事件格式无效')}};source.addEventListener('snapshot',snapshot as EventListener);source.addEventListener('heartbeat',()=>setEventsConnected(true));source.onopen=()=>setEventsConnected(true);source.onerror=()=>setEventsConnected(false);return()=>{source.close();setEventsConnected(false)}},[authenticated,capabilities?.events?.sse,capabilities?.events?.global_url,mergeJobs])
 useEffect(()=>{if(!authenticated||eventsConnected)return;const timer=setInterval(()=>void refreshJobs(),5000);return()=>clearInterval(timer)},[authenticated,eventsConnected,refreshJobs])
 const refreshResources=useCallback(async()=>{
  if(!authenticated)return
  const loadCapabilities=async()=>setCapabilities(await api.capabilities())
  const results=await Promise.allSettled([loadCapabilities(),refreshVoiceprints(),refreshHotwordLists()])
  const failure=results.find((result):result is PromiseRejectedResult=>result.status==='rejected')
  setConnectionError(failure?(failure.reason as Error).message:'')
 },[authenticated,refreshHotwordLists,refreshVoiceprints])
 useEffect(()=>{void refreshResources()},[refreshResources])
 const hasPendingVoiceprint=voiceprints.some(person=>person.samples.some(sample=>sample.state==='pending'))
 useEffect(()=>{if(!hasPendingVoiceprint)return;const timer=setInterval(()=>void refreshVoiceprints(),2000);return()=>clearInterval(timer)},[hasPendingVoiceprint,refreshVoiceprints])
 useEffect(()=>{const onHash=()=>setPage(pageFromHash());addEventListener('hashchange',onHash);return()=>removeEventListener('hashchange',onHash)},[])
 const navigate=(next:Page)=>{setPage(next);if(location.hash!==`#${next}`)location.hash=next}
 const openJob=(job:Job)=>{if(job.state==='succeeded'){setPinnedJobs(current=>({...current,[job.kind]:job}));setSelected(current=>({...current,[job.kind]:job.id}));setReveal({kind:job.kind,jobId:job.id,token:++revealSequence.current});navigate(job.kind)}else navigate('jobs')}
 const onRevealHandled=useCallback((token:number)=>setReveal(current=>current?.token===token?undefined:current),[])
 const onJobSubmitted=useCallback((job:Job)=>{refreshSequence.current+=1;setJobs(current=>newestJobsFirst([job,...current.filter(item=>item.id!==job.id)]).slice(0,jobLimit));setConnectionError('')},[])
 const onJobUpdated=useCallback((snapshot:Job)=>{setJobs(current=>current.map(job=>job.id===snapshot.id?snapshot:job));setPinnedJobs(current=>current[snapshot.kind]?.id===snapshot.id?{...current,[snapshot.kind]:snapshot}:current)},[])
 const onJobResultUpdated=useCallback((jobId:string,result:JobResult)=>{const updatedAt=new Date().toISOString();setJobs(current=>current.map(job=>job.id===jobId?{...job,result,updated_at:updatedAt}:job));setPinnedJobs(current=>{const entry=Object.values(current).find(job=>job?.id===jobId);return entry?{...current,[entry.kind]:{...entry,result,updated_at:updatedAt}}:current})},[])
 const onJobsRemoved=useCallback((ids:string[])=>{const removed=new Set(ids);setJobs(current=>current.filter(job=>!removed.has(job.id)));setPinnedJobs(current=>{const next={...current};for(const kind of ['asr','tts'] as const)if(next[kind]&&removed.has(next[kind]!.id))delete next[kind];return next});setSelected(current=>{const next={...current};for(const kind of ['asr','tts'] as const)if(next[kind]&&removed.has(next[kind]!))delete next[kind];return next})},[])
 const gpuAvailable=health?Boolean(health.hardware.gpu):undefined
 const login=async(key:string)=>{await api.login(key);setVoiceprintsState('loading');setHotwordsState('loading');setAuth({required:true,authenticated:true});setAuthError('')}
 const logout=async()=>{await api.logout();setJobs([]);setPinnedJobs({});setSelected({});setHealth(undefined);setSystemError('');setCapabilities(undefined);setVoiceprints([]);setVoiceprintsState('loading');setHotwordLists([]);setHotwordsState('loading');setAuth({required:true,authenticated:false})}
 const retryConnection=()=>{setConnectionError('');void Promise.all([refresh(),refreshResources()])}
 return <><AppShell page={page} setPage={navigate} health={health} systemError={systemError} connectionError={connectionError} authRequired={auth?.required&&authenticated} onLogout={()=>void logout()} onRetry={authenticated?retryConnection:undefined}>
  {authenticated&&page==='asr'?<AsrPage jobs={workspaceJobs} onJobSubmitted={onJobSubmitted} onJobResultUpdated={onJobResultUpdated} selectedJobId={selected.asr} onSelect={openJob} gpuAvailable={gpuAvailable} maxSpeakers={capabilities?.asr.speaker_count.max||15} asrLanguages={capabilities?.asr.languages} alignerLanguages={capabilities?.asr.aligner_languages} asrModels={capabilities?.asr.models||[]} hotwordLists={hotwordLists} hotwordLimits={capabilities?.asr.hotword_library} voiceprints={voiceprints} refreshVoiceprints={refreshVoiceprints} refreshPeopleAndHotwords={refreshPeopleAndHotwords} revealRequest={reveal?.kind==='asr'?reveal:undefined} onRevealHandled={onRevealHandled}/>:null}
  {authenticated&&page==='tts'?<TtsPage jobs={workspaceJobs} onJobSubmitted={onJobSubmitted} selectedJobId={selected.tts} onSelect={openJob} gpuAvailable={gpuAvailable} voiceprints={voiceprints} asrModels={capabilities?.asr.models||[]} ttsModels={capabilities?.tts?.model_capabilities||[]} ttsLanguages={capabilities?.tts?.languages} referenceLanguages={capabilities?.asr.aligner_languages} revealRequest={reveal?.kind==='tts'?reveal:undefined} onRevealHandled={onRevealHandled}/>:null}
  {authenticated&&page==='hotwords'?<HotwordsPage items={hotwordLists} state={hotwordsState} limits={capabilities?.asr.hotword_library} refresh={refreshHotwordLists}/>:null}
  {authenticated&&page==='voiceprints'?<VoiceprintsPage people={voiceprints} state={voiceprintsState} refresh={refreshVoiceprints} refreshPeopleAndHotwords={refreshPeopleAndHotwords} onJobSubmitted={onJobSubmitted} gpuAvailable={gpuAvailable} asrModels={capabilities?.asr.models||[]} asrLanguages={capabilities?.asr.languages}/>:null}
  {authenticated&&page==='jobs'?<JobsPage liveJobs={jobs} query={jobHistoryQuery} setQuery={setJobHistoryQuery} refreshRecentJobs={refreshJobs} openJob={openJob} onJobUpdated={onJobUpdated} onJobsRemoved={onJobsRemoved}/>:null}
  {authenticated&&page==='system'?<SystemPage health={health} systemError={systemError} retry={refreshSystem}/>:null}
 </AppShell>{!auth||!authenticated?<AuthGate loading={!auth&&!authError} error={authError} onLogin={login}/>:null}</>
}
