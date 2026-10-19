import {useEffect,useLayoutEffect,useRef,useState,type PointerEvent} from 'react'
import {createPortal} from 'react-dom'
import {LocateFixed,Maximize,Minus,Plus} from 'lucide-react'
import {useTranslation} from 'react-i18next'
import {readingIndex} from '../lib/readerNavigation'
import {InfoTooltip} from './InfoTooltip'
import {formatTime,HttpError,request} from '../lib/api'
import type {JobWaveform,VideoScene} from '../lib/types'
import type {VideoScenesResource} from '../hooks/useVideoScenes'

type WindowRange={start:number;span:number}
const clamp=(value:number,lower:number,upper:number)=>Math.max(lower,Math.min(upper,value))
export function VideoTimeline({jobId,duration,currentTime,enhanced,available,scenes,onSeek,onSceneSeek,onSeekCommit}:{jobId:string;duration:number;currentTime:number;enhanced:boolean;available:boolean;scenes:VideoScenesResource;onSeek:(time:number)=>void;onSceneSeek:(scene:VideoScene)=>void;onSeekCommit:(time:number)=>void}){
 const {t}=useTranslation()
 const [range,setRange]=useState<WindowRange>({start:0,span:duration})
 const [follow,setFollow]=useState(true)
 const [preview,setPreview]=useState<{scene:VideoScene;left:number;top:number;below:boolean}>()
 const [previewError,setPreviewError]=useState(false)
 const previewAnchor=useRef<HTMLElement|null>(null)
 const [resource,setResource]=useState<{key:string;data?:JobWaveform;error?:string}>({key:''})
 const [reload,setReload]=useState(0)
 const [blockedUntil,setBlockedUntil]=useState(0)
 const [retryWaiting,setRetryWaiting]=useState(false)
 const canvas=useRef<HTMLCanvasElement>(null)
 const track=useRef<HTMLDivElement>(null)
 const scroller=useRef<HTMLDivElement>(null)
 const programmaticScroll=useRef(0)
 const scenePointer=useRef({x:0,scroll:0})
 const touchPanning=useRef(false)
 const dragged=useRef(false)
 const [width,setWidth]=useState(800)
 const trackName=enhanced?'enhanced':'original'
 const start=clamp(range.start,0,Math.max(0,duration-range.span))
 const span=Math.min(duration,range.span)||duration
 const end=Math.min(duration,start+span)
 const points=Math.min(2048,Math.max(64,Math.round(width)))
 const key=`${jobId}:${trackName}:${start}:${end}:${points}`
 const data=resource.key===key?resource.data:undefined
 const error=resource.key===key?resource.error:undefined
 useEffect(()=>{setRange({start:0,span:duration});setFollow(true);setPreview(undefined)},[jobId,duration])
 useEffect(()=>{
  if(!retryWaiting)return
  const timer=setInterval(()=>{if(Date.now()>=blockedUntil)setRetryWaiting(false)},250)
  return()=>clearInterval(timer)
 },[retryWaiting,blockedUntil])
 useLayoutEffect(()=>{const node=track.current;if(!node)return;const observer=new ResizeObserver(entries=>setWidth(Math.max(1,entries[0].contentRect.width)));observer.observe(node);return()=>observer.disconnect()},[])
 useEffect(()=>{
  if(!duration||!available)return
  const controller=new AbortController()
  const timer=setTimeout(()=>{
   setResource({key})
   void request<JobWaveform>(`/api/v1/jobs/${jobId}/waveform?track=${trackName}&start=${start}&end=${end}&points=${points}`,{signal:controller.signal}).then(value=>{if(!controller.signal.aborted)setResource({key,data:value})},cause=>{if(!controller.signal.aborted){setResource({key,error:cause.message});if(cause instanceof HttpError&&cause.retryAfter){setBlockedUntil(Date.now()+cause.retryAfter*1000);setRetryWaiting(true)}}})
  },150)
  return()=>{clearTimeout(timer);controller.abort()}
 },[key,jobId,trackName,start,end,points,duration,available,reload])
 useEffect(()=>{
  const node=track.current
  if(!node)return
  const wheel=(event:WheelEvent)=>{
   const delta=event.deltaX||(event.shiftKey?event.deltaY:0)
   const viewport=scroller.current
   if(!delta||!viewport||viewport.scrollWidth<=viewport.clientWidth)return
   event.preventDefault()
   viewport.scrollLeft+=delta*(event.deltaMode===1?16:event.deltaMode===2?viewport.clientWidth:1)
  }
  node.addEventListener('wheel',wheel,{passive:false})
  return()=>node.removeEventListener('wheel',wheel)
 },[])
 useEffect(()=>{
  if(!follow||!duration||span>=duration||currentTime>=start&&currentTime<=end)return
  setRange({span,start:clamp(currentTime-span*.2,0,duration-span)})
 },[currentTime,follow,duration,span,start,end])
 useEffect(()=>{
  const node=canvas.current;if(!node)return
  const ratio=devicePixelRatio||1,rect=node.getBoundingClientRect()
  node.width=Math.round(rect.width*ratio);node.height=Math.round(rect.height*ratio)
  const ctx=node.getContext('2d');if(!ctx)return
  ctx.scale(ratio,ratio);ctx.clearRect(0,0,rect.width,rect.height)
  if(!data)return
  const offset=(data.start-start)/span*rect.width
  const step=(data.end-data.start)/span*rect.width/data.waveform.length
  data.waveform.forEach((value,index)=>{const h=Math.max(1,value*rect.height*.85);ctx.fillStyle='#00aab2';ctx.fillRect(offset+index*step,(rect.height-h)/2,Math.max(1,step*.65),h)})
 },[data,width,start,span])
 useEffect(()=>{
  if(!preview)return
  const escape=(event:KeyboardEvent)=>{if(event.key==='Escape')setPreview(undefined)}
  const scroll=()=>{
   const anchor=previewAnchor.current
   if(anchor&&document.activeElement===anchor){
    const box=anchor.getBoundingClientRect()
    if(box.bottom>0&&box.top<innerHeight){
     const position=previewPosition(anchor)
     setPreview(current=>current&&current.left===position.left&&current.top===position.top&&current.below===position.below?current:current?{...current,...position}:undefined)
     return
    }
   }
   setPreview(undefined)
  }
  window.addEventListener('keydown',escape);window.addEventListener('scroll',scroll,true)
  return()=>{window.removeEventListener('keydown',escape);window.removeEventListener('scroll',scroll,true)}
 },[preview])
 function previewPosition(node:HTMLElement){
  const rect=node.getBoundingClientRect(),below=rect.top<220
  return {left:clamp(rect.left+rect.width/2,Math.min(152,innerWidth/2),Math.max(innerWidth/2,innerWidth-152)),top:below?rect.bottom+6:rect.top-6,below}
 }
 function showPreview(scene:VideoScene,node:HTMLElement){
  if(node.classList.contains('timeline-scene')&&document.activeElement?.classList.contains('timeline-scenes'))return
  previewAnchor.current=node
  setPreviewError(false);setPreview({scene,...previewPosition(node)})
 }
 function sceneAt(time:number){const items=scenes.data?.scenes||[];return items[readingIndex(items,time)]}
 function selectScene(scene:VideoScene){setFollow(true);onSceneSeek(scene)}
 function chooseScene(scene:VideoScene,node:HTMLElement){showPreview(scene,node);selectScene(scene)}
 function zoom(factor:number){const next=clamp(span*factor,Math.min(10,duration),duration);const center=currentTime>=start&&currentTime<=end?currentTime:start+span/2;setRange({start:clamp(center-next/2,0,duration-next),span:next})}
 function seekPointer(event:PointerEvent<HTMLCanvasElement>,commit=false){const rect=event.currentTarget.getBoundingClientRect();const seconds=start+clamp((event.clientX-rect.left)/rect.width,0,1)*span;setFollow(true);onSeek(seconds);if(commit)onSeekCommit(seconds)}
 const scrollWidth=Math.min(1_000_000,Math.max(width,duration/span*width))
 useLayoutEffect(()=>{
  const node=scroller.current
  if(!node)return
  const target=duration>span?start/(duration-span)*(node.scrollWidth-node.clientWidth):0
  programmaticScroll.current=target
  if(Math.abs(node.scrollLeft-target)>.5)node.scrollLeft=target
 },[start,span,width,duration,scrollWidth])
 function scrollWindow(){
  const node=scroller.current
  if(!node||Math.abs(node.scrollLeft-programmaticScroll.current)<1)return
  const extent=node.scrollWidth-node.clientWidth
  if(extent<=0)return
  programmaticScroll.current=node.scrollLeft
  setFollow(false)
  setRange({span,start:clamp(node.scrollLeft/extent*(duration-span),0,duration-span)})
 }
 const visibleScenes=(scenes.data?.scenes||[]).filter(scene=>scene.end>start&&scene.start<end)
 // Dense full views retain every scene marker, while only loading images with usable width.
 let nextTile=0
 const tiles=visibleScenes.filter(scene=>{const left=(Math.max(start,scene.start)-start)/span*width;if(left<nextTile)return false;nextTile=left+64;return true})
 const playhead=currentTime>=start&&currentTime<=end?100*(currentTime-start)/span:undefined
 return <section className="video-timeline" aria-label={t('timeline.label')}>
  <div className="timeline-toolbar"><span>{formatTime(start,false)}–{formatTime(end,false)}</span><InfoTooltip id={`timeline-help-${jobId}`} label={t('timeline.helpLabel')} text={t('timeline.help')}/><select className={`timeline-scene-picker${visibleScenes.length>width/44?' dense':''}`} aria-label={t('timeline.chooseScene')} value="" onChange={event=>{const scene=scenes.data?.scenes.find(scene=>scene.id===event.target.value);if(scene)selectScene(scene)}}><option value="">{t('timeline.chooseScene')}</option>{scenes.data?.scenes.map((scene,index)=><option key={scene.id} value={scene.id}>{t('video.scene',{number:index+1})} · {formatTime(scene.start,false)}–{formatTime(scene.end,false)}</option>)}</select><div role="group" aria-label={t('timeline.zoom')}><button type="button" className="icon-button" aria-label={t('timeline.zoomOut')} disabled={span>=duration} onClick={()=>zoom(2)}><Minus size={16}/></button><button type="button" className="icon-button" aria-label={t('timeline.zoomIn')} disabled={span<=Math.min(10,duration)} onClick={()=>zoom(.5)}><Plus size={16}/></button><button type="button" className="icon-button" aria-label={t('timeline.fit')} onClick={()=>{setRange({start:0,span:duration});setFollow(true)}}><Maximize size={16}/></button><button className="icon-button" type="button" aria-label={t('timeline.follow')} title={t('timeline.followHelp')} disabled={playhead!==undefined} onClick={()=>{setFollow(true);setRange({start:clamp(currentTime-span*.2,0,duration-span),span})}}><LocateFixed size={16}/></button></div></div>
  <div className="timeline-tracks" ref={track}>
   <div className="timeline-scenes" role="slider" tabIndex={0} aria-label={t('timeline.scenes')} aria-valuemin={0} aria-valuemax={duration} aria-valuenow={currentTime} aria-valuetext={formatTime(currentTime,false)} onFocus={event=>{const scene=sceneAt(currentTime);if(scene)showPreview(scene,event.currentTarget)}} onBlur={()=>setPreview(undefined)} onPointerDown={event=>{scenePointer.current={x:event.clientX,scroll:scroller.current?.scrollLeft||0};touchPanning.current=event.pointerType==='touch';if(touchPanning.current)event.currentTarget.setPointerCapture(event.pointerId)}} onPointerMove={event=>{if(touchPanning.current&&scroller.current)scroller.current.scrollLeft=scenePointer.current.scroll+scenePointer.current.x-event.clientX}} onPointerUp={()=>{touchPanning.current=false}} onPointerCancel={()=>{touchPanning.current=false}} onClick={event=>{if(Math.abs(event.clientX-scenePointer.current.x)>6||Math.abs((scroller.current?.scrollLeft||0)-scenePointer.current.scroll)>2)return;const rect=event.currentTarget.getBoundingClientRect();const scene=sceneAt(start+clamp((event.clientX-rect.left)/rect.width,0,1)*span);if(scene)chooseScene(scene,event.currentTarget)}} onKeyDown={event=>{const items=scenes.data?.scenes;if(!items?.length)return;const index=items.findIndex(scene=>scene===sceneAt(currentTime));const next=event.key==='ArrowRight'?index+1:event.key==='ArrowLeft'?index-1:event.key==='Home'?0:event.key==='End'?items.length-1:event.key==='Enter'?index:undefined;if(next!==undefined){event.preventDefault();chooseScene(items[clamp(next,0,items.length-1)],event.currentTarget)}}} onMouseLeave={event=>{if(document.activeElement!==event.currentTarget)setPreview(undefined)}}>
    {!scenes.loading&&!scenes.error?<div className="timeline-tiles" aria-hidden="true">{tiles.map(scene=><img key={scene.id} loading="lazy" src={`/api/v1/jobs/${jobId}/video-frames/${encodeURIComponent(scene.image)}`} alt="" style={{left:`${(Math.max(start,scene.start)-start)/span*100}%`,width:`${Math.max(64/width*100,(Math.min(end,scene.end)-Math.max(start,scene.start))/span*100)}%`}} onError={event=>{event.currentTarget.hidden=true}}/>)}</div>:null}
    {scenes.loading?<span className="timeline-status" role="status">{t('video.loading')}</span>:scenes.error?<span className="timeline-status" role="alert">{t('timeline.scenesFailed')} <button className="button" onClick={scenes.reload}>{t('common.actions.reload')}</button></span>:visibleScenes.map(scene=>{
     const left=(Math.max(start,scene.start)-start)/span*100,percent=(Math.min(end,scene.end)-Math.max(start,scene.start))/span*100
     const label=`${t('video.scene',{number:(scenes.data?.scenes.indexOf(scene)||0)+1})} · ${formatTime(scene.start,false)}–${formatTime(scene.end,false)}`

     return <span key={scene.id} className={`timeline-scene${percent*width/100>=48?'':' timeline-marker'}`} style={{left:`${left}%`,width:`${percent}%`}} aria-hidden="true" title={label} onMouseEnter={event=>showPreview(scene,event.currentTarget)}><span>{formatTime(scene.start,false)}</span></span>
    })}
   </div>
   <canvas ref={canvas} className="timeline-waveform" data-window-start={data?.start} data-window-end={data?.end} data-track={data?.track} aria-busy={!data&&available&&!error} role="slider" tabIndex={0} aria-label={t('audio.position')} aria-valuemin={0} aria-valuemax={duration} aria-valuenow={currentTime} aria-valuetext={`${formatTime(currentTime,false)} / ${formatTime(duration,false)}`} onPointerDown={event=>{dragged.current=true;event.currentTarget.setPointerCapture(event.pointerId);seekPointer(event)}} onPointerMove={event=>{if(dragged.current)seekPointer(event)}} onPointerUp={event=>{if(dragged.current)seekPointer(event,true);dragged.current=false}} onPointerCancel={()=>{dragged.current=false}} onKeyDown={event=>{let next:number|undefined;if(['ArrowRight','ArrowUp'].includes(event.key))next=currentTime+5;else if(['ArrowLeft','ArrowDown'].includes(event.key))next=currentTime-5;else if(event.key==='Home')next=0;else if(event.key==='End')next=duration;else if(event.key==='PageUp')next=currentTime+span*.1;else if(event.key==='PageDown')next=currentTime-span*.1;if(next!==undefined){event.preventDefault();setFollow(true);onSeek(clamp(next,0,duration));onSeekCommit(clamp(next,0,duration))}}}/>
   {playhead!==undefined?<div className="timeline-playhead" style={{left:`${playhead}%`}} aria-hidden="true"/>:null}
  </div>
  <div className="timeline-scroll-viewport" ref={scroller} tabIndex={0} role="region" aria-label={t('timeline.window')} title={t('timeline.window')} onScroll={scrollWindow}><div className="timeline-scroll-content" style={{width:scrollWidth,height:1}}/></div>
  {!data?<div className="timeline-load-state" role={error?'alert':'status'}>{!available?t('asr.enhancement.originalUnavailable'):error?t('timeline.waveFailed'):t('audio.waveformLoading')}{error&&available?<button className="button" disabled={retryWaiting} onClick={()=>setReload(value=>value+1)}>{t('common.actions.retry')}</button>:null}</div>:null}

  {preview?createPortal(<div className="timeline-preview" role="tooltip" style={{left:preview.left,top:preview.top,transform:`translate(-50%,${preview.below?'0':'-100%'})`}}>{previewError?<span>{t('video.frameFailed')}</span>:<img src={`/api/v1/jobs/${jobId}/video-frames/${encodeURIComponent(preview.scene.image)}`} alt={t('video.frameAt',{time:formatTime(preview.scene.frame_time,false)})} onError={()=>setPreviewError(true)}/>}<span>{t('timeline.sceneRange')} {formatTime(preview.scene.start,false)}–{formatTime(preview.scene.end,false)} · {t('timeline.frameTime')} {formatTime(preview.scene.frame_time,false)}</span></div>,document.body):null}
 </section>
}
