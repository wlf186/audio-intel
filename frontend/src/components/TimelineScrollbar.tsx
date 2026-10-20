import {useEffect,useLayoutEffect,useRef,useState,type PointerEvent} from 'react'
import {useTranslation} from 'react-i18next'
import {formatTime} from '../lib/api'

const clamp=(value:number,max:number)=>Math.max(0,Math.min(max,value))

/** A persistent window indicator, independent of the OS overlay-scrollbar preference. */
export function TimelineScrollbar({duration,start,span,controls,onChange}:{duration:number;start:number;span:number;controls:string;onChange:(start:number)=>void}){
 const {t}=useTranslation()
 const rail=useRef<HTMLDivElement>(null)
 const drag=useRef<{pointer:number;x:number;start:number}|undefined>(undefined)
 const [width,setWidth]=useState(0)
 const maximum=Math.max(0,duration-span)
 const value=clamp(start,maximum)
 const thumbWidth=Math.min(width,Math.max(44,duration>0?width*span/duration:width))
 const travel=Math.max(0,width-thumbWidth)
 const offset=maximum>0?value/maximum*travel:0
 const disabled=maximum===0||travel===0
 useLayoutEffect(()=>{
  const node=rail.current
  if(!node)return
  const observer=new ResizeObserver(entries=>setWidth(entries[0].contentRect.width))
  observer.observe(node)
  return()=>observer.disconnect()
 },[])
 useEffect(()=>{drag.current=undefined},[duration,span,width])
 function begin(event:PointerEvent<HTMLDivElement>){
  if(disabled||event.button!==0)return
  event.preventDefault()
  event.currentTarget.focus({preventScroll:true})
  const x=event.clientX-event.currentTarget.getBoundingClientRect().left
  if(x>=offset&&x<=offset+thumbWidth){
   drag.current={pointer:event.pointerId,x:event.clientX,start:value}
   event.currentTarget.setPointerCapture(event.pointerId)
  }else onChange(clamp(value+(x<offset?-1:1)*span*.9,maximum))
 }
 function move(event:PointerEvent<HTMLDivElement>){
  const active=drag.current
  if(!active||active.pointer!==event.pointerId||disabled)return
  onChange(clamp(active.start+(event.clientX-active.x)/travel*maximum,maximum))
 }
 function end(event:PointerEvent<HTMLDivElement>){
  drag.current=undefined
  if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId)
 }
 return <div ref={rail} className="timeline-scrollbar" role="scrollbar" tabIndex={0}
  aria-label={t('timeline.window')} aria-controls={controls} aria-orientation="horizontal"
  aria-valuemin={0} aria-valuemax={maximum} aria-valuenow={value}
  aria-valuetext={disabled?t('timeline.fullWindow'):t('timeline.visibleWindow',{start:formatTime(value,false),end:formatTime(Math.min(duration,value+span),false)})}
  aria-disabled={disabled} title={disabled?t('timeline.fullWindow'):t('timeline.scrollHelp')}
  onPointerDown={begin} onPointerMove={move} onPointerUp={end} onPointerCancel={end}
  onLostPointerCapture={()=>{drag.current=undefined}}
  onKeyDown={event=>{
   const next=event.key==='ArrowLeft'?value-span*.1:event.key==='ArrowRight'?value+span*.1:event.key==='PageUp'?value-span*.9:event.key==='PageDown'?value+span*.9:event.key==='Home'?0:event.key==='End'?maximum:undefined
   if(next===undefined)return
   event.preventDefault()
   if(!disabled)onChange(clamp(next,maximum))
  }}>
  <span className="timeline-scrollbar-track" aria-hidden="true"/>
  <span className="timeline-scrollbar-thumb" aria-hidden="true" style={{width:thumbWidth,transform:`translateX(${offset}px)`}}/>
 </div>
}
