import {useEffect,useId,useRef,type ReactNode} from 'react'
import {X} from 'lucide-react'

type Props={title:string;closeLabel:string;onClose:()=>void;children:ReactNode}

export function Modal({title,closeLabel,onClose,children}:Props){
 const dialogRef=useRef<HTMLDialogElement>(null)
 const titleId=useId()
 useEffect(()=>{
  const dialog=dialogRef.current
  if(!dialog)return
  const returnFocus=document.activeElement instanceof HTMLElement?document.activeElement:null
  const previousOverflow=document.body.style.overflow
  document.body.style.overflow='hidden'
  dialog.showModal()
  const initial=dialog.querySelector<HTMLElement>('input:not([disabled]),select:not([disabled]),textarea:not([disabled]),button:not([disabled]):not(.modal-close)')
  initial?.focus()
  return()=>{document.body.style.overflow=previousOverflow;if(dialog.open)dialog.close();returnFocus?.focus()}
 },[])
 return <dialog ref={dialogRef} className="modal-card" aria-labelledby={titleId} onCancel={event=>{event.preventDefault();onClose()}}>
  <button className="modal-close" aria-label={closeLabel} onClick={onClose}><X/></button>
  <h2 id={titleId}>{title}</h2>
  {children}
 </dialog>
}
