import {useEffect,useRef,useState} from 'react'
import {createPortal} from 'react-dom'
import {Fingerprint} from 'lucide-react'
import {useTranslation} from 'react-i18next'
import type {VideoScenesResource} from '../hooks/useVideoScenes'
import {formatTime} from '../lib/api'
import type {Job,JobResult,VideoScene} from '../lib/types'
import {Modal} from './Modal'
import {SceneDetection} from './SceneDetection'
import type {SceneDetectionCapability} from '../lib/sceneDetection'
import {readReaderBookmark} from '../lib/workspaceSession'
import {readingIndex,revealReadingNode} from '../lib/readerNavigation'
import {useReaderBookmark} from '../hooks/useReaderBookmark'

export function Frame({jobId,scene}:{jobId:string;scene:Pick<VideoScene,'image'|'frame_time'>}){
 const {t}=useTranslation();const [error,setError]=useState(false);const [retry,setRetry]=useState(0);const [expanded,setExpanded]=useState(false)
 useEffect(()=>setError(false),[scene.image])
 const url=`/api/v1/jobs/${jobId}/video-frames/${scene.image}?retry=${retry}`
 const label=t('video.frameAt',{time:formatTime(scene.frame_time??0,false)})
 if(!scene.image)return <div className="video-no-frame">{t('video.noStableFrame')}</div>
 return error?<p role="alert">{t('video.frameFailed')} <button className="button" onClick={()=>{setError(false);setRetry(value=>value+1)}}>{t('common.actions.reload')}</button></p>:<><button type="button" className="video-frame" aria-label={t('workspaceSplit.enlargeFrame')} title={t('workspaceSplit.enlargeFrame')} onClick={()=>setExpanded(true)}><img loading="lazy" src={url} alt={label} onError={()=>setError(true)}/></button>{expanded?<Modal title={label} closeLabel={t('common.actions.close')} className="video-frame-modal" onClose={()=>setExpanded(false)}><img src={url} alt={label} onError={()=>{setExpanded(false);setError(true)}}/></Modal>:null}</>
}
export function VideoTranscript({job,toolbar,onUpdated,onSeek,onRename,onEnroll,resource,reveal,currentTime=0,detectionCapability,query='',speakerFilter='all'}:{job:Job;query?:string;speakerFilter?:string;resource:VideoScenesResource;reveal?:{sceneId?:string;time:number;token:number};toolbar?:HTMLElement|null;onUpdated:(id:string,result:JobResult)=>void;onSeek:(time:number)=>void;onRename:(id:string)=>void;onEnroll:(id:string,segmentIds:number[])=>void;currentTime?:number;detectionCapability?:SceneDetectionCapability}){
 const {t}=useTranslation()
 const {data,error,loading:refreshing,reload}=resource
 const [detectionOpen,setDetectionOpen]=useState(false)
 const [visible,setVisible]=useState(()=>readReaderBookmark(`${job.id}:video`)?.visibleCount||20)
 const revealedToken=useRef<number|undefined>(undefined)
 const scrollRoot=useRef<HTMLDivElement>(null)
 const sentinel=useRef<HTMLDivElement>(null)
 const {prepareRefresh}=useReaderBookmark(`${job.id}:video`,scrollRoot,{active:true,ready:Boolean(data)&&!refreshing,visibleCount:visible,query,speakerFilter})
 useEffect(()=>{if(refreshing)prepareRefresh()},[refreshing,prepareRefresh])
 useEffect(()=>{
  if(!reveal||!data||refreshing||revealedToken.current===reveal.token)return
  const index=reveal.sceneId?data.scenes.findIndex(scene=>scene.id===reveal.sceneId):readingIndex(data.scenes,reveal.time)
  if(index<0)return
  if(index>=visible){setVisible(index+1);return}
  const node=[...scrollRoot.current?.querySelectorAll<HTMLElement>('[data-reader-id]')||[]].find(node=>node.dataset.readerId===data.scenes[index].id)
  if(node&&scrollRoot.current){
    revealedToken.current=reveal.token
    const paragraphs=data.scenes[index].paragraphs||[]
    const paragraph=node.querySelectorAll<HTMLElement>('.video-paragraph')[readingIndex(paragraphs,reveal.time)]
    revealReadingNode(scrollRoot.current,paragraph||node)
  }
 },[reveal,data,refreshing,visible])
 useEffect(()=>{
  const root=scrollRoot.current,target=sentinel.current
  if(!root||!target||!data||refreshing||visible>=data.scenes.length)return
  let active=true
  const observer=new IntersectionObserver(entries=>{if(active&&entries.some(entry=>entry.isIntersecting))setVisible(value=>Math.min(data.scenes.length,value+20))},{root,rootMargin:'0px 0px 360px'})
  observer.observe(target)
  return()=>{active=false;observer.disconnect()}
 },[data,visible,refreshing])
 const hiddenScenes=new Set(data?.revision===job.result?.video?.revision?job.result?.video?.export_hidden_scene_ids||[]:[])
 const activeScene=data?.scenes[readingIndex(data.scenes,currentTime)]
 const activeParagraph=activeScene?.paragraphs?.[readingIndex(activeScene.paragraphs,currentTime)]
 const tools=<div className="video-reading-tools">{detectionCapability?.preview?<button className="button" disabled={!data||refreshing||Boolean(error)} onClick={()=>setDetectionOpen(true)}>{t('sceneDetection.title')}</button>:null}</div>
 return <>{detectionOpen&&detectionCapability&&job.result?<SceneDetection jobId={job.id} result={job.result} capability={detectionCapability} onClose={()=>setDetectionOpen(false)} onUpdated={onUpdated}/>:null}{toolbar?createPortal(tools,toolbar):tools}<div className="video-transcript" ref={scrollRoot} aria-busy={refreshing}>
  {error?<p className="error" role="alert">{error} <button className="button" onClick={()=>reload()}>{t('common.actions.reload')}</button></p>:refreshing?<p className="reader-refresh-status" role="status">{t('video.loading')}</p>:null}
  {data?<>
   {data.scenes.slice(0,visible).map((scene,index)=>{
    const paragraphs=scene.paragraphs?.filter(p=>(speakerFilter==='all'||p.speaker===speakerFilter)&&(!query.trim()||p.text.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())))
    if((query.trim()||speakerFilter!=='all')&&!paragraphs?.length)return null
    return <section className={`video-section${activeScene?.id===scene.id?' navigation-active':''}`} key={scene.id} data-reader-id={scene.id} data-reader-time={scene.start}>
    <header><h3>{t('video.scene',{number:index+1})}</h3><button className="video-scene-time" onClick={()=>onSeek(scene.start)}>{formatTime(scene.start,false)} – {formatTime(scene.end,false)}</button>{hiddenScenes.has(scene.id)?<span className="scene-hidden-badge">{t('sceneDetection.hidden')}</span>:null}</header>
    <div className="video-scene-content"><Frame jobId={job.id} scene={scene}/><div className="video-scene-text">
    {paragraphs?.length?paragraphs.map((paragraph,i)=><div className={`video-paragraph${paragraph===activeParagraph?' navigation-active':''}`} key={i}><div><button className="video-speaker" onClick={()=>onRename(paragraph.speaker)}>{paragraph.speaker_label}</button><button className="video-paragraph-time" onClick={()=>onSeek(paragraph.start)}>{formatTime(paragraph.start,false)} – {formatTime(paragraph.end,false)}</button><button className="icon-button video-enroll" title={t('asr.results.addToVoiceprints')} aria-label={t('asr.results.addToVoiceprints')} onClick={()=>onEnroll(paragraph.speaker,paragraph.segment_ids)}><Fingerprint size={17}/></button></div><p>{paragraph.text}</p></div>):<p className="video-silent">{t('video.noSpeech')}</p>}
    </div></div>

   </section>})}
   <div className="segment-load-more" ref={sentinel}><span>{t('video.sceneCount',{visible:Math.min(visible,data.scenes.length),total:data.scenes.length})}</span>{visible<data.scenes.length?<button className="button" onClick={()=>setVisible(value=>Math.min(data.scenes.length,value+20))}>{t('asr.results.loadMore')}</button>:null}</div>
  </>:null}
 </div></>
}
