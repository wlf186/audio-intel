import {Info} from 'lucide-react'
import {useLayoutEffect,useRef,useState} from 'react'
import {createPortal} from 'react-dom'
import {useTranslation} from 'react-i18next'

export function InfoTooltip({id,text,label}:{id:string;text:string;label?:string}){
 const {t}=useTranslation()
 const button=useRef<HTMLButtonElement>(null)
 const popup=useRef<HTMLSpanElement>(null)
 const [hovered,setHovered]=useState(false)
 const [focused,setFocused]=useState(false)
 const open=hovered||focused
 const [position,setPosition]=useState({left:0,top:0,width:280})
 useLayoutEffect(()=>{
  if(!open)return
  const place=()=>{
   const box=button.current?.getBoundingClientRect()
   if(!box)return
   const width=Math.min(280,innerWidth-32),height=popup.current?.getBoundingClientRect().height||0
   const left=Math.max(8,Math.min(innerWidth-width-8,box.left+box.width/2-width/2))
   const top=box.top-height-8>=8?box.top-height-8:Math.max(8,Math.min(innerHeight-height-8,box.bottom+8))
   setPosition({left,top,width})
  }
  place();addEventListener('resize',place);addEventListener('scroll',place,true)
  return()=>{removeEventListener('resize',place);removeEventListener('scroll',place,true)}
 },[open,text])
 return <span className="info-tooltip" onMouseEnter={()=>setHovered(true)} onMouseLeave={()=>setHovered(false)}><button ref={button} type="button" aria-label={label||t('common.accelerationHelp')} aria-describedby={open?id:undefined} onFocus={()=>setFocused(true)} onBlur={()=>setFocused(false)} onKeyDown={event=>{if(event.key==='Escape'){setHovered(false);setFocused(false)}}}><Info size={14}/></button>{open?createPortal(<span ref={popup} id={id} role="tooltip" className="info-tooltip-popup" style={position}>{text}</span>,document.body):null}</span>
}
