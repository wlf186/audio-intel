import {useEffect,useMemo,useRef,useState} from 'react'
import {CircleStop,Download,Fingerprint,Mic2,RefreshCw,RotateCcw,Sparkles,Trash2,Upload} from 'lucide-react'
import {api,artifactUrl,formatTime} from '../lib/api'
import type {ComputeDevice,Job,ResultRevealRequest,VoiceprintPerson} from '../lib/types'
import {JobMini} from '../components/JobMini'
import {AudioTransport} from '../components/AudioTransport'
import {InfoTooltip} from '../components/InfoTooltip'
import {useMicrophoneRecorder} from '../hooks/useMicrophoneRecorder'
import {clearTtsPreferences,defaultTtsPreferences,loadTtsContent,loadTtsPreferences,saveTtsContent,saveTtsPreferences,type TtsContent,type TtsPreferences} from '../lib/preferences'
import {visibleWorkspaceJobs} from '../lib/jobs'

type Props={jobs:Job[];onJobSubmitted:(job:Job)=>void;selectedJobId?:string;onSelect:(job:Job)=>void;gpuAvailable?:boolean;voiceprints:VoiceprintPerson[];ttsLanguages?:string[];referenceLanguages?:string[];revealRequest?:ResultRevealRequest;onRevealHandled:(token:number)=>void}
type ReferenceSource='upload'|'record'

const fallbackTtsLanguages=['Auto','Chinese','English','Japanese','Korean','German','French','Russian','Portuguese','Spanish','Italian']
const fallbackReferenceLanguages=['Auto','Chinese','English','Cantonese','Japanese','Korean','German','French','Russian','Portuguese','Spanish','Italian']

