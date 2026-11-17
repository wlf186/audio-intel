import {useEffect,useState,type ReactNode} from 'react'
import {useTranslation} from 'react-i18next'
import {Modal} from './Modal'

// Short viewports keep the timeline available without taking space from the reader.
const compactQuery='(max-height:850px), (max-width:700px)'
export function MediaTimeline({children,title,selection}:{children:ReactNode;title:string;selection?:ReactNode}){
 const {t}=useTranslation()
 const [compact,setCompact]=useState(()=>matchMedia(compactQuery).matches)
 const [open,setOpen]=useState(false)
 const [visited,setVisited]=useState(false)
 useEffect(()=>{const query=matchMedia(compactQuery);const update=()=>{setCompact(query.matches);setOpen(false)};query.addEventListener('change',update);return()=>query.removeEventListener('change',update)},[])
 return <div className="result-timeline">{selection}<div className="media-timeline-content" hidden={Boolean(selection)}>{compact?<><button className="button timeline-open" aria-haspopup="dialog" aria-expanded={open} onClick={()=>{setVisited(true);setOpen(true)}}>{t('video.showTimeline')}</button>{visited?<Modal open={open} title={title} closeLabel={t('common.actions.close')} className="timeline-modal" onClose={()=>setOpen(false)}>{children}</Modal>:null}</>:children}</div></div>
}
