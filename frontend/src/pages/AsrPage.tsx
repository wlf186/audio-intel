import {memo,useCallback,useEffect,useMemo,useRef,useState} from 'react'
import {Download,FileAudio,Pause,Play,Search,UploadCloud} from 'lucide-react'
import {api,artifactUrl,formatTime,sourceUrl} from '../lib/api'
import type {ComputeDevice,Job,Segment} from '../lib/types'
import {Waveform} from '../components/Waveform'
import {JobMini} from '../components/JobMini'

const deviceKey='audio-intel:asr-device-v1'
function loadDevice():ComputeDevice{return sessionStorage.getItem(deviceKey)==='cpu'?'cpu':'gpu'}

const SegmentRow=memo(function SegmentRow({segment,active,currentTime,onPlay,onSeekWord}:{segment:Segment;active:boolean;currentTime:number;onPlay:(segment:Segment)=>void;onSeekWord:(start:number)=>void}){
 const [expanded,setExpanded]=useState(false)
 return <article className={active?'active':''}><div className="segment-time"><b>{formatTime(segment.start)}</b><span>—</span><b>{formatTime(segment.end)}</b></div><div className={`speaker s${Number(segment.speaker.split('_')[1])%4}`}><i/><b>{segment.speaker_label}</b></div><div className="segment-copy">{expanded&&segment.words?.length?<div className="words">{segment.words.map((word,index)=><button className={active&&currentTime>=word.start&&currentTime<word.end?'active':''} key={index} onClick={()=>onSeekWord(word.start)}>{word.text}<small>{formatTime(word.start)}</small></button>)}</div>:<p>{segment.text}</p>}{segment.words?.length?<button className="word-toggle" onClick={()=>setExpanded(value=>!value)}>{expanded?'收起字词时间戳':`查看 ${segment.words.length} 个字词时间戳`}</button>:null}</div><button className="icon-button" aria-label={`播放片段 ${segment.id+1}`} onClick={()=>onPlay(segment)}><Play size={17}/></button></article>
})

