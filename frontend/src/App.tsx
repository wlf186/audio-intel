import {videoResumeKey} from './lib/videoUpload'
import {clearWaveforms} from './lib/waveforms'
import {useCallback,useEffect,useMemo,useRef,useState} from 'react'
import {AppShell,type Page} from './components/AppShell'
import {api,HttpError} from './lib/api'
import {activateWorkspaceSession,clearWorkspaceSession,clearPeopleSession,checkpointWorkspace,readAsrSession,removeJobSession,saveAsrSession} from './lib/workspaceSession'
import {personFromHash,type NavigationGuard} from './lib/navigation'
import type {LibraryMode,AuthSession,Capabilities,Health,HotwordList,Job,JobDetailResource,JobHistoryQuery,JobResult,JobSummary,ResourceState,ResultRevealRequest,VoiceprintGroup,VoiceprintPerson} from './lib/types'
import {AsrPage} from './pages/AsrPage'
import {TtsPage} from './pages/TtsPage'
import {JobsPage} from './pages/JobsPage'
import {SystemPage} from './pages/SystemPage'
import {VoiceprintsPage} from './pages/VoiceprintsPage'
import {HotwordsPage} from './pages/HotwordsPage'
import {LibraryPage} from './pages/LibraryPage'
import {resetLibraryPreferences} from './lib/preferences'
import {AuthGate} from './components/AuthGate'
import {ResourceStatePanel} from './components/ResourceStatePanel'
import {newestJobsFirst} from './lib/jobs'
import {useTranslation} from 'react-i18next'

const pages=new Set<Page>(['asr','tts','hotwords','voiceprints','jobs','system'])
const jobLimit=100
const initialJobHistoryQuery:JobHistoryQuery={kind:'all',state:'all',search:'',limit:25,offset:0}
function pageFromHash(current=location.hash):Page{const hash=current.slice(1);const value=(hash==='library'?'voiceprints':hash.startsWith('library/')?hash.split('/')[1]:hash) as Page;return pages.has(value)?value:'asr'}
function resetPageScroll(){document.querySelector<HTMLElement>('#main-content')?.scrollTo({top:0,left:0,behavior:'auto'});window.scrollTo({top:0,left:0,behavior:'auto'})}
function sameJobSnapshot(current:JobSummary,next:JobSummary){return current.updated_at===next.updated_at&&current.state===next.state&&current.stage===next.stage&&current.progress===next.progress&&current.attempts===next.attempts&&current.error_code===next.error_code&&current.error_message===next.error_message&&current.started_at===next.started_at&&current.finished_at===next.finished_at}
function jobVersion(job:JobSummary){return job.updated_at||job.created_at}
function summaryOf(job:Job):JobSummary{const {request:_,result:__,...summary}=job;return summary}

