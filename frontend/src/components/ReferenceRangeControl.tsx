import {useEffect,useMemo,useRef,useState,type PointerEvent} from 'react'
import {Pause,Play,SlidersHorizontal} from 'lucide-react'
import {useTranslation} from 'react-i18next'
import type {VoiceprintSample} from '../lib/types'
import {previewReferenceRange,rangeError,referenceTime,type ReferenceRange,type ReferenceRangeCapability} from '../lib/referenceRanges'
import {Modal} from './Modal'
import {Waveform} from './Waveform'
import {useAudioWaveform} from './useAudioWaveform'
import './reference-range.css'

type Props={sample:VoiceprintSample;capability:ReferenceRangeCapability;value?:ReferenceRange;onChange:(value?:ReferenceRange)=>void}

export function ReferenceRangeControl({sample,capability,value,onChange}:Props){
 const {t}=useTranslation()
 const [open,setOpen]=useState(false)
 const error=value?rangeError(value,sample.duration,capability,false):undefined
 return <section className="reference-range-card" aria-label={t('referenceRange.label')}>
  <div className="reference-range-heading"><SlidersHorizontal size={16}/><strong>{t('referenceRange.label')}</strong><span>{t(value?'referenceRange.manual':'referenceRange.auto')}</span></div>
  <p>{value?`${referenceTime(value.start)} – ${referenceTime(value.end)} · ${(value.end-value.start).toFixed(3)} s`:t('referenceRange.defaultSummary',{seconds:capability.default_max_seconds})}</p>
  {error?<p className="error" role="alert">{t(`referenceRange.${error}`)}</p>:null}
  <div className="reference-range-actions">
   <button type="button" className="reference-range-button" disabled={!sample.audio_url||Boolean(sample.duration&&sample.duration<capability.min_seconds)} onClick={()=>setOpen(true)}>{t(value?'referenceRange.edit':'referenceRange.adjust')}</button>
   {value?<button type="button" className="reference-range-button quiet reference-range-auto" onClick={()=>onChange()}>{t('referenceRange.useAuto',{seconds:capability.default_max_seconds})}</button>:null}
  </div>
  {sample.duration&&sample.duration<capability.min_seconds?<small>{t('referenceRange.tooShort',{seconds:capability.min_seconds})}</small>:null}
  {open?<ReferenceRangeEditor key={sample.id} sample={sample} capability={capability} value={value} onClose={()=>setOpen(false)} onApply={range=>{onChange(range);setOpen(false)}}/>:null}
 </section>
}

