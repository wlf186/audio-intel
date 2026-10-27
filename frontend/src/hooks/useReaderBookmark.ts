import {useCallback,useEffect,useLayoutEffect,useRef,type RefObject} from 'react'
import {readReaderBookmark,saveReaderBookmark} from '../lib/workspaceSession'

type Options={active:boolean;ready:boolean;visibleCount:number;query?:string;speakerFilter?:string}
export function useReaderBookmark(key:string,root:RefObject<HTMLDivElement|null>,options:Options){
 const current=useRef({key,...options});current.current={key,...options}
 const restore=useRef(true)
 const previousKey=useRef(key)
 const previousFilter=useRef(`${options.query||''}\n${options.speakerFilter||''}`)
 if(previousKey.current!==key){previousKey.current=key;restore.current=true}
 const filter=`${options.query||''}\n${options.speakerFilter||''}`
 if(previousFilter.current!==filter){previousFilter.current=filter;restore.current=true}
 const capture=useCallback(()=>{
  const element=root.current,info=current.current
  if(!element||!info.key||!info.active||!info.ready||restore.current)return
  const top=element.getBoundingClientRect().top
  const anchor=[...element.querySelectorAll<HTMLElement>('[data-reader-id]')].find(item=>item.getBoundingClientRect().bottom>top+1)
  saveReaderBookmark(info.key,{anchorId:anchor?.dataset.readerId||'',time:Number(anchor?.dataset.readerTime)||0,offset:anchor?anchor.getBoundingClientRect().top-top:0,scrollTop:element.scrollTop,visibleCount:info.visibleCount,query:info.query,speakerFilter:info.speakerFilter})
 },[root])
 useLayoutEffect(()=>{
  const element=root.current
  if(!options.active){restore.current=true;return}
  if(!element||!options.ready||!restore.current)return
  const saved=readReaderBookmark(key)
  if(saved&&(saved.query||'')===(options.query||'')&&(saved.speakerFilter||'all')===(options.speakerFilter||'all')){
   if(!saved.anchorId){element.scrollTop=saved.scrollTop;restore.current=false;return}
   const items=[...element.querySelectorAll<HTMLElement>('[data-reader-id]')]
   const anchor=items.find(item=>item.dataset.readerId===saved.anchorId)||items.find(item=>Number(item.dataset.readerTime)>=saved.time)||items.at(-1)
   if(anchor){
    // Lay out deferred rows before measuring the anchor. Their intrinsic estimates
    // can differ from the rendered height after a view has been remounted.
    const deferred=items.filter(item=>getComputedStyle(item).contentVisibility==='auto')
    for(const item of deferred)item.style.contentVisibility='visible'
    const align=()=>{element.scrollTop+=anchor.getBoundingClientRect().top-element.getBoundingClientRect().top-saved.offset}
    align()
    if(deferred.length){
     const frame=requestAnimationFrame(()=>{for(const item of deferred)item.style.removeProperty('content-visibility');align();restore.current=false})
     return()=>{cancelAnimationFrame(frame);for(const item of deferred)item.style.removeProperty('content-visibility')}
    }
   }
   else element.scrollTop=saved.scrollTop
  }else element.scrollTop=0
  restore.current=false
 },[key,root,options.active,options.ready,options.visibleCount,options.query,options.speakerFilter])
 useEffect(()=>{
  const element=root.current
  if(!element||!options.active)return
  let timer:ReturnType<typeof setTimeout>|undefined
  const scroll=()=>{if(!timer)timer=setTimeout(()=>{timer=undefined;capture()},150)}
  element.addEventListener('scroll',scroll,{passive:true})
  window.addEventListener('audio-intel:checkpoint-workspace',capture)
  window.addEventListener('pagehide',capture)
  return()=>{if(timer)clearTimeout(timer);capture();element.removeEventListener('scroll',scroll);window.removeEventListener('audio-intel:checkpoint-workspace',capture);window.removeEventListener('pagehide',capture)}
 },[root,key,options.active,options.ready,capture])
 const prepareRefresh=useCallback(()=>{capture();restore.current=true},[capture])
 return{capture,prepareRefresh}
}
