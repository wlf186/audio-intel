export function highlightText(text:string,query:string){
 const needle=query.trim().toLocaleLowerCase()
 if(!needle)return text
 const lower=text.toLocaleLowerCase(),parts=[]
 let offset=0,index=lower.indexOf(needle)
 while(index>=0){
  parts.push(text.slice(offset,index),<mark data-search-hit key={index}>{text.slice(index,index+needle.length)}</mark>)
  offset=index+needle.length;index=lower.indexOf(needle,offset)
 }
 parts.push(text.slice(offset))
 return parts
}

import {useLayoutEffect,useMemo,useState,useRef,type RefObject} from 'react'
import {useTranslation} from 'react-i18next'
export function TranscriptSearchNavigation({query,entries,root,onReveal}:{query:string;entries:{id:string;texts:string[]}[];root:RefObject<HTMLElement|null>;onReveal:(id:string)=>void}){
 const {t}=useTranslation()
 const needle=query.trim().toLocaleLowerCase()
 const hits=useMemo(()=>entries.flatMap(entry=>{
  const matches:{id:string;occurrence:number}[]=[]
  if(!needle)return matches
  let occurrence=0
  for(const text of entry.texts){const lower=text.toLocaleLowerCase();let index=lower.indexOf(needle);while(index>=0){matches.push({id:entry.id,occurrence:occurrence++});index=lower.indexOf(needle,index+needle.length)}}
  return matches
 }),[entries,needle])
 const [selection,setSelection]=useState<{query:string;index:number}|null>(null)
 const index=selection?.query===query?Math.min(selection.index,Math.max(0,hits.length-1)):-1
 const last=useRef<{node:HTMLElement;query:string;index:number}|null>(null)
 useLayoutEffect(()=>{
  if(index<0){last.current=null;root.current?.querySelectorAll('.search-current').forEach(node=>node.classList.remove('search-current'));return}
  const hit=hits[index]
  const owner=[...root.current?.querySelectorAll<HTMLElement>('[data-search-owner]')||[]].find(node=>node.dataset.searchOwner===hit?.id)
  const mark=owner?.querySelectorAll<HTMLElement>('[data-search-hit]')[hit?.occurrence]
  if(mark&&last.current?.node===mark&&last.current.query===query&&last.current.index===index)return
  root.current?.querySelectorAll('.search-current').forEach(node=>node.classList.remove('search-current'))
  if(mark){last.current={node:mark,query,index};mark.classList.add('search-current');mark.scrollIntoView({block:'center',inline:'nearest'})}
 })
 if(!needle)return null
 const jump=(direction:number)=>{const next=index<0?(direction>0?0:hits.length-1):(index+direction+hits.length)%hits.length;setSelection({query,index:next});onReveal(hits[next].id)}
 return <div className="transcript-search-navigation" role="group" aria-label={t('optimization.searchMatches')}><span role="status">{index<0?t('optimization.hitTotal',{count:hits.length}):t('optimization.hitCount',{current:index+1,total:hits.length})}</span><button className="button" disabled={!hits.length} onClick={()=>jump(-1)}>{t('optimization.previousHit')}</button><button className="button" disabled={!hits.length} onClick={()=>jump(1)}>{t('optimization.nextHit')}</button></div>
}