export default function App(){
 const {t}=useTranslation()
 const [page,setPage]=useState<Page>(pageFromHash)
 const [personId,setPersonId]=useState(personFromHash)
 const navigationGuard=useRef<NavigationGuard|undefined>(undefined)
 const acceptedLocation=useRef({hash:location.hash,index:typeof history.state?.audio_intel_index==='number'?history.state.audio_intel_index:0})
 const allowedHash=useRef<string|undefined>(undefined)
 const resolvingHistory=useRef(false)
 const pendingHistoryGuard=useRef<(()=>void)|undefined>(undefined)
 const registerNavigationGuard=useCallback((guard?:NavigationGuard)=>{navigationGuard.current=guard},[])
 const lastLibraryTab=useRef<'voiceprints'|'hotwords'>(page==='hotwords'?'hotwords':'voiceprints')
 const [libraryEpoch,setLibraryEpoch]=useState(0)
 const [libraryError,setLibraryError]=useState('')
 const [jobs,setJobs]=useState<JobSummary[]>([])
 const [jobsReady,setJobsReady]=useState(false)
 const [health,setHealth]=useState<Health>()
 const [capabilities,setCapabilities]=useState<Capabilities>()
 const [voiceprintGroups,setVoiceprintGroups]=useState<VoiceprintGroup[]>([])
 const [voiceprintGroupsState,setVoiceprintGroupsState]=useState<ResourceState>('loading')
 const [voiceprints,setVoiceprints]=useState<VoiceprintPerson[]>([])
 const [hotwordLists,setHotwordLists]=useState<HotwordList[]>([])
 const [jobsError,setJobsError]=useState('')
 const [capabilitiesError,setCapabilitiesError]=useState('')
 const [capabilitiesState,setCapabilitiesState]=useState<ResourceState>('loading')
 const [systemError,setSystemError]=useState('')
 const [auth,setAuth]=useState<AuthSession>()
 const [authError,setAuthError]=useState('')
 const [voiceprintsState,setVoiceprintsState]=useState<ResourceState>('loading')
 const [hotwordsState,setHotwordsState]=useState<ResourceState>('loading')
 const [eventsConnected,setEventsConnected]=useState(false)
 const [selected,setSelected]=useState<Partial<Record<JobSummary['kind'],string>>>(()=>({asr:readAsrSession().jobId}))
 const [restoreState,setRestoreState]=useState<ResourceState>('loading')
 const [restoreError,setRestoreError]=useState('')
 const restoreRequest=useRef<string|undefined>(undefined)
 const authGeneration=useRef(0)
 const removedJobs=useRef(new Set<string>())
 const [pinnedJobs,setPinnedJobs]=useState<Partial<Record<JobSummary['kind'],JobSummary>>>({})
 const [jobDetails,setJobDetails]=useState<Record<string,JobDetailResource>>({})
 const [jobHistoryQuery,setJobHistoryQuery]=useState<JobHistoryQuery>(initialJobHistoryQuery)
 const [reveal,setReveal]=useState<(ResultRevealRequest&{kind:JobSummary['kind']})>()
 const refreshSequence=useRef(0)
 const revealSequence=useRef(0)
 const detailRef=useRef<Record<string,JobDetailResource>>({})
 const detailRequests=useRef(new Map<string,Promise<Job>>())
 const refreshVoiceprintGroups=useCallback(async()=>{setVoiceprintGroupsState(current=>current==='ready'?'ready':'loading');try{const response=await api.voiceprintGroups();setVoiceprintGroups(response.items);setVoiceprintGroupsState('ready')}catch(error){setVoiceprintGroupsState('error');throw error}},[])
 const refreshVoiceprints=useCallback(async()=>{const people=async()=>{setVoiceprintsState(current=>current==='ready'?'ready':'loading');try{const response=await api.voiceprints();setVoiceprints(response.items);setVoiceprintsState('ready')}catch(error){setVoiceprintsState('error');throw error}};const results=await Promise.allSettled([people(),refreshVoiceprintGroups()]);const failed=results.find(result=>result.status==='rejected');if(failed?.status==='rejected')throw failed.reason},[refreshVoiceprintGroups])
 const refreshHotwordLists=useCallback(async()=>{setHotwordsState(current=>current==='ready'?'ready':'loading');try{const response=await api.hotwordLists();setHotwordLists(response.items);setHotwordsState('ready')}catch(error){setHotwordsState('error');throw error}},[])
 const refreshPeopleAndHotwords=useCallback(async()=>{await Promise.all([refreshVoiceprints(),refreshHotwordLists()])},[refreshHotwordLists,refreshVoiceprints])
 const onLibraryImported=useCallback(async(mode:LibraryMode)=>{if(mode==='replace'){clearPeopleSession();if(personFromHash()){history.replaceState(history.state,'','#library/voiceprints');acceptedLocation.current.hash='#library/voiceprints';setPersonId(undefined)}resetLibraryPreferences();window.dispatchEvent(new Event('audio-intel:library-replaced'));setLibraryEpoch(value=>value+1)}await refreshPeopleAndHotwords()},[refreshPeopleAndHotwords])
 const authenticated=auth?.authenticated===true
 useEffect(()=>{
  if(!authenticated)return
  let cancelled=false
  let timer:ReturnType<typeof setTimeout>
  const monitor=async()=>{
   try{const id=sessionStorage.getItem('audio-intel:library-import:v1');if(id){const receipt=await api.libraryImport(id);if(!cancelled&&sessionStorage.getItem('audio-intel:library-import:v1')===id&&['completed','ready','failed','expired'].includes(receipt.state)){sessionStorage.removeItem('audio-intel:library-import:v1');if(receipt.state==='completed')await onLibraryImported(receipt.mode||'merge')}}}
   catch(cause){if(!cancelled)setLibraryError((cause as Error).message)}
   if(!cancelled)timer=setTimeout(()=>void monitor(),1500)
  }
  void monitor();return()=>{cancelled=true;clearTimeout(timer)}
 },[authenticated,onLibraryImported])
 useEffect(()=>{if(page==='voiceprints'||page==='hotwords'){lastLibraryTab.current=page;const canonical=`#library/${page}${page==='voiceprints'&&personId?'/'+encodeURIComponent(personId):''}`;if(location.hash!==canonical){history.replaceState(history.state,'',canonical);acceptedLocation.current.hash=canonical}}},[page,personId])
 const mergeJobs=useCallback((items:JobSummary[])=>{const fullItems=items.filter((item):item is Job=>'request' in item);if(fullItems.length){const next={...detailRef.current};for(const item of fullItems)next[item.id]={state:'ready',version:jobVersion(item),job:item};detailRef.current=next;setJobDetails(next)}const summaries=items.map(item=>'request' in item?summaryOf(item as Job):item);setPinnedJobs(current=>{const next={...current};for(const kind of ['asr','tts'] as const){const update=summaries.find(item=>item.id===current[kind]?.id);if(update)next[kind]=update}return next});setJobs(current=>{const previous=new Map(current.map(job=>[job.id,job]));const merged=newestJobsFirst(summaries).map(job=>{const existing=previous.get(job.id);return existing&&sameJobSnapshot(existing,job)?existing:job});return merged.length===current.length&&merged.every((job,index)=>job===current[index])?current:merged})},[])
 const mergeJobUpdates=useCallback((items:JobSummary[],removedIds:string[])=>{removeJobSession(removedIds);for(const id of removedIds)removedJobs.current.add(id);setPinnedJobs(current=>{const next={...current};for(const kind of ['asr','tts'] as const){if(removedIds.includes(current[kind]?.id||''))delete next[kind];else{const update=items.find(item=>item.id===current[kind]?.id);if(update)next[kind]=update}}return next});setJobs(current=>{const removed=new Set(removedIds);const updates=new Map(items.map(job=>[job.id,job]));const merged=current.filter(job=>!removed.has(job.id)).map(job=>{const next=updates.get(job.id);if(!next)return job;updates.delete(job.id);return sameJobSnapshot(job,next)?job:next});return newestJobsFirst([...updates.values(),...merged]).slice(0,jobLimit)})},[])
 const workspaceJobs=useMemo(()=>newestJobsFirst([...jobs,...Object.values(pinnedJobs).filter((job):job is JobSummary=>Boolean(job)&&!jobs.some(item=>item.id===job.id))]),[jobs,pinnedJobs])
 const updateDetail=useCallback((id:string,resource:JobDetailResource)=>{detailRef.current={...detailRef.current,[id]:resource};setJobDetails(detailRef.current)},[])
 const loadJobDetail=useCallback((summary:JobSummary,force=false)=>{const version=jobVersion(summary);const cached=detailRef.current[summary.id];if(!force&&cached?.version===version&&(cached.state==='ready'||cached.state==='loading'))return;const key=`${summary.id}|${version}`;const generation=authGeneration.current;let request=detailRequests.current.get(key);updateDetail(summary.id,{state:'loading',version,job:cached?.job?.state==='succeeded'&&summary.state==='succeeded'?cached.job:undefined});if(!request){request=api.job(summary.id);detailRequests.current.set(key,request);request.finally(()=>detailRequests.current.delete(key)).catch(()=>undefined)}void request.then(job=>{if(generation===authGeneration.current)updateDetail(summary.id,{state:'ready',version,job})}).catch(error=>{if(generation===authGeneration.current)updateDetail(summary.id,{state:'error',version,error:(error as Error).message})})},[updateDetail])
 const refreshJobs=useCallback(async()=>{
  if(!authenticated)return
  const sequence=++refreshSequence.current
  try{
   const response=await api.jobs()
   if(sequence===refreshSequence.current){mergeJobs(response.items);setJobsReady(true);setJobsError('')}
  }catch(error){if(sequence===refreshSequence.current)setJobsError((error as Error).message)}
 },[authenticated,mergeJobs])
 const restoreSelectedAsr=useCallback((force=false)=>{
  const id=selected.asr
  if(!authenticated||!id||(!force&&restoreRequest.current===id))return
  if(removedJobs.current.has(id)){restoreRequest.current=id;setRestoreState('ready');return}
  restoreRequest.current=id
  const generation=authGeneration.current
  setRestoreState('loading');setRestoreError('')
  void api.job(id).then(job=>{
   if(generation!==authGeneration.current||restoreRequest.current!==id)return
   setPinnedJobs(current=>({...current,asr:summaryOf(job)}));updateDetail(id,{state:'ready',version:jobVersion(job),job});setRestoreState('ready')
  }).catch(cause=>{
   if(generation!==authGeneration.current||restoreRequest.current!==id)return
   if(cause instanceof HttpError&&cause.status===404){removeJobSession([id]);setRestoreState('ready')}
   else{setRestoreError((cause as Error).message);setRestoreState('error')}
  })
 },[authenticated,selected.asr,updateDetail])
 const refreshSystem=useCallback(async()=>{if(!authenticated)return;try{const response=await api.system();setHealth({...response,deployment:response.deployment||{profile:'full',default_compute_device:'gpu',gpu_runtime_installed:true}});setSystemError('')}catch(error){setSystemError((error as Error).message)}},[authenticated])
 const refresh=useCallback(async()=>{await Promise.all([refreshJobs(),refreshSystem()])},[refreshJobs,refreshSystem])
 useEffect(()=>{let active=true;void (async()=>{try{const status=await api.auth();const legacy=sessionStorage.getItem('audio-intel:key');if(status.required&&!status.authenticated&&legacy){try{await api.login(legacy);status.authenticated=true}finally{sessionStorage.removeItem('audio-intel:key')}}if(active){if(status.authenticated)activateWorkspaceSession();else{clearWorkspaceSession();authGeneration.current++;restoreRequest.current=undefined;setRestoreState('loading');setRestoreError('');setSelected({})}setAuth(status)}}catch(error){if(active)setAuthError((error as Error).message)}})();const unauthorized=()=>{clearWorkspaceSession();authGeneration.current++;restoreRequest.current=undefined;setRestoreState('loading');setRestoreError('');setSelected({});setJobsReady(false);setAuth(current=>current?{...current,authenticated:false}:{required:true,authenticated:false})};addEventListener('audio-intel:unauthorized',unauthorized);return()=>{active=false;removeEventListener('audio-intel:unauthorized',unauthorized)}},[])
 useEffect(()=>{if(!authenticated)return;void refresh()},[authenticated,refresh])
 useEffect(()=>{if(!authenticated)return;const timer=setInterval(()=>void refreshSystem(),page==='system'?2000:10000);return()=>clearInterval(timer)},[authenticated,page,refreshSystem])
 useEffect(()=>{if(!authenticated||capabilities?.events?.sse!==true){setEventsConnected(false);return}const source=new EventSource(capabilities.events.global_url);const connected=()=>{setEventsConnected(true);setJobsError('')};const snapshot=(event:MessageEvent<string>)=>{try{const payload=JSON.parse(event.data) as {jobs?:JobSummary[]};if(payload.jobs){mergeJobs(payload.jobs);setJobsReady(true)}connected()}catch{setJobsError(t('errors.invalidTaskEvent'))}};const update=(event:MessageEvent<string>)=>{try{const payload=JSON.parse(event.data) as {jobs?:JobSummary[];removed_job_ids?:string[]};mergeJobUpdates(payload.jobs||[],payload.removed_job_ids||[]);connected()}catch{setJobsError(t('errors.invalidTaskEvent'))}};source.addEventListener('snapshot',snapshot as EventListener);source.addEventListener('update',update as EventListener);source.addEventListener('heartbeat',connected);source.onopen=()=>setEventsConnected(true);source.onerror=()=>setEventsConnected(false);return()=>{source.close();setEventsConnected(false)}},[authenticated,capabilities?.events?.sse,capabilities?.events?.global_url,mergeJobs,mergeJobUpdates,t])
 useEffect(()=>{if(!authenticated||eventsConnected)return;const timer=setInterval(()=>void refreshJobs(),5000);return()=>clearInterval(timer)},[authenticated,eventsConnected,refreshJobs])
 const refreshResources=useCallback(async()=>{
  if(!authenticated)return
  const loadCapabilities=async()=>{setCapabilities(undefined);setCapabilitiesState('loading');setCapabilities(await api.capabilities());setCapabilitiesState('ready')}
  const results=await Promise.allSettled([loadCapabilities(),refreshVoiceprints(),refreshHotwordLists()])
  const capabilityResult=results[0]
  setCapabilitiesError(capabilityResult.status==='rejected'?(capabilityResult.reason as Error).message:'')
  if(capabilityResult.status==='rejected')setCapabilitiesState('error')
 },[authenticated,refreshHotwordLists,refreshVoiceprints])
 useEffect(()=>{void refreshResources()},[refreshResources])
 const hasPendingVoiceprint=voiceprints.some(person=>person.samples.some(sample=>sample.state==='pending'))
 useEffect(()=>{if(!authenticated||!hasPendingVoiceprint)return;const timer=setInterval(()=>void refreshVoiceprints().catch(()=>undefined),2000);return()=>clearInterval(timer)},[authenticated,hasPendingVoiceprint,refreshVoiceprints])
 const acceptHash=useCallback((hash:string,index:number,preserveScroll=false)=>{
  checkpointWorkspace();acceptedLocation.current={hash,index}
  if(!preserveScroll)resetPageScroll()
  setPage(pageFromHash(hash));setPersonId(personFromHash(hash))
 },[])
 useEffect(()=>{
  history.replaceState({...history.state,audio_intel_index:acceptedLocation.current.index},'',location.href)
  const onHash=()=>{
   const hash=location.hash,old=acceptedLocation.current
   if(hash===old.hash){resolvingHistory.current=false;const request=pendingHistoryGuard.current;pendingHistoryGuard.current=undefined;request?.();return}
   if(resolvingHistory.current)return
   const index=typeof history.state?.audio_intel_index==='number'?history.state.audio_intel_index:old.index+1
   if(allowedHash.current===hash){allowedHash.current=undefined;acceptHash(hash,index);return}
   if(navigationGuard.current){
    const delta=index-old.index
    const guard=navigationGuard.current
    if(delta){resolvingHistory.current=true;pendingHistoryGuard.current=()=>guard(()=>{allowedHash.current=hash;history.go(delta)},hash);history.go(-delta)}
    else{history.replaceState(history.state,'',old.hash||'#asr');guard(()=>{history.replaceState(history.state,'',hash);acceptHash(hash,index)},hash)}
    return
   }
   acceptHash(hash,index)
  }
  addEventListener('hashchange',onHash);addEventListener('popstate',onHash)
  return()=>{removeEventListener('hashchange',onHash);removeEventListener('popstate',onHash)}
 },[acceptHash])
 const navigateHash=useCallback((hash:string,preserveScroll=false,replace=false)=>{
  if(hash===acceptedLocation.current.hash){if(!preserveScroll)resetPageScroll();return}
  const proceed=()=>{
   const old=acceptedLocation.current
   if(hash===old.hash)return
   const index=old.index+(replace?0:1)
   const state={...history.state,audio_intel_index:index,audio_intel_from:old.hash}
   if(replace)history.replaceState(state,'',hash);else history.pushState(state,'',hash)
   acceptHash(hash,index,preserveScroll)
  }
  if(navigationGuard.current)navigationGuard.current(proceed,hash);else proceed()
 },[acceptHash])
 const navigate=(requested:Page,preserveScroll=false)=>{const next=requested==='library'?lastLibraryTab.current:requested;navigateHash(next==='voiceprints'||next==='hotwords'?`#library/${next}`:`#${next}`,preserveScroll)}
 const openPerson=(id:string)=>navigateHash(`#library/voiceprints/${encodeURIComponent(id)}`)
 const closePerson=()=>{if(history.state?.audio_intel_from==='#library/voiceprints')history.back();else navigateHash('#library/voiceprints',false,true)}
 const openJob=(job:JobSummary)=>{if(job.state==='succeeded'){checkpointWorkspace();if(job.kind==='asr')saveAsrSession({jobId:job.id,view:'results'});setPinnedJobs(current=>({...current,[job.kind]:job}));setSelected(current=>({...current,[job.kind]:job.id}));loadJobDetail(job);setReveal({kind:job.kind,jobId:job.id,token:++revealSequence.current});navigate(job.kind,true)}else navigate('jobs')}
 const selectTtsJob=useCallback((job:JobSummary)=>{setSelected(current=>({...current,tts:job.id}));const latest=jobs.find(item=>item.id===job.id);if(latest)setPinnedJobs(current=>({...current,tts:latest}))},[jobs])
 const selectAsrJob=useCallback((job:JobSummary)=>{restoreRequest.current=undefined;setRestoreState('loading');checkpointWorkspace();saveAsrSession({jobId:job.id});setSelected(current=>({...current,asr:job.id}));const latest=jobs.find(item=>item.id===job.id);if(latest)setPinnedJobs(current=>({...current,asr:latest}))},[jobs])
 const manageAsrJobs=()=>{setJobHistoryQuery({...initialJobHistoryQuery,kind:'asr'});navigate('jobs')}
 const manageTtsJobs=()=>{setJobHistoryQuery({...initialJobHistoryQuery,kind:'tts'});navigate('jobs')}
 const onRevealHandled=useCallback((token:number)=>setReveal(current=>current?.token===token?undefined:current),[])
 const onJobSubmitted=useCallback((job:Job)=>{refreshSequence.current+=1;const summary=summaryOf(job);if(job.kind==='tts'){setSelected(current=>({...current,tts:job.id}));setPinnedJobs(current=>({...current,tts:summary}))}setJobs(current=>newestJobsFirst([summary,...current.filter(item=>item.id!==job.id)]).slice(0,jobLimit));setJobsError('')},[])
 const onJobUpdated=useCallback((snapshot:JobSummary)=>{const summary='request' in snapshot?summaryOf(snapshot as Job):snapshot;setJobs(current=>current.map(job=>job.id===summary.id?summary:job));setPinnedJobs(current=>current[summary.kind]?.id===summary.id?{...current,[summary.kind]:summary}:current)},[])
 const onJobResultUpdated=useCallback((jobId:string,result:JobResult)=>{const current=detailRef.current[jobId];if(current?.job)updateDetail(jobId,{...current,job:{...current.job,result}})},[updateDetail])
 const onJobsRemoved=useCallback((ids:string[])=>{removeJobSession(ids);for(const id of ids)removedJobs.current.add(id);const removed=new Set(ids);setJobs(current=>current.filter(job=>!removed.has(job.id)));setPinnedJobs(current=>{const next={...current};for(const kind of ['asr','tts'] as const)if(next[kind]&&removed.has(next[kind]!.id))delete next[kind];return next})},[])
 const gpuAvailable=health?Boolean(health.hardware.gpu):undefined
 const login=async(key:string)=>{await api.login(key);activateWorkspaceSession();setJobsReady(false);setVoiceprintsState('loading');setVoiceprintGroupsState('loading');setHotwordsState('loading');setAuth({required:true,authenticated:true});setAuthError('')}
 const logout=async()=>{await api.logout();clearWorkspaceSession();authGeneration.current++;restoreRequest.current=undefined;setRestoreState('loading');setRestoreError('');clearWaveforms();sessionStorage.removeItem('audio-intel:document-draft');sessionStorage.removeItem(videoResumeKey);setJobs([]);setJobsReady(false);setPinnedJobs({});setSelected({});detailRef.current={};setJobDetails({});setHealth(undefined);setSystemError('');setCapabilities(undefined);setCapabilitiesState('loading');setJobsError('');setCapabilitiesError('');setVoiceprints([]);setVoiceprintsState('loading');setVoiceprintGroups([]);setVoiceprintGroupsState('loading');setHotwordLists([]);setHotwordsState('loading');setAuth({required:true,authenticated:false})}
 const connectionError=jobsError||capabilitiesError||libraryError
 const retryConnection=()=>{setLibraryError('');setJobsError('');setCapabilitiesError('');void Promise.all([refresh(),refreshResources()])}
 const capabilityPanel=<ResourceStatePanel state={capabilitiesState} loadingLabel={t('capabilities.loading')} errorLabel={capabilitiesError||t('capabilities.loadFailed')} retry={()=>void refreshResources()}/>
 return <><AppShell page={page} setPage={next=>navigate(next)} health={health} systemError={systemError} connectionError={connectionError} authRequired={auth?.required&&authenticated} onLogout={()=>void logout()} onRetry={authenticated?retryConnection:undefined}>
  {authenticated&&page==='asr'?(capabilities?<AsrPage jobsReady={jobsReady} jobsError={jobsError} refreshJobs={refreshJobs} refreshHotwords={refreshHotwordLists} onManageJobs={manageAsrJobs} jobs={workspaceJobs} jobDetails={jobDetails} loadJobDetail={loadJobDetail} onJobSubmitted={onJobSubmitted} onJobResultUpdated={onJobResultUpdated} selectedJobId={selected.asr} restoreState={restoreState} restoreError={restoreError} restoreSelected={restoreSelectedAsr} onSelect={selectAsrJob} gpuAvailable={gpuAvailable} defaultComputeDevice={capabilities.deployment?.default_compute_device||'gpu'} maxSpeakers={capabilities.asr.speaker_count?.max||15} maxUploadBytes={capabilities.limits.max_upload_bytes} videoCapability={capabilities.asr.video} enhancementAvailable={capabilities.asr.audio_enhancement?.speech_enhancement?.available} denoiseAvailable={capabilities.asr.audio_enhancement?.background_denoise?.available} gainStrengthCapability={capabilities.asr.audio_enhancement?.speech_enhancement?.strength} noiseStrengthCapability={capabilities.asr.audio_enhancement?.background_denoise?.strength} audioFormats={capabilities.asr.audio_formats} asrLanguages={capabilities.asr.languages} alignerLanguages={capabilities.asr.aligner_languages} asrModels={capabilities.asr.models||[]} hotwordLists={hotwordLists} hotwordsState={hotwordsState} hotwordLimits={capabilities.asr.hotword_library} voiceprints={voiceprints} voiceprintsState={voiceprintsState} voiceprintGroups={voiceprintGroups} voiceprintGroupsState={voiceprintGroupsState} refreshVoiceprintGroups={refreshVoiceprintGroups} refreshVoiceprints={refreshVoiceprints} refreshPeopleAndHotwords={refreshPeopleAndHotwords} revealRequest={reveal?.kind==='asr'?reveal:undefined} onRevealHandled={onRevealHandled}/>:capabilityPanel):null}
  {authenticated&&page==='tts'?(capabilities?<TtsPage jobsReady={jobsReady} jobsError={jobsError} refreshJobs={refreshJobs} onManageJobs={manageTtsJobs} documentCapability={capabilities.tts?.document_jobs} jobs={workspaceJobs} jobDetails={jobDetails} loadJobDetail={loadJobDetail} onJobSubmitted={onJobSubmitted} selectedJobId={selected.tts} onSelect={selectTtsJob} gpuAvailable={gpuAvailable} defaultComputeDevice={capabilities.deployment?.default_compute_device||'gpu'} maxUploadBytes={capabilities.limits.max_upload_bytes} voiceprints={voiceprints} voiceprintsState={voiceprintsState} refreshVoiceprints={refreshVoiceprints} maxCloneReferenceSeconds={capabilities.limits.max_clone_reference_seconds} asrModels={capabilities.asr.models||[]} ttsModels={capabilities.tts?.model_capabilities||[]} ttsLanguages={capabilities.tts?.languages} referenceLanguages={capabilities.asr.aligner_languages} revealRequest={reveal?.kind==='tts'?reveal:undefined} onRevealHandled={onRevealHandled}/>:capabilityPanel):null}
  {authenticated&&(page==='voiceprints'||page==='hotwords')?(capabilities?<LibraryPage tab={page} onTabChange={next=>navigate(next)} maxUploadBytes={capabilities.limits.max_upload_bytes} onImported={onLibraryImported}>{page==='voiceprints'?<VoiceprintsPage key={libraryEpoch} detailId={personId} onOpenPerson={openPerson} onBack={closePerson} registerNavigationGuard={registerNavigationGuard} people={voiceprints} groups={voiceprintGroups} groupsState={voiceprintGroupsState} refreshGroups={refreshVoiceprintGroups} maxCloneReferenceSeconds={capabilities.limits.max_clone_reference_seconds} state={voiceprintsState} refresh={refreshVoiceprints} refreshPeopleAndHotwords={refreshPeopleAndHotwords} onJobSubmitted={onJobSubmitted} gpuAvailable={gpuAvailable} defaultComputeDevice={capabilities.deployment?.default_compute_device||'gpu'} maxUploadBytes={capabilities.limits.max_upload_bytes} asrModels={capabilities.asr.models||[]} asrLanguages={capabilities.asr.languages}/>:<HotwordsPage key={libraryEpoch} items={hotwordLists} state={hotwordsState} limits={capabilities.asr.hotword_library} refresh={refreshHotwordLists}/>}</LibraryPage>:capabilityPanel):null}
  {authenticated&&page==='jobs'?<JobsPage liveJobs={jobs} liveJobsReady={jobsReady} query={jobHistoryQuery} setQuery={setJobHistoryQuery} refreshRecentJobs={refreshJobs} openJob={openJob} onJobUpdated={onJobUpdated} onJobsRemoved={onJobsRemoved}/>:null}
  {authenticated&&page==='system'?<SystemPage health={health} systemError={systemError} retry={refreshSystem}/>:null}
 </AppShell>{!auth||!authenticated?<AuthGate loading={!auth&&!authError} error={authError} onLogin={login}/>:null}</>
}