function ReferenceRangeEditor({sample,capability,value,onClose,onApply}:Omit<Props,'onChange'>&{onClose:()=>void;onApply:(value:ReferenceRange)=>void}){
 const {t}=useTranslation()
 const [selection,setSelection]=useState<ReferenceRange>(()=>value||{start:0,end:Math.min(sample.duration||capability.default_max_seconds,capability.default_max_seconds)})
 const [metadataDuration,setMetadataDuration]=useState<number>()
 const [playing,setPlaying]=useState(false)
 const [currentTime,setCurrentTime]=useState(0)
 const [playbackError,setPlaybackError]=useState(false)
 const audio=useRef<HTMLAudioElement>(null)
 const track=useRef<HTMLDivElement>(null)
 const drag=useRef<'start'|'end'|null>(null)
 const waveform=useAudioWaveform(sample.audio_url!,false,true)
 const duration=waveform.data?.duration||metadataDuration||sample.duration
 const basicError=rangeError(selection,duration,capability)
 const preview=useMemo(()=>!basicError&&duration?previewReferenceRange(sample,selection,duration):{range:selection},[basicError,duration,sample,selection])
 const error=basicError||preview.error||rangeError(preview.range,duration,capability)
 const effective=preview.range
 const pause=()=>{audio.current?.pause();setPlaying(false)}
 useEffect(()=>{
  const player=audio.current
  const hidden=()=>{if(document.hidden)player?.pause()}
  document.addEventListener('visibilitychange',hidden)
  return()=>{player?.pause();document.removeEventListener('visibilitychange',hidden)}
 },[])
 useEffect(()=>{
  if(!playing)return
  let frame=0
  const tick=()=>{
   const player=audio.current
   if(!player)return
   setCurrentTime(player.currentTime)
   if(player.currentTime>=effective.end||player.currentTime<effective.start){player.pause();player.currentTime=effective.end;setCurrentTime(effective.end);setPlaying(false);return}
   frame=requestAnimationFrame(tick)
  }
  frame=requestAnimationFrame(tick)
  return()=>cancelAnimationFrame(frame)
 },[playing,effective.start,effective.end])
 const change=(next:ReferenceRange)=>{pause();setSelection(next)}
 const move=(event:PointerEvent<HTMLElement>)=>{
  if(!drag.current||!duration||!track.current)return
  const box=track.current.getBoundingClientRect()
  const seconds=Math.round(Math.max(0,Math.min(duration,(event.clientX-box.left)/box.width*duration))*1000)/1000
  setSelection(current=>drag.current==='start'?{...current,start:Math.max(0,Math.min(current.end-capability.min_seconds,Math.max(current.end-capability.max_seconds,seconds)))}:{...current,end:Math.min(duration,Math.max(current.start+capability.min_seconds,Math.min(current.start+capability.max_seconds,seconds)))})
 }
 const snap=()=>{if(!error&&!preview.pending)setSelection(effective)}
 const play=async()=>{
  const player=audio.current
  if(!player||error)return
  if(!player.paused){pause();return}
  setPlaybackError(false)
  player.currentTime=effective.start
  setCurrentTime(effective.start)
  try{await player.play()}catch{setPlaybackError(true)}
 }
 return <Modal title={t('referenceRange.title')} closeLabel={t('referenceRange.close')} onClose={onClose} className="reference-range-modal">
  <p className="reference-range-intro">{sample.name} · {duration?`${duration.toFixed(3)} s`:t('referenceRange.unknownDuration')}</p>
  <p>{t('referenceRange.help',{min:capability.min_seconds,max:capability.max_seconds})}</p>
  <div ref={waveform.container} className="reference-range-waveform">
   <audio ref={audio} src={sample.audio_url} preload="metadata" onLoadedMetadata={event=>{const seconds=event.currentTarget.duration;if(Number.isFinite(seconds)&&seconds>0)setMetadataDuration(seconds)}} onTimeUpdate={event=>{if(event.currentTarget.currentTime>=effective.end&&!event.currentTarget.paused){event.currentTarget.pause();event.currentTarget.currentTime=effective.end;setCurrentTime(effective.end)}}} onPlay={()=>setPlaying(true)} onPause={()=>setPlaying(false)} onEnded={()=>setPlaying(false)} onError={()=>setPlaybackError(true)}/>
   <div className="reference-range-track" ref={track}>
    <Waveform peaks={waveform.data?.waveform} duration={duration} currentTime={currentTime}/>
    {duration?<>
     <div className="reference-range-selection" style={{left:`${Math.max(0,selection.start/duration*100)}%`,width:`${Math.max(0,Math.min(duration,selection.end)-Math.max(0,selection.start))/duration*100}%`}}/>
     {(['start','end'] as const).map(edge=><button key={edge} type="button" role="slider" aria-label={t(`referenceRange.${edge}Handle`)} aria-valuemin={edge==='start'?Math.max(0,selection.end-capability.max_seconds):selection.start+capability.min_seconds} aria-valuemax={edge==='start'?selection.end-capability.min_seconds:Math.min(duration,selection.start+capability.max_seconds)} aria-valuenow={selection[edge]} aria-valuetext={referenceTime(selection[edge])} className={`reference-range-handle ${edge}`} style={{left:`${Math.max(0,Math.min(100,selection[edge]/duration*100))}%`}} onPointerDown={event=>{pause();drag.current=edge;event.currentTarget.setPointerCapture(event.pointerId)}} onPointerMove={move} onPointerUp={()=>{drag.current=null;snap()}} onPointerCancel={()=>{drag.current=null}} onKeyDown={event=>{
      const low=edge==='start'?Math.max(0,selection.end-capability.max_seconds):selection.start+capability.min_seconds
      const high=edge==='start'?selection.end-capability.min_seconds:Math.min(duration,selection.start+capability.max_seconds)
      let next:number|undefined
      if(event.key==='ArrowLeft'||event.key==='ArrowDown')next=selection[edge]-(event.shiftKey?1:.1)
      if(event.key==='ArrowRight'||event.key==='ArrowUp')next=selection[edge]+(event.shiftKey?1:.1)
      if(event.key==='Home')next=low
      if(event.key==='End')next=high
      if(next!==undefined){event.preventDefault();change({...selection,[edge]:Math.round(Math.max(low,Math.min(high,next))*1000)/1000})}
     }}><span>{t(`referenceRange.${edge}Short`)}</span></button>)}
    </>:null}
   </div>
   {!waveform.data?<div className="waveform-status" aria-live="polite">{waveform.error?<><span>{t('audio.waveformError')}</span><button type="button" className="reference-range-button" disabled={waveform.retryDisabled} onClick={waveform.retry}>{t('audio.retryWaveform')}</button></>:t('audio.waveformLoading')}</div>:null}
  </div>
  <div className="reference-range-inputs">
   {(['start','end'] as const).map(edge=><label key={edge}>{t(`referenceRange.${edge}Input`)}<input type="number" min="0" max={duration} step="0.001" value={Number.isFinite(selection[edge])?selection[edge]:''} onChange={event=>change({...selection,[edge]:event.currentTarget.valueAsNumber})}/></label>)}
  </div>
  {error?<p className="error" role="alert">{t(`referenceRange.${error}`,{min:capability.min_seconds,max:capability.max_seconds})}</p>:<p className="reference-range-effective" aria-live="polite">{t('referenceRange.effective')} {referenceTime(effective.start)} – {referenceTime(effective.end)} · {(effective.end-effective.start).toFixed(3)} s</p>}
  {preview.pending?<p className="reference-range-note">{t('referenceRange.pending')}</p>:preview.text?<div className="reference-range-text"><strong>{t('referenceRange.transcript')}</strong><p>{preview.text}</p></div>:null}
  <div className="reference-range-preview"><button type="button" className="reference-range-button" disabled={Boolean(error)||!metadataDuration} onClick={()=>void play()}>{playing?<Pause size={16}/>:<Play size={16}/>} {t(playing?'referenceRange.pause':'referenceRange.listen')}</button><span>{referenceTime(currentTime)}</span></div>
  {playbackError?<p className="error" role="alert">{t('audio.playbackError')}</p>:null}
  <div className="reference-range-footer"><button type="button" className="reference-range-button quiet" onClick={onClose}>{t('referenceRange.cancel')}</button><button type="button" className="primary" disabled={Boolean(error)} onClick={()=>onApply(effective)}>{t('referenceRange.apply')}</button></div>
 </Modal>
}
