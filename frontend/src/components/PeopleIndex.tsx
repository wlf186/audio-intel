import {useMemo,useRef,useState,type ReactNode} from 'react'
import {useTranslation} from 'react-i18next'
import type {VoiceprintPerson} from '../lib/types'

const alphabet=[...'ABCDEFGHIJKLMNOPQRSTUVWXYZ','#']

export function PeopleIndex({people,children}:{people:VoiceprintPerson[];children:(person:VoiceprintPerson)=>ReactNode}){
 const {t}=useTranslation()
 const list=useRef<HTMLDivElement>(null)
 const headings=useRef(new Map<string,HTMLElement>())
 const [active,setActive]=useState('')
 const groups=useMemo(()=>{
  const grouped=new Map<string,VoiceprintPerson[]>()
  for(const person of people){
   const initial=person.sort_initial||(/^[a-z]/i.test(person.name)?person.name[0].toUpperCase():'#')
   grouped.set(initial,[...(grouped.get(initial)||[]),person])
  }
  return alphabet.filter(letter=>grouped.has(letter)).map(letter=>({letter,people:grouped.get(letter)!}))
 },[people])
 const jump=(letter:string)=>{
  const heading=headings.current.get(letter)
  if(!heading||!list.current)return
  list.current.scrollTop+=heading.getBoundingClientRect().top-list.current.getBoundingClientRect().top
  setActive(letter)
  heading.focus({preventScroll:true})
 }
 const track=()=>{
  if(!list.current)return
  if(list.current.scrollTop>0&&list.current.scrollTop+list.current.clientHeight>=list.current.scrollHeight-2){
   setActive(groups.at(-1)?.letter||'')
   return
  }
  const top=list.current.getBoundingClientRect().top
  const visible=groups.filter(group=>{const heading=headings.current.get(group.letter);return heading&&heading.getBoundingClientRect().top<=top+2}).at(-1)
  setActive(visible?.letter||groups[0]?.letter||'')
 }
 return <div className="people-index">
  <div className="people-list" ref={list} onScroll={track}>
   {groups.map(group=><section key={group.letter} aria-labelledby={`people-group-${group.letter}`}>
    <h3 id={`people-group-${group.letter}`} tabIndex={-1} ref={node=>{if(node)headings.current.set(group.letter,node);else headings.current.delete(group.letter)}}>{group.letter}</h3>
    {group.people.map(person=>children(person))}
   </section>)}
  </div>
  <nav className="people-alphabet" aria-label={t('library.alphabet')} onKeyDown={event=>{
   if(!['ArrowUp','ArrowDown','Home','End'].includes(event.key))return
   const buttons=[...event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')]
   const current=buttons.indexOf(document.activeElement as HTMLButtonElement)
   const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:event.key==='ArrowDown'?Math.min(current+1,buttons.length-1):Math.max(current-1,0)
   event.preventDefault();buttons[next]?.focus()
  }}>
   {alphabet.map(letter=><button type="button" key={letter} aria-label={t('library.jumpLetter',{letter})} aria-current={active===letter?'location':undefined} disabled={!groups.some(group=>group.letter===letter)} onClick={()=>jump(letter)}>{letter}</button>)}
  </nav>
 </div>
}
