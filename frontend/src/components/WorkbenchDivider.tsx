import {useLayoutEffect,useRef,useState,type KeyboardEvent,type PointerEvent} from 'react'
import {RotateCcw} from 'lucide-react'
import {useTranslation} from 'react-i18next'
import './workbench.css'

type Workbench='asr'|'tts'
const storageKey='audio-intel:workbench-layout:v1'
const minimumInput=320
const minimumSettings=400

function savedLayouts():Partial<Record<Workbench,number>>{
 try{
  const value=JSON.parse(localStorage.getItem(storageKey)||'{}')
  return Object.fromEntries((['asr','tts'] as const).flatMap(key=>typeof value?.[key]==='number'&&Number.isFinite(value[key])&&value[key]>0&&value[key]<1?[[key,value[key]]]:[]))
 }catch{return {}}
}

// Only this small component renders while dragging; the two workbench panes stay mounted.
export function WorkbenchDivider({workbench}:{workbench:Workbench}){
 const {t}=useTranslation()
 const handle=useRef<HTMLDivElement>(null)
 const [initial]=useState(()=>savedLayouts()[workbench]??.5)
 const preferred=useRef(initial)
 const geometry=useRef({width:0,ratio:.5,min:0,max:1})
 const drag=useRef<{id:number;x:number;ratio:number;previous:number}|undefined>(undefined)
 const frame=useRef(0)
 const [position,setPosition]=useState({ratio:.5,min:0,max:1})
 const [dragging,setDragging]=useState(false)

 const paint=()=>{
  const container=handle.current?.parentElement?.parentElement
  if(!container||!matchMedia('(min-width:1024px)').matches||container.clientWidth===0)return
  const gap=parseFloat(getComputedStyle(container).columnGap)||0
  const width=container.clientWidth-(handle.current?.parentElement?.getBoundingClientRect().width||16)-gap*2
  if(width<minimumInput+minimumSettings)return
  const min=minimumInput/width,max=1-minimumSettings/width
  const ratio=Math.max(min,Math.min(max,preferred.current))
  geometry.current={width,ratio,min,max}
  container.style.setProperty('--workbench-first',`${width*ratio}px`)
  setPosition(current=>current.ratio===ratio&&current.min===min&&current.max===max?current:{ratio,min,max})
 }
 const schedule=()=>{if(!frame.current)frame.current=requestAnimationFrame(()=>{frame.current=0;paint()})}
 const save=()=>{try{localStorage.setItem(storageKey,JSON.stringify({...savedLayouts(),[workbench]:preferred.current}))}catch{/* Keep the layout usable when storage is unavailable. */}}
 const reset=()=>{preferred.current=.5;paint();save()}
 useLayoutEffect(()=>{
  const container=handle.current?.parentElement?.parentElement
  if(!container)return
  const observer=new ResizeObserver(schedule)
  observer.observe(container)
  paint()
  return()=>{observer.disconnect();cancelAnimationFrame(frame.current);frame.current=0}
 },[])
 const finish=(event:PointerEvent<HTMLDivElement>,cancelled=false)=>{
  if(drag.current?.id!==event.pointerId)return
  if(cancelled)preferred.current=drag.current.previous
  drag.current=undefined
  cancelAnimationFrame(frame.current);frame.current=0;paint()
  if(!cancelled)save()
  setDragging(false)
  if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId)
 }
 const keyboard=(event:KeyboardEvent<HTMLDivElement>)=>{
  const {ratio,min,max}=geometry.current
  const step=event.shiftKey?.1:.02
  const next=event.key==='ArrowLeft'?ratio-step:event.key==='ArrowRight'?ratio+step:event.key==='Home'?min:event.key==='End'?max:undefined
  if(next===undefined)return
  event.preventDefault();preferred.current=Math.max(min,Math.min(max,next));paint();save()
 }
 return <div className={`workbench-divider${dragging?' dragging':''}`}>
  <div ref={handle} className="workbench-resize-handle" role="separator" tabIndex={0} aria-orientation="vertical" aria-label={t('workspaceSplit.resize')} aria-controls={`${workbench}-input-pane`} aria-valuemin={Math.round(position.min*100)} aria-valuemax={Math.round(position.max*100)} aria-valuenow={Math.round(position.ratio*100)} aria-valuetext={t('workspaceSplit.ratio',{left:Math.round(position.ratio*100),right:Math.round((1-position.ratio)*100)})} title={t('workspaceSplit.help')}
   onDoubleClick={reset} onKeyDown={keyboard}
   onPointerDown={event=>{if(event.button!==0||!geometry.current.width)return;event.preventDefault();event.currentTarget.focus();drag.current={id:event.pointerId,x:event.clientX,ratio:geometry.current.ratio,previous:preferred.current};event.currentTarget.setPointerCapture(event.pointerId);setDragging(true)}}
   onPointerMove={event=>{if(drag.current?.id!==event.pointerId)return;const {width,min,max}=geometry.current;preferred.current=Math.max(min,Math.min(max,drag.current.ratio+(event.clientX-drag.current.x)/width));schedule()}}
   onPointerUp={event=>finish(event)} onPointerCancel={event=>finish(event,true)} onLostPointerCapture={event=>finish(event,true)}><span aria-hidden="true"/></div>
  <button type="button" className="workbench-reset-layout" title={t('workspaceSplit.reset')} aria-label={t('workspaceSplit.reset')} onClick={reset}><RotateCcw size={14}/></button>
 </div>
}