export function TtsPage({jobs,onJobSubmitted,selectedJobId,onSelect,gpuAvailable,voiceprints,ttsLanguages=fallbackTtsLanguages,referenceLanguages=fallbackReferenceLanguages,revealRequest,onRevealHandled}:Props){
 const [preferences,setPreferences]=useState<TtsPreferences>(loadTtsPreferences)
 const [content,setContent]=useState<TtsContent>(loadTtsContent)
 const [referenceSource,setReferenceSource]=useState<ReferenceSource>('upload')
 const [referenceName,setReferenceName]=useState('')
 const [voices,setVoices]=useState<string[]>(['Vivian','Serena','Uncle_Fu','Dylan','Eric','Ryan','Aiden','Ono_Anna','Sohee'])
 const [busy,setBusy]=useState(false)
 const [referenceBusy,setReferenceBusy]=useState(false)
 const [error,setError]=useState('')
 const [notice,setNotice]=useState('')
 const fileRef=useRef<HTMLInputElement>(null)
 const preview=useRef<HTMLElement>(null)
 const recorder=useMicrophoneRecorder(30)
 const ttsJobs=useMemo(()=>jobs.filter(job=>job.kind==='tts'),[jobs])
 const selected=ttsJobs.find(job=>job.id===selectedJobId&&job.state==='succeeded')||ttsJobs.find(job=>job.state==='succeeded')
 const visibleJobs=useMemo(()=>visibleWorkspaceJobs(ttsJobs,selected?.id),[ttsJobs,selected?.id])
 const draft={...preferences,...content}
 const person=voiceprints.find(item=>item.id===draft.personId)||voiceprints[0]
 const eligibleSamples=person?.samples.filter(sample=>sample.tts_eligible)||[]
 const sample=eligibleSamples.find(item=>item.id===draft.sampleId)||eligibleSamples[0]
 const referenceJob=jobs.find(job=>job.id===draft.refJobId)
 const referenceReady=referenceJob?.state==='succeeded'&&Boolean(referenceJob.result?.text)
 const microphoneActive=recorder.phase==='requesting'||recorder.phase==='recording'
 const elapsed=Math.min(recorder.maxSeconds,Math.max(0,recorder.elapsedSeconds))
 const availableReferenceLanguages=useMemo(()=>Array.from(new Set(['Auto',...referenceLanguages])),[referenceLanguages])

 useEffect(()=>{saveTtsContent(content)},[content])
 useEffect(()=>{api.voices().then(response=>setVoices(response.preset_speakers)).catch(cause=>setError((cause as Error).message))},[])
 useEffect(()=>{if(voices.length&&!voices.includes(preferences.speaker))setPreferences(current=>{const next={...current,speaker:voices[0]};saveTtsPreferences(next);return next})},[voices,preferences.speaker])
 useEffect(()=>{if(person&&draft.personId!==person.id)setPreferences(current=>{const next={...current,personId:person.id,sampleId:person.samples.find(item=>item.tts_eligible)?.id||''};saveTtsPreferences(next);return next})},[person,draft.personId])
 useEffect(()=>{if(sample&&draft.sampleId!==sample.id)setPreferences(current=>{const next={...current,sampleId:sample.id};saveTtsPreferences(next);return next})},[sample,draft.sampleId])
 useEffect(()=>{
  if(referenceJob?.state!=='succeeded'||!referenceJob.result?.text)return
  setContent(current=>current.refJobId!==referenceJob.id||current.refText?current:{...current,refText:referenceJob.result?.text||'',refLanguage:referenceJob.result?.language||'Auto'})
 },[referenceJob?.id,referenceJob?.result?.language,referenceJob?.result?.text,referenceJob?.state])
 useEffect(()=>{if(!revealRequest||revealRequest.jobId!==selected?.id)return;const frame=requestAnimationFrame(()=>{if(matchMedia('(max-width: 900px)').matches)preview.current?.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});onRevealHandled(revealRequest.token)});return()=>cancelAnimationFrame(frame)},[onRevealHandled,revealRequest,selected?.id])

 const updatePreference=<K extends keyof TtsPreferences>(key:K,value:TtsPreferences[K])=>setPreferences(current=>{const next={...current,[key]:value};saveTtsPreferences(next);return next})
 const updateContent=<K extends keyof TtsContent>(key:K,value:TtsContent[K])=>setContent(current=>({...current,[key]:value}))
 const cancelActiveReference=async()=>{if(referenceJob&&['queued','running'].includes(referenceJob.state))await api.cancel(referenceJob.id).then(onJobSubmitted).catch(()=>undefined)}
 const analyzeReference=async(file:File)=>{
  if(draft.computeDevice==='gpu'&&gpuAvailable===false){setError('当前未检测到可用 GPU，请选择 CPU 后再分析参考音频。');return false}
  setReferenceBusy(true);setError('');setNotice('')
  try{
   await cancelActiveReference()
   const data=new FormData()
   data.set('file',file);data.set('compute_device',draft.computeDevice);data.set('accelerate_single_task',String(draft.accelerateSingleTask))
   const job=await api.analyzeCloneReference(data)
   setReferenceName(file.name)
   setContent(current=>({...current,refJobId:job.id,refText:'',refLanguage:'Auto'}))
   onJobSubmitted(job)
   setNotice('已创建克隆参考 ASR 分析任务，识别完成后可确认文本并生成。')
   return true
  }catch(cause){setError((cause as Error).message);return false}finally{setReferenceBusy(false)}
 }
 const chooseReference=(file?:File)=>{if(file)void analyzeReference(file);if(fileRef.current)fileRef.current.value=''}
 const analyzeRecording=async()=>{if(recorder.recorded&&await analyzeReference(recorder.recorded.file))recorder.discard()}
 const retryReference=async()=>{if(!referenceJob)return;setReferenceBusy(true);setError('');try{const job=await api.retry(referenceJob.id);setContent(current=>({...current,refText:'',refLanguage:'Auto'}));onJobSubmitted(job)}catch(cause){setError((cause as Error).message)}finally{setReferenceBusy(false)}}
 const clearReference=async()=>{await cancelActiveReference();setContent(current=>({...current,refJobId:'',refText:'',refLanguage:'Auto'}));setReferenceName('');recorder.discard();setNotice('已清除当前克隆参考。')}
 const submit=async()=>{
  if(!draft.text.trim()){setError('请输入需要合成的文本。');return}
  if(draft.computeDevice==='gpu'&&gpuAvailable===false){setError('当前未检测到可用 GPU，请选择 CPU。');return}
  if(draft.mode==='inline_clone'&&draft.cloneSource==='upload'&&(!referenceReady||!draft.refText.trim())){setError('请先完成参考音频的自动识别，并确认识别文本。');return}
  if(draft.mode==='inline_clone'&&draft.cloneSource==='voiceprint'&&!sample){setError('请选择一个已完成转写的声纹样本。');return}
  setBusy(true);setError('')
  try{
   const data=new FormData()
   data.set('text',draft.text);data.set('language',draft.language);data.set('response_format','wav')
   data.set('display_name',draft.text.slice(0,18)||'语音合成');data.set('compute_device',draft.computeDevice)
   data.set('accelerate_single_task',String(draft.accelerateSingleTask))
   if(draft.mode==='preset'){data.set('voice_mode','preset');data.set('speaker',draft.speaker)}
   else if(draft.cloneSource==='voiceprint'){data.set('voice_mode','voiceprint');data.set('voiceprint_sample_id',sample!.id)}
   else{data.set('voice_mode','inline_clone');data.set('reference_job_id',draft.refJobId);data.set('reference_text',draft.refText);data.set('reference_language',draft.refLanguage)}
   const job=await api.submitTts(data);onJobSubmitted(job)
  }catch(cause){setError((cause as Error).message)}finally{setBusy(false)}
 }
 const resetPreferences=()=>{const next={...defaultTtsPreferences};clearTtsPreferences();saveTtsPreferences(next);setPreferences(next);setNotice('已恢复 TTS 默认配置。');setError('')}

 return <div className="tts-grid hud-page"><section className="tts-editor" data-module="TTS_CONSOLE / SYN_02"><div className="section-title"><div><h1>语音合成</h1><p>{draft.computeDevice==='cpu'?'CPU 全精度离线生成，音质优先':'GPU 原生精度加速，无量化'}</p></div><div className="section-actions"><span className="performance-badge">{draft.computeDevice==='cpu'?(draft.accelerateSingleTask?'CPU · FP32 · SDPA · AUTO BATCH':'CPU · FP32 · SDPA · BATCH 1'):(draft.accelerateSingleTask?'GPU · BF16 · SDPA · AUTO BATCH':'GPU · BF16 · SDPA · ADAPTIVE BATCH 1–2')}</span><button className="reset-settings" type="button" onClick={resetPreferences}><RotateCcw size={14}/>恢复默认配置</button></div></div><div className="tabs"><button className={draft.mode==='preset'?'active':''} onClick={()=>updatePreference('mode','preset')}>预置音色</button><button className={draft.mode==='inline_clone'?'active':''} onClick={()=>updatePreference('mode','inline_clone')}>声音克隆</button></div><label className="text-editor">合成文本<textarea value={draft.text} maxLength={50000} onChange={event=>updateContent('text',event.target.value)}/><small>{draft.text.length} / 50,000</small></label>{draft.mode==='inline_clone'?<div className="clone-source"><button className={draft.cloneSource==='upload'?'active':''} onClick={()=>updatePreference('cloneSource','upload')}><Upload size={15}/>一次性参考</button><button className={draft.cloneSource==='voiceprint'?'active':''} onClick={()=>updatePreference('cloneSource','voiceprint')}><Fingerprint size={15}/>声纹库</button></div>:null}<div className="two-cols"><label>输出语种<select value={draft.language} onChange={event=>updatePreference('language',event.target.value)}>{ttsLanguages.map(language=><option key={language}>{language}</option>)}</select><small className="device-hint">已知语种时显式选择；未知或混合语种使用 Auto。</small></label>{draft.mode==='preset'?<label>音色<select value={draft.speaker} onChange={event=>updatePreference('speaker',event.target.value)}>{voices.map(voice=><option key={voice}>{voice}</option>)}</select></label>:draft.cloneSource==='voiceprint'?<label>声纹人员<select value={person?.id||''} disabled={!voiceprints.length} onChange={event=>setPreferences(current=>{const next={...current,personId:event.target.value,sampleId:''};saveTtsPreferences(next);return next})}>{voiceprints.length?voiceprints.map(item=><option key={item.id} value={item.id}>{item.name}</option>):<option value="">声纹库为空</option>}</select></label>:null}</div>{draft.mode==='inline_clone'&&draft.cloneSource==='upload'?<section className="clone-reference-panel" aria-label="一次性克隆参考"><div className="clone-reference-head"><div><b>克隆参考自动识别</b><small>选择单人音频后由 ASR 自动识别语种、文本和时间戳。</small></div>{draft.refJobId?<button className="icon-button danger" aria-label="清除克隆参考" onClick={()=>void clearReference()}><Trash2/></button>:null}</div><div className="sample-source-tabs" role="tablist" aria-label="克隆参考来源"><button role="tab" aria-selected={referenceSource==='upload'} className={referenceSource==='upload'?'active':''} disabled={referenceBusy||microphoneActive} onClick={()=>setReferenceSource('upload')}><Upload size={15}/>上传文件</button><button role="tab" aria-selected={referenceSource==='record'} className={referenceSource==='record'?'active':''} disabled={referenceBusy||microphoneActive} onClick={()=>setReferenceSource('record')}><Mic2 size={15}/>麦克风录音</button></div>{referenceSource==='upload'?<div className="sample-input-panel" role="tabpanel"><button className="select-like upload" disabled={referenceBusy} onClick={()=>fileRef.current?.click()}><Upload size={16}/>{referenceBusy?'正在提交分析…':referenceName||referenceJob?.display_name.replace('TTS 克隆参考分析 · ','')||'选择后自动分析参考音频'}</button><input hidden ref={fileRef} type="file" accept="audio/*" onChange={event=>chooseReference(event.target.files?.[0])}/><p>建议 5–15 秒、环境安静且只有一位说话人；较长音频会按字词边界截断。</p></div>:<div className="sample-input-panel recorder-panel" role="tabpanel">{!recorder.supported?<div className="recorder-unavailable" role="note"><Mic2/><p>{recorder.unavailableReason}</p></div>:recorder.phase==='recording'?<div className="recording-live" role="status"><span className="recording-dot" aria-hidden="true"/><div><b>正在录音</b><strong>{formatTime(elapsed,false)} / 00:00:30</strong></div><progress max={recorder.maxSeconds} value={elapsed} aria-label="TTS 克隆参考录音进度"/><button className="record-stop" onClick={recorder.stop}><CircleStop size={17}/>停止并试听</button></div>:recorder.phase==='requesting'?<div className="recorder-requesting" role="status"><Mic2/><p>正在请求麦克风权限…</p></div>:recorder.recorded?<div className="recording-preview"><div><b>参考录音完成</b><span>{formatTime(recorder.recorded.durationSeconds,false)}</span></div><audio controls preload="metadata" src={recorder.recorded.url}/><button className="primary" disabled={referenceBusy} onClick={()=>void analyzeRecording()}><Sparkles size={15}/>{referenceBusy?'正在提交…':'使用并自动分析'}</button><button className="button secondary" onClick={()=>void recorder.start()}><RefreshCw size={15}/>重新录制</button></div>:<div className="recorder-ready"><Mic2/><div><b>直接录制克隆参考</b><p>停止后可先试听，再确认提交自动识别。</p></div><button className="record-start" disabled={referenceBusy} onClick={()=>void recorder.start()}><span aria-hidden="true"/>开始录音</button>{recorder.error?<p className="recording-error" role="alert">{recorder.error}</p>:null}</div>}</div>}{referenceJob?<div className={`clone-analysis ${referenceJob.state}`}><div className="clone-analysis-status"><b>{referenceJob.state==='queued'?'等待 ASR 分析':referenceJob.state==='running'?'正在 ASR 分析':referenceJob.state==='succeeded'?'参考识别完成':referenceJob.state==='failed'?'参考识别失败':'参考分析已取消'}</b><span>{Math.round(referenceJob.progress*100)}% · {referenceJob.stage}</span></div>{referenceJob.state==='running'||referenceJob.state==='queued'?<progress max={1} value={referenceJob.progress} aria-label="克隆参考分析进度"/>:null}{referenceJob.state==='failed'||referenceJob.state==='cancelled'?<><p className="error">{referenceJob.error_message||'参考音频未能完成识别。'}</p><button className="button secondary" disabled={referenceBusy} onClick={()=>void retryReference()}><RefreshCw size={15}/>重试分析</button></>:null}{referenceReady?<div className="clone-analysis-result"><label>参考音频语种<select value={draft.refLanguage} onChange={event=>updateContent('refLanguage',event.target.value)}>{availableReferenceLanguages.map(language=><option key={language}>{language}</option>)}</select></label><label>自动识别文本（可修正）<textarea className="short" value={draft.refText} onChange={event=>updateContent('refText',event.target.value)} placeholder="请核对文本与参考音频逐字一致。"/></label>{referenceJob.result?.artifacts?.some(item=>item.name==='reference.wav')?<audio controls preload="metadata" src={artifactUrl(referenceJob.id,'reference.wav')}/>:null}<small>修正后将使用当前文本；超过 15 秒时会按修正内容重新精确对齐。</small></div>:null}</div>:draft.refJobId?<p className="error">参考分析任务已不存在，请重新上传或录音。</p>:null}</section>:null}{draft.mode==='inline_clone'&&draft.cloneSource==='voiceprint'?<label>声纹样本<select aria-label="TTS 声纹样本" value={sample?.id||''} disabled={!eligibleSamples.length} onChange={event=>updatePreference('sampleId',event.target.value)}>{eligibleSamples.length?eligibleSamples.map((item,index)=><option key={item.id} value={item.id}>样本 {eligibleSamples.length-index} · {formatTime(item.duration||0)}{item.duration&&item.duration>15?' · 自动截断':''}</option>):<option value="">没有可用于 TTS 的样本</option>}</select>{sample?<small className="sample-summary">{sample.language} · {sample.transcript}{sample.duration&&sample.duration>15?' · 克隆前将按字词边界精确截断至 15 秒以内。':''}</small>:null}</label>:null}<label className="device-control">计算设备<select aria-label="TTS 计算设备" value={draft.computeDevice} onChange={event=>updatePreference('computeDevice',event.target.value as ComputeDevice)}><option value="gpu" disabled={gpuAvailable===false}>GPU · BF16{gpuAvailable===false?'（不可用）':'（默认）'}</option><option value="cpu">CPU · FP32</option></select><small className="device-hint">参考 ASR 与 TTS 使用当前设备；GPU 不可用时不会静默回退。</small></label><div className="acceleration-control"><label><input type="checkbox" checked={draft.accelerateSingleTask} onChange={event=>updatePreference('accelerateSingleTask',event.target.checked)}/><span>单任务加速</span></label><InfoTooltip id="tts-acceleration-help" text="按 CPU 核心与可用内存或 GPU 显存自动提高任务内部批次。长文本收益更明显；不改变模型、精度或解码方式，内存不足时会自动回退。"/></div>{error?<p className="error" role="alert">{error}</p>:null}{notice?<p className="notice" role="status">{notice}</p>:null}<button className="primary synth" disabled={busy||referenceBusy||!draft.text.trim()||draft.computeDevice==='gpu'&&gpuAvailable===false||draft.mode==='inline_clone'&&draft.cloneSource==='upload'&&!referenceReady} onClick={()=>void submit()}><Sparkles size={18}/>{busy?'正在提交…':'生成语音'}</button><div className="quality-note"><b>本地质量策略</b><span>官方 0.6B 权重 · 无量化 · {draft.computeDevice==='cpu'?'CPU FP32；兼容性优先。':'GPU BF16；官方原生精度加速。'}</span></div></section><aside ref={preview} className="tts-preview" data-module="RENDER_QUEUE / Q_02"><h2>当前合成结果</h2>{selected?.result?<div className="audio-card"><div><b>{selected.display_name}</b><span>{selected.result.speaker||'克隆音色'} · {selected.result.duration}s · {(selected.result.compute_device||selected.request.compute_device||'gpu').toString().toUpperCase()} {selected.result.precision||''}</span></div>{selected.result.artifacts?.[0]?<AudioTransport src={artifactUrl(selected.id,selected.result.artifacts[0].name)} peaks={selected.result.waveform} duration={selected.result.duration}/>:null}{selected.result.artifacts?.[0]?<a className="button primary" href={artifactUrl(selected.id,selected.result.artifacts[0].name)}><Download size={16}/>下载 {selected.result.format?.toUpperCase()||'WAV'}</a>:null}</div>:<div className="empty small"><Sparkles/><p>合成完成后可在这里试听和下载</p></div>}<h2>任务列表</h2>{visibleJobs.map(job=><JobMini key={job.id} job={job} isSelected={job.id===selected?.id} onOpen={item=>item.state==='succeeded'&&onSelect(item)}/>)}</aside></div>
}
