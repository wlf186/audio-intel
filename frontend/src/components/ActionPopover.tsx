import {useEffect,useId,useLayoutEffect,useRef,useState,type ReactNode} from 'react'
import {createPortal} from 'react-dom'

export function ActionPopover({label,children,className='',disabled=false,icon,closeOnAction=false,primary=false}:{label:string;children:ReactNode;className?:string;disabled?:boolean;icon?:ReactNode;closeOnAction?:boolean;primary?:boolean}){
 const [open,setOpen]=useState(false)
 const [position,setPosition]=useState({left:8,top:8,maxHeight:400})
 const trigger=useRef<HTMLButtonElement>(null)
 const panel=useRef<HTMLDivElement>(null)
 const id=useId()
 const close=(focus=false)=>{setOpen(false);if(focus)trigger.current?.focus({preventScroll:true})}
 useLayoutEffect(()=>{
  if(!open)return
  const place=()=>{
   const box=trigger.current?.getBoundingClientRect(),popup=panel.current
   if(!box||!popup)return
   const width=popup.getBoundingClientRect().width,height=popup.scrollHeight
   const below=innerHeight-box.bottom-12,above=box.top-12
   const space=below>=Math.min(height,280)||below>=above?below:above
   setPosition({left:Math.max(8,Math.min(innerWidth-width-8,box.right-width)),top:space===below?box.bottom+6:Math.max(8,box.top-Math.min(height,space)-6),maxHeight:Math.max(80,space)})
  }
  place()
  const observer=new ResizeObserver(place);if(panel.current)observer.observe(panel.current)
  window.addEventListener('resize',place);window.addEventListener('scroll',place,true)
  return()=>{observer.disconnect();window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true)}
 },[open])
 useEffect(()=>{
  if(!open)return
  panel.current?.querySelector<HTMLElement>('[data-initial-focus],textarea,input,button')?.focus({preventScroll:true})
  const outside=(event:Event)=>{const target=event.target as Node;if(!panel.current?.contains(target)&&!trigger.current?.contains(target))close()}
  const keyboard=(event:KeyboardEvent)=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close(true)}}
  document.addEventListener('pointerdown',outside);document.addEventListener('focusin',outside);document.addEventListener('keydown',keyboard)
  return()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('focusin',outside);document.removeEventListener('keydown',keyboard)}
 },[open])
 return <div className={`action-popover ${className}`}><button type="button" ref={trigger} className={primary?'primary':'button'} aria-label={label} title={label} aria-expanded={open} aria-controls={open?id:undefined} onClick={()=>setOpen(value=>!value)} disabled={disabled}>{icon}{label}</button>{open?createPortal(<div ref={panel} id={id} role="dialog" aria-label={label} className={`action-popover-panel ${className}-panel`} style={{left:position.left,top:position.top,maxHeight:position.maxHeight}} onClick={event=>{if(closeOnAction&&(event.target as Element).closest('button,a[download]'))close(true)}}>{children}</div>,document.body):null}</div>
}
