import {useEffect,useRef,useState} from 'react'
import {Pause,Play} from 'lucide-react'
import {formatTime} from '../lib/api'
import {Waveform} from './Waveform'

export function AudioTransport({src,peaks,duration=0}:{src:string;peaks?:number[];duration?:number}){
 const audio=useRef<HTMLAudioElement>(null)
 const [playing,setPlaying]=useState(false)
 const [currentTime,setCurrentTime]=useState(0)
 const [error,setError]=useState('')
 useEffect(()=>{const player=audio.current;if(player){player.pause();player.currentTime=0;player.load()}setPlaying(false);setCurrentTime(0);setError('')},[src])
 const toggle=async()=>{const player=audio.current;if(!player)return;setError('');try{if(player.paused)await player.play();else player.pause()}catch{setError('当前浏览器无法播放该音频编码。')}}
 const seek=(ratio:number)=>{const player=audio.current;if(!player)return;const total=player.duration||duration;if(!total)return;player.currentTime=Math.max(0,Math.min(total,total*ratio));setCurrentTime(player.currentTime)}
 const total=audio.current?.duration||duration
 return <div className="audio-transport"><audio ref={audio} className="sr-only" preload="metadata" src={src} onPlay={()=>setPlaying(true)} onPause={()=>setPlaying(false)} onEnded={()=>setPlaying(false)} onError={()=>setError('当前浏览器无法播放该音频编码。')} onLoadedMetadata={event=>setCurrentTime(event.currentTarget.currentTime)} onTimeUpdate={event=>setCurrentTime(event.currentTarget.currentTime)}/><Waveform peaks={peaks} progress={total?currentTime/total:0} onSeek={seek}/><div className="transport-row"><button className="round" aria-label={playing?'暂停当前合成结果':'播放当前合成结果'} onClick={()=>void toggle()}>{playing?<Pause/>:<Play/>}</button><strong>{formatTime(currentTime,false)}</strong><span>/ {formatTime(total,false)}</span><span className="transport-track"><i style={{width:`${total?Math.min(100,currentTime/total*100):0}%`}}/></span><small>{playing?'PLAYING':'READY'}</small></div>{error?<p className="media-error" role="alert">{error}</p>:null}</div>
}
