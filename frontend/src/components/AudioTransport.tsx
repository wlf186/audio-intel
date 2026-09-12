import {useEffect,useRef,useState} from 'react'
import {Pause,Play} from 'lucide-react'
import {formatTime} from '../lib/api'
import {useAudioWaveform} from './useAudioWaveform'
import {Waveform} from './Waveform'
import {useTranslation} from 'react-i18next'

export function AudioTransport({src,peaks,duration=0,active=true}:{src:string;peaks?:number[];duration?:number;active?:boolean}){
 const {t}=useTranslation()
 const waveform=useAudioWaveform(src,Boolean(peaks?.length),active)
 const audio=useRef<HTMLAudioElement>(null)
 const [metadata,setMetadata]=useState({src:'',duration:0})
 const [playing,setPlaying]=useState(false)
 const [currentTime,setCurrentTime]=useState(0)
 const [playbackError,setPlaybackError]=useState(false)
 useEffect(()=>{const player=audio.current;if(player){player.pause();player.currentTime=0;player.load()}setPlaying(false);setCurrentTime(0);setPlaybackError(false)},[src])
 const toggle=async()=>{const player=audio.current;if(!player)return;setPlaybackError(false);try{if(player.paused)await player.play();else player.pause()}catch{setPlaybackError(true)}}
 const seek=(ratio:number)=>{const player=audio.current;if(!player)return;const total=Number.isFinite(player.duration)&&player.duration>0?player.duration:waveform.data?.duration||duration;if(!total)return;player.currentTime=Math.max(0,Math.min(total,total*ratio));setCurrentTime(player.currentTime)}
 const total=metadata.src===src&&Number.isFinite(metadata.duration)&&metadata.duration>0?metadata.duration:waveform.data?.duration||duration
 const displayedPeaks=peaks?.length?peaks:waveform.data?.waveform
 return <div className="audio-transport" ref={waveform.container}><audio ref={audio} className="sr-only" preload="metadata" src={src} onPlay={()=>setPlaying(true)} onPause={()=>setPlaying(false)} onEnded={()=>setPlaying(false)} onError={()=>setPlaybackError(true)} onLoadedMetadata={event=>{setCurrentTime(event.currentTarget.currentTime);setMetadata({src,duration:event.currentTarget.duration})}} onDurationChange={event=>setMetadata({src,duration:event.currentTarget.duration})} onTimeUpdate={event=>setCurrentTime(event.currentTarget.currentTime)}/><Waveform peaks={displayedPeaks} currentTime={currentTime} duration={total} onSeek={seek}/>{!displayedPeaks?.length?<div className="waveform-status" aria-live="polite">{waveform.error?<><span>{t('audio.waveformError')}</span><button type="button" disabled={waveform.retryDisabled} onClick={waveform.retry}>{t('audio.retryWaveform')}</button></>:<span>{t('audio.waveformLoading')}</span>}</div>:null}<div className="transport-row"><button className="round" aria-label={playing?t('audio.pauseSynthesis'):t('audio.playSynthesis')} onClick={()=>void toggle()}>{playing?<Pause/>:<Play/>}</button><strong>{formatTime(currentTime,false)}</strong><span>/ {formatTime(total,false)}</span><span className="transport-track"><i style={{width:`${total?Math.min(100,currentTime/total*100):0}%`}}/></span><small>{playing?'PLAYING':'READY'}</small></div>{playbackError?<p className="media-error" role="alert">{t('audio.playbackError')}</p>:null}</div>
}
