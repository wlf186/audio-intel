import {useEffect,useRef,useState} from 'react'
import {HttpError} from '../lib/api'
import {readWaveform,type ArtifactWaveform} from '../lib/waveforms'

type State={url:string;data?:ArtifactWaveform;error?:string;retryAt?:number}
export function useAudioWaveform(src:string,hasPeaks:boolean,active:boolean){
 const container=useRef<HTMLDivElement>(null)
 const [visible,setVisible]=useState(false)
 const [attempt,setAttempt]=useState(0)
 const [state,setState]=useState<State>({url:''})
 const url=`${src}/waveform`
 useEffect(()=>{
  const element=container.current
  if(!element)return
  const observer=new IntersectionObserver(entries=>setVisible(entries[0].isIntersecting))
  observer.observe(element)
  return()=>observer.disconnect()
 },[])
 useEffect(()=>{
  if(hasPeaks||!active||!visible)return
  const controller=new AbortController()
  setState({url})
  void readWaveform(url,controller.signal).then(data=>{
   if(!controller.signal.aborted)setState({url,data})
  }).catch((error:unknown)=>{
   if(!controller.signal.aborted)setState({url,error:(error as Error).message,retryAt:error instanceof HttpError&&error.retryAfter?Date.now()+error.retryAfter*1000:undefined})
  })
  return()=>controller.abort()
 },[url,hasPeaks,active,visible,attempt])
 useEffect(()=>{
  if(!state.retryAt)return
  const timer=setTimeout(()=>setState(current=>({...current,retryAt:undefined})),Math.max(0,state.retryAt-Date.now()))
  return()=>clearTimeout(timer)
 },[state.retryAt])
 const current=state.url===url?state:undefined
 return {container,data:current?.data,error:current?.error,retryDisabled:Boolean(current?.retryAt),retry:()=>setAttempt(value=>value+1)}
}
