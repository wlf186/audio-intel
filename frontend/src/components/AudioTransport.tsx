import {useEffect,useRef,useState} from 'react'
import {Pause,Play} from 'lucide-react'
import {formatTime} from '../lib/api'
import {Waveform} from './Waveform'
import {useTranslation} from 'react-i18next'

export function AudioTransport({src,peaks,duration=0}:{src:string;peaks?:number[];duration?:number}){
 const {t}=useTranslation()
 const audio=useRef<HTMLAudioElement>(null)
 const [playing,setPlaying]=useState(false)
 const [currentTime,setCurrentTime]=useState(0)
 const [playbackError,setPlaybackError]=useState(false)
 useEffect(()=>{const player=audio.current;if(player){player.pause();player.currentTime=0;player.load()}setPlaying(false);setCurrentTime(0);setPlaybackError(false)},[src])
 const toggle=async()=>{const player=audio.current;if(!player)return;setPlaybackError(false);try{if(player.paused)await player.play();else player.pause()}catch{setPlaybackError(true)}}
 const seek=(ratio:number)=>{const player=audio.current;if(!player)return;const total=player.duration||duration;if(!total)return;player.currentTime=Math.max(0,Math.min(total,total*ratio));setCurrentTime(player.currentTime)}
 const total=audio.current?.duration||duration
 return <div className="audio-transport"><audio ref={audio} className="sr-only" preload="metadata" src={src} onPlay={()=>setPlaying(true)} onPause={()=>setPlaying(false)} onEnded={()=>setPlaying(false)} onError={()=>setPlaybackError(true)} onLoadedMetadata={event=>setCurrentTime(event.currentTarget.currentTime)} onTimeUpdate={event=>setCurrentTime(event.currentTarget.currentTime)}/><Waveform peaks={peaks} currentTime={currentTime} duration={total} onSeek={seek}/><div className="transport-row"><button className="round" aria-label={playing?t('audio.pauseSynthesis'):t('audio.playSynthesis')} onClick={()=>void toggle()}>{playing?<Pause/>:<Play/>}</button><strong>{formatTime(currentTime,false)}</strong><span>/ {formatTime(total,false)}</span><span className="transport-track"><i style={{width:`${total?Math.min(100,currentTime/total*100):0}%`}}/></span><small>{playing?'PLAYING':'READY'}</small></div>{playbackError?<p className="media-error" role="alert">{t('audio.playbackError')}</p>:null}</div>
}
