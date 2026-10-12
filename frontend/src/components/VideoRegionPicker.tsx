import {useEffect,useRef,useState} from 'react'
import {useTranslation} from 'react-i18next'
import type {VideoRegion} from '../lib/videoUpload'

export function VideoRegionPicker({file,value,onChange,disabled}:{file:File;value?:VideoRegion;onChange:(value?:VideoRegion)=>void;disabled:boolean}){
 const {t}=useTranslation()
 const [url,setUrl]=useState('')
 const [failed,setFailed]=useState(false)
 const [selecting,setSelecting]=useState(false)
 const [aspect,setAspect]=useState(16/9)
 const start=useRef<{x:number;y:number}|undefined>(undefined)
 useEffect(()=>{if(disabled)setSelecting(false)},[disabled])
 useEffect(()=>{const next=URL.createObjectURL(file);setUrl(next);setFailed(false);return()=>URL.revokeObjectURL(next)},[file])
 function point(event:React.PointerEvent<HTMLElement>){const box=event.currentTarget.getBoundingClientRect();return{x:Math.max(0,Math.min(.99,(event.clientX-box.left)/box.width)),y:Math.max(0,Math.min(.99,(event.clientY-box.top)/box.height))}}
 return <div className="video-region">
  {failed?<p role="status">{t('video.previewUnsupported')}</p>:<>
   <div className="video-region-media" style={{aspectRatio:aspect}}>
    {url?<video src={url} preload="metadata" controls onLoadedMetadata={event=>setAspect(event.currentTarget.videoWidth/event.currentTarget.videoHeight)} onError={()=>{setFailed(true);onChange(undefined)}}/>:null}
    {value?<div className="video-region-box" style={{left:`${value.x*100}%`,top:`${value.y*100}%`,width:`${value.width*100}%`,height:`${value.height*100}%`}}/>:null}
    {selecting?<button type="button" className="video-region-draw" aria-label={t('video.drawRegion')} onPointerDown={event=>{start.current=point(event);event.currentTarget.setPointerCapture(event.pointerId)}} onPointerMove={event=>{if(!start.current)return;const end=point(event);onChange({x:Math.min(start.current.x,end.x),y:Math.min(start.current.y,end.y),width:Math.max(.01,Math.abs(start.current.x-end.x)),height:Math.max(.01,Math.abs(start.current.y-end.y))})}} onPointerUp={()=>{start.current=undefined;setSelecting(false)}} onPointerCancel={()=>{start.current=undefined;setSelecting(false)}}/>:null}
   </div>
   <div className="video-region-actions"><button type="button" className="button" disabled={disabled} onClick={()=>{setSelecting(current=>!current);if(!value)onChange({x:0,y:0,width:1,height:1})}}>{t('video.selectRegion')}</button>{value?<button type="button" className="button" disabled={disabled} onClick={()=>{onChange(undefined);setSelecting(false)}}>{t('video.fullFrame')}</button>:null}</div>
   <p>{selecting?t('video.drawRegion'):t('video.regionHelp')}</p>
   {value?<div className="video-region-fields">{(['x','y','width','height'] as const).map(key=><label key={key}>{t(`video.region.${key}`)}<input type="number" min={0} max={100} step={1} disabled={disabled} value={Math.round(value[key]*100)} onChange={event=>{const next={...value,[key]:Math.max((key==='width'||key==='height') ? .01 : 0,Math.min(key==='x'||key==='y'?.99:1,Number(event.target.value)/100))};next.width=Math.min(next.width,1-next.x);next.height=Math.min(next.height,1-next.y);onChange(next)}}/></label>)}</div>:null}
  </>}
 </div>
}