export function AsrPage({jobs,onJobSubmitted,selectedJobId,onSelect,gpuAvailable}:{jobs:Job[];onJobSubmitted:(job:Job)=>void;selectedJobId?:string;onSelect:(j:Job)=>void;gpuAvailable?:boolean}){
 const [file,setFile]=useState<File>()
 const [language,setLanguage]=useState('Auto')
 const [speakers,setSpeakers]=useState('auto')
 const [align,setAlign]=useState(true)
 const [computeDevice,setComputeDevice]=useState<ComputeDevice>(loadDevice)
 const [busy,setBusy]=useState(false)
 const [error,setError]=useState('')
 const [mediaError,setMediaError]=useState('')
 const [query,setQuery]=useState('')
 const [currentTime,setCurrentTime]=useState(0)
 const [playing,setPlaying]=useState(false)
 const input=useRef<HTMLInputElement>(null)
 const audio=useRef<HTMLAudioElement>(null)
 const stopAt=useRef<number|undefined>(undefined)
 const asrJobs=useMemo(()=>jobs.filter(job=>job.kind==='asr'),[jobs])
 const selected=asrJobs.find(job=>job.id===selectedJobId&&job.state==='succeeded')||asrJobs.find(job=>job.state==='succeeded')
 const result=selected?.result
 const duration=result?.duration||0
 const normalizedQuery=query.trim().toLocaleLowerCase()
 const segments=useMemo(()=>{const values=result?.segments||[];return normalizedQuery?values.filter(segment=>segment.text.toLocaleLowerCase().includes(normalizedQuery)):values},[result?.segments,normalizedQuery])

 useEffect(()=>{sessionStorage.setItem(deviceKey,computeDevice)},[computeDevice])

 useEffect(()=>{const player=audio.current;if(player){player.pause();player.currentTime=0;player.load()}stopAt.current=undefined;setCurrentTime(0);setPlaying(false);setMediaError('')},[selected?.id])

 const submit=async()=>{if(!file){input.current?.click();return}if(computeDevice==='gpu'&&gpuAvailable===false){setError('当前未检测到可用 GPU，请选择 CPU。');return}setBusy(true);setError('');try{const data=new FormData();data.set('file',file);data.set('language',language);data.set('speaker_count',speakers);data.set('diarize','true');data.set('align',String(align));data.set('export_formats','json,srt,vtt,txt');data.set('compute_device',computeDevice);const job=await api.submitAsr(data);onJobSubmitted(job);setFile(undefined);if(input.current)input.current.value=''}catch(cause){setError((cause as Error).message)}finally{setBusy(false)}}
 const play=async()=>{const player=audio.current;if(!player)return;setMediaError('');stopAt.current=undefined;try{if(player.paused)await player.play();else player.pause()}catch{setMediaError('当前浏览器无法播放该音频编码，请下载原文件后播放。')}}
 const playSegment=useCallback(async(segment:Segment)=>{const player=audio.current;if(!player)return;setMediaError('');player.currentTime=segment.start;setCurrentTime(segment.start);stopAt.current=segment.end;try{await player.play()}catch{setMediaError('当前浏览器无法播放该音频编码，请下载原文件后播放。')}},[])
 const seekWord=useCallback((start:number)=>{const player=audio.current;if(player){player.currentTime=start;setCurrentTime(start)}},[])
 const seek=(ratio:number)=>{const player=audio.current;if(!player||!duration)return;stopAt.current=undefined;player.currentTime=Math.max(0,Math.min(duration,ratio*duration));setCurrentTime(player.currentTime)}
 const chooseDropped=(event:React.DragEvent<HTMLButtonElement>)=>{event.preventDefault();const next=event.dataTransfer.files[0];if(next)setFile(next)}
 return <div className="workbench hud-page"><aside className="control-panel" data-module="AUDIO_INPUT / UP_01"><h1>音频转写</h1><p className="subtitle">上传音频，获得说话人、逐字时间戳与字幕文件</p><input ref={input} hidden type="file" accept="audio/*,video/*" onChange={event=>setFile(event.target.files?.[0])}/><button className="dropzone" onClick={()=>input.current?.click()} onDragOver={event=>event.preventDefault()} onDrop={chooseDropped}><UploadCloud size={44}/><b>{file?.name||'拖放音频到这里'}</b><span>{file?'点击可重新选择':'支持常见音视频格式'}</span></button><label>识别语言<select value={language} onChange={event=>setLanguage(event.target.value)}><option value="Auto">自动检测</option><option>Chinese</option><option>English</option><option>Cantonese</option><option>Japanese</option><option>Korean</option></select></label><label>说话人数<select value={speakers} onChange={event=>setSpeakers(event.target.value)}><option value="auto">自动</option>{[1,2,3,4,5,6].map(value=><option key={value}>{value}</option>)}</select></label><label>时间戳<select value={align?'word':'segment'} onChange={event=>setAlign(event.target.value==='word')}><option value="word">句级 + 字词级</option><option value="segment">仅句级</option></select></label><label>计算设备<select aria-label="ASR 计算设备" value={computeDevice} onChange={event=>setComputeDevice(event.target.value as ComputeDevice)}><option value="gpu" disabled={gpuAvailable===false}>GPU · BF16{gpuAvailable===false?'（不可用）':'（默认）'}</option><option value="cpu">CPU · FP32</option></select><small className="device-hint">ASR 与时间对齐同步切换；VAD 和说话人分离始终使用 CPU。</small></label><label>导出格式<div className="select-like">JSON · SRT · VTT · TXT</div></label>{error?<p className="error" role="alert">{error}</p>:null}<button className="primary" disabled={busy||computeDevice==='gpu'&&gpuAvailable===false} onClick={submit}><Play size={18}/>{busy?'正在提交…':'开始转写'}</button><section className="aside-jobs"><h2>任务列表</h2>{asrJobs.slice(0,5).map(job=><JobMini key={job.id} job={job} onOpen={item=>item.state==='succeeded'&&onSelect(item)}/>)}</section></aside><section className="result-panel" data-module="TRANSCRIPT_CORE / TRN_01">{selected&&result?<><div className="result-head"><span><FileAudio size={20}/>{selected.display_name}</span><span>{formatTime(duration)} · {(result.compute_device||selected.request.compute_device||'gpu').toString().toUpperCase()} {result.precision||''}</span><div className="artifact-links">{result.artifacts?.map(item=><a key={item.name} className="button" href={artifactUrl(selected.id,item.name)} title={`下载 ${item.name}`}><Download size={15}/>{item.name.split('.').pop()?.toUpperCase()}</a>)}</div></div><audio ref={audio} className="sr-only" preload="metadata" src={selected.source_url||sourceUrl(selected.id)} onPlay={()=>setPlaying(true)} onPause={()=>setPlaying(false)} onEnded={()=>setPlaying(false)} onError={()=>setMediaError('当前浏览器无法播放该音频编码，请下载原文件后播放。')} onTimeUpdate={event=>{const value=event.currentTarget.currentTime;setCurrentTime(value);if(stopAt.current!==undefined&&value>=stopAt.current-.03){event.currentTarget.pause();stopAt.current=undefined}}}/><div className="wave-area"><Waveform peaks={result.waveform} progress={duration?currentTime/duration:0} onSeek={seek}/><div className="time-ruler"><span>00:00</span><span>{formatTime(duration/2,false)}</span><span>{formatTime(duration,false)}</span></div></div><div className="player-row"><button className="round" aria-label={playing?'暂停':'播放'} onClick={play}>{playing?<Pause/>:<Play/>}</button><strong>{formatTime(currentTime)}</strong><span>/ {formatTime(duration)}</span><span className="grow"/><span>{result.language} · {result.timestamp_precision==='word_or_character'?'字词级对齐':'句级时间戳'}</span></div>{mediaError?<p className="media-error" role="alert">{mediaError} <a href={sourceUrl(selected.id,true)}>下载原文件</a></p>:null}<div className="transcript-tools"><Search size={18}/><input value={query} onChange={event=>setQuery(event.target.value)} placeholder="搜索转写内容"/><span>{segments.length} / {result.segments?.length||0} 个片段</span></div><div className="segments">{segments.length?segments.map(segment=>{const active=currentTime>=segment.start&&currentTime<segment.end;return <SegmentRow key={segment.id} segment={segment} active={active} currentTime={active?currentTime:-1} onPlay={playSegment} onSeekWord={seekWord}/>}):<div className="empty search-empty"><Search size={38}/><p>没有匹配的转写片段</p></div>}</div></>:<div className="empty"><FileAudio size={52}/><h2>还没有转写结果</h2><p>从左侧上传音频并启动任务；处理完成后会在这里展示。</p></div>}</section></div>
}
