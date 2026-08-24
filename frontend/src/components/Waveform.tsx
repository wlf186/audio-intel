import {memo,useEffect,useRef} from 'react'

export const Waveform=memo(function Waveform({peaks=[],progress=0,onSeek}:{peaks?:number[];progress?:number;onSeek?:(value:number)=>void}){
  const ref=useRef<HTMLCanvasElement>(null)
  useEffect(()=>{const canvas=ref.current;if(!canvas)return;const draw=()=>{const ratio=devicePixelRatio||1;const rect=canvas.getBoundingClientRect();canvas.width=rect.width*ratio;canvas.height=rect.height*ratio;const ctx=canvas.getContext('2d');if(!ctx)return;ctx.scale(ratio,ratio);ctx.clearRect(0,0,rect.width,rect.height);const values=peaks.length?peaks:Array.from({length:180},(_,i)=>.12+.55*Math.abs(Math.sin(i*.31)*Math.sin(i*.067)));const step=rect.width/values.length;values.forEach((v,i)=>{const height=Math.max(2,v*(rect.height*.8));ctx.fillStyle=i/values.length<=progress?'#f4ed00':'#00aab2';ctx.fillRect(i*step,(rect.height-height)/2,Math.max(1,step*.48),height)});};draw();const observer=new ResizeObserver(draw);observer.observe(canvas);return()=>observer.disconnect()},[peaks,progress])
  return <canvas ref={ref} className="waveform" onClick={event=>{const r=event.currentTarget.getBoundingClientRect();onSeek?.((event.clientX-r.left)/r.width)}} aria-label="音频波形"/>
})
