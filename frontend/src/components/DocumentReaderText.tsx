import {Fragment,useMemo} from 'react'
import {useTranslation} from 'react-i18next'
import type {DocumentText,NarrationOptions} from '../lib/types'

export function DocumentReaderText({data,flow,options}:{data:DocumentText;flow:boolean;options?:NarrationOptions|null}){
 const {t}=useTranslation()
 const paragraphs=useMemo(()=>{
  const chars=Array.from(data.text),end=data.start+chars.length
  const annotations=data.annotations||[]
  const breaks=flow&&data.analysis_revision&&data.boundaries?data.boundaries.map(b=>b.offset):flow?Array.from(data.text.matchAll(/\n[^\S\n]*\n+/g),m=>data.start+Array.from(data.text.slice(0,m.index!+m[0].length)).length):[]
  const boundaries=[data.start,...breaks.filter(at=>at>data.start&&at<end),end].sort((a,b)=>a-b)
  return boundaries.slice(0,-1).map((start,index)=>{
   const finish=boundaries[index+1],visible=annotations.filter(a=>a.end>start&&a.start<finish)
   const points=[...new Set([start,finish,...visible.flatMap(a=>[Math.max(start,a.start),Math.min(finish,a.end)])])].sort((a,b)=>a-b)
   const parts=points.slice(0,-1).map((at,i)=>{
    const covering=visible.filter(a=>a.start<=at&&a.end>=points[i+1])
    // A nested citation belongs in the note area, but every covering kind affects speech.
    const annotation=covering.find(a=>a.kind==='note_body')||covering[0]
    const skipped=!!options?.skip_enabled&&covering.some(a=>options.skip_kinds.includes(a.kind))
    let text=chars.slice(at-data.start,points[i+1]-data.start).join('')
    if(flow&&data.source_format==='pdf'&&data.analysis_revision){text=text.replace(/(?<=[\u3400-\u9fff])\s*\n\s*(?=[\u3400-\u9fff])/g,'').replace(/\s*\n\s*/g,' ')}
    if(flow)text=text.replace(/\n[^\S\n]*\n+$/,'')
    return {text,annotation,skipped,at}
   })
   if(flow&&data.source_format==='pdf'){
    const spoken=parts.filter(p=>p.annotation?.kind!=='page_number'&&p.annotation?.kind!=='note_body')
    for(let i=1;i<spoken.length;i++){
     if(/[\u3400-\u9fff]\s*$/.test(spoken[i-1].text)&&/^\s*[\u3400-\u9fff]/.test(spoken[i].text)){
      spoken[i-1].text=spoken[i-1].text.trimEnd();spoken[i].text=spoken[i].text.trimStart()
     }
    }
   }
   return parts
  })
 },[data,flow,options])
 const render=(part:typeof paragraphs[number][number])=>part.annotation?<span key={part.at} className={`document-annotation document-annotation-${part.annotation.kind}${part.skipped?' is-skipped':''}`} title={t(part.skipped?'document.narration.skipped':'document.narration.retained')}>{part.text}</span>:<Fragment key={part.at}>{part.text}</Fragment>
 if(!flow)return <pre>{paragraphs.flat().map(render)}</pre>
 return <div className="document-reader-flow">{paragraphs.map((parts,index)=><section className="document-flow-paragraph" key={index}>
  <p>{parts.filter(p=>p.annotation?.kind!=='page_number'&&p.annotation?.kind!=='note_body').map(render)}</p>
  {parts.some(p=>p.annotation?.kind==='page_number'||p.annotation?.kind==='note_body')?<aside className="document-reader-notes">{parts.filter(p=>p.annotation?.kind==='page_number'||p.annotation?.kind==='note_body').map(p=><span key={p.at}>{p.annotation?.kind==='page_number'?t('document.narration.page')+' ':null}{render(p)} </span>)}</aside>:null}
 </section>)}</div>
}
