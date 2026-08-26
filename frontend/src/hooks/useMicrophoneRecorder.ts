import {useCallback,useEffect,useRef,useState} from 'react'

export type RecorderPhase='idle'|'requesting'|'recording'|'preview'|'error'
export type RecordedAudio={file:File;url:string;durationSeconds:number;mimeType:string}

const MIME_TYPES=['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4']

function supportReason(){
 if(typeof window==='undefined')return '当前环境不支持浏览器录音。'
 if(!window.isSecureContext)return '麦克风录音需要通过 localhost、127.0.0.1 或 HTTPS 访问；仍可上传音频文件。'
 if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined')return '当前浏览器不支持麦克风录音；仍可上传音频文件。'
 return ''
}

function preferredMimeType(){
 if(typeof MediaRecorder==='undefined'||typeof MediaRecorder.isTypeSupported!=='function')return ''
 return MIME_TYPES.find(type=>MediaRecorder.isTypeSupported(type))||''
}

function extensionFor(mimeType:string){
 const type=mimeType.toLowerCase()
 if(type.includes('ogg'))return 'ogg'
 if(type.includes('mp4'))return 'm4a'
 if(type.includes('wav'))return 'wav'
 return 'webm'
}

function permissionError(cause:unknown){
 const name=cause instanceof DOMException?cause.name:''
 if(name==='NotAllowedError'||name==='SecurityError')return '麦克风权限被拒绝。请在浏览器站点设置中允许麦克风后重试。'
 if(name==='NotFoundError'||name==='DevicesNotFoundError')return '未检测到可用麦克风，请连接设备后重试。'
 if(name==='NotReadableError'||name==='TrackStartError')return '麦克风暂时无法使用，可能正被其他程序占用。'
 return cause instanceof Error&&cause.message?`无法开始录音：${cause.message}`:'无法开始录音，请检查麦克风后重试。'
}

export function useMicrophoneRecorder(maxSeconds=30){
 const unavailableReason=supportReason()
 const [phase,setPhase]=useState<RecorderPhase>('idle')
 const [elapsedSeconds,setElapsedSeconds]=useState(0)
 const [recorded,setRecorded]=useState<RecordedAudio>()
 const [error,setError]=useState('')
 const recorderRef=useRef<MediaRecorder|undefined>(undefined)
 const streamRef=useRef<MediaStream|undefined>(undefined)
 const chunksRef=useRef<Blob[]>([])
 const startedAtRef=useRef(0)
 const elapsedRef=useRef(0)
 const intervalRef=useRef<number|undefined>(undefined)
 const timeoutRef=useRef<number|undefined>(undefined)
 const previewUrlRef=useRef('')
 const discardOnStopRef=useRef(false)
 const mountedRef=useRef(true)

 const clearTimers=useCallback(()=>{
  if(intervalRef.current!==undefined)window.clearInterval(intervalRef.current)
  if(timeoutRef.current!==undefined)window.clearTimeout(timeoutRef.current)
  intervalRef.current=undefined
  timeoutRef.current=undefined
 },[])

 const releaseStream=useCallback(()=>{
  streamRef.current?.getTracks().forEach(track=>track.stop())
  streamRef.current=undefined
 },[])

 const releasePreview=useCallback(()=>{
  if(previewUrlRef.current)URL.revokeObjectURL(previewUrlRef.current)
  previewUrlRef.current=''
 },[])

 const discard=useCallback(()=>{
  discardOnStopRef.current=true
  clearTimers()
  const recorder=recorderRef.current
  if(recorder&&recorder.state!=='inactive'){
   recorder.ondataavailable=null
   recorder.onstop=null
   recorder.onerror=null
   recorder.stop()
  }
  recorderRef.current=undefined
  chunksRef.current=[]
  releaseStream()
  releasePreview()
  elapsedRef.current=0
  if(mountedRef.current){
   setElapsedSeconds(0)
   setRecorded(undefined)
   setError('')
   setPhase('idle')
  }
 },[clearTimers,releasePreview,releaseStream])

 const stop=useCallback(()=>{
  const recorder=recorderRef.current
  if(!recorder||recorder.state!=='recording')return
  elapsedRef.current=Math.min(maxSeconds,(performance.now()-startedAtRef.current)/1000)
  setElapsedSeconds(elapsedRef.current)
  recorder.stop()
 },[maxSeconds])

 const start=useCallback(async()=>{
  if(unavailableReason){setError(unavailableReason);setPhase('error');return}
  discard()
  discardOnStopRef.current=false
  setError('')
  setPhase('requesting')
  let stream:MediaStream|undefined
  try{
   stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:{ideal:1},echoCancellation:true,noiseSuppression:true},video:false})
   if(!mountedRef.current){stream.getTracks().forEach(track=>track.stop());return}
   const mimeType=preferredMimeType()
   const recorder=new MediaRecorder(stream,mimeType?{mimeType}:undefined)
   streamRef.current=stream
   recorderRef.current=recorder
   chunksRef.current=[]
   recorder.ondataavailable=event=>{if(event.data.size)chunksRef.current.push(event.data)}
   recorder.onerror=()=>{
    discardOnStopRef.current=true
    setError('录音过程中发生错误，请重新录制。')
    setPhase('error')
    clearTimers()
    releaseStream()
   }
   recorder.onstop=()=>{
    clearTimers()
    releaseStream()
    recorderRef.current=undefined
    if(discardOnStopRef.current||!mountedRef.current){chunksRef.current=[];return}
    const actualMime=recorder.mimeType||chunksRef.current[0]?.type||mimeType||'audio/webm'
    const blob=new Blob(chunksRef.current,{type:actualMime})
    chunksRef.current=[]
    if(!blob.size){setError('未捕获到有效音频，请重新录制。');setPhase('error');return}
    releasePreview()
    const url=URL.createObjectURL(blob)
    previewUrlRef.current=url
    const stamp=new Date().toISOString().replace(/\D/g,'').slice(0,14)
    const file=new File([blob],`voiceprint-recording-${stamp}.${extensionFor(actualMime)}`,{type:actualMime,lastModified:Date.now()})
    setRecorded({file,url,durationSeconds:elapsedRef.current,mimeType:actualMime})
    setPhase('preview')
   }
   recorder.start(250)
   startedAtRef.current=performance.now()
   elapsedRef.current=0
   setElapsedSeconds(0)
   setPhase('recording')
   intervalRef.current=window.setInterval(()=>{
    const elapsed=Math.min(maxSeconds,(performance.now()-startedAtRef.current)/1000)
    elapsedRef.current=elapsed
    setElapsedSeconds(elapsed)
   },250)
   timeoutRef.current=window.setTimeout(stop,maxSeconds*1000)
  }catch(cause){
   stream?.getTracks().forEach(track=>track.stop())
   recorderRef.current=undefined
   streamRef.current=undefined
   if(mountedRef.current){setError(permissionError(cause));setPhase('error')}
  }
 },[clearTimers,discard,maxSeconds,releasePreview,releaseStream,stop,unavailableReason])

 useEffect(()=>{
  mountedRef.current=true
  return ()=>{
   mountedRef.current=false
   discardOnStopRef.current=true
   clearTimers()
   const recorder=recorderRef.current
   if(recorder&&recorder.state!=='inactive'){
    recorder.ondataavailable=null
    recorder.onstop=null
    recorder.onerror=null
    recorder.stop()
   }
   releaseStream()
   releasePreview()
  }
 },[clearTimers,releasePreview,releaseStream])

 return {supported:!unavailableReason,unavailableReason,phase,elapsedSeconds,recorded,error,start,stop,discard,maxSeconds}
}
