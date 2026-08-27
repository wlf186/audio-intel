import {useState} from 'react'
import {BookA,Pencil,Plus,Trash2} from 'lucide-react'
import {api} from '../lib/api'
import type {HotwordLibraryCapability,HotwordList} from '../lib/types'

function parseTerms(value:string){const seen=new Set<string>();return value.split(/[\n,，;；]+/).map(term=>term.normalize('NFKC').trim()).filter(term=>{const key=term.toLocaleLowerCase();if(!term||seen.has(key))return false;seen.add(key);return true})}

export function HotwordsPage({items,limits,refresh}:{items:HotwordList[];limits?:HotwordLibraryCapability;refresh:()=>Promise<void>}){
 const [editing,setEditing]=useState<HotwordList>()
 const [name,setName]=useState('')
 const [terms,setTerms]=useState('')
 const [busy,setBusy]=useState(false)
 const [error,setError]=useState('')
 const open=(item?:HotwordList)=>{setEditing(item);setName(item?.name||'');setTerms(item?.terms.join('\n')||'');setError('')}
 const save=async()=>{const parsed=parseTerms(terms);if(!name.trim()||!parsed.length){setError('请输入场景名称和至少一个热词。');return}setBusy(true);setError('');try{if(editing)await api.updateHotwordList(editing.id,name,parsed);else await api.addHotwordList(name,parsed);await refresh();open()}catch(cause){setError((cause as Error).message)}finally{setBusy(false)}}
 const remove=async(item:HotwordList)=>{if(!confirm(`删除热词表“${item.name}”？已提交任务保留自己的快照。`))return;setBusy(true);setError('');try{await api.removeHotwordList(item.id);await refresh();if(editing?.id===item.id)open()}catch(cause){setError((cause as Error).message)}finally{setBusy(false)}}
 const parsed=parseTerms(terms)
 return <div className="page-pad hud-page hotwords-page"><div className="page-heading"><div><h1>热词库</h1><p>按业务场景维护词表，在创建 ASR 任务时按需选择</p></div><span className="online"><BookA size={15}/>LOCAL VOCABULARY</span></div>{error?<p className="error" role="alert">{error}</p>:null}<div className="hotword-layout"><section className="hotword-list"><header><h2>场景词表</h2><button className="button" disabled={busy||Boolean(limits&&items.length>=limits.max_lists)} onClick={()=>open(undefined)}><Plus size={16}/>新建词表</button></header>{items.length?items.map(item=><article key={item.id}><div><b>{item.name}</b><small>{item.term_count} 个词 · {item.terms.slice(0,6).join('、')}{item.term_count>6?'…':''}</small></div><button className="icon-button" aria-label={`编辑 ${item.name}`} onClick={()=>open(item)}><Pencil/></button><button className="icon-button danger" aria-label={`删除 ${item.name}`} onClick={()=>void remove(item)}><Trash2/></button></article>):<div className="empty small"><BookA/><p>还没有词表。可按项目、客户或专业领域创建。</p></div>}</section><section className="hotword-editor"><h2>{editing?'编辑词表':'新建词表'}</h2><label>场景名称<input maxLength={80} value={name} placeholder="例如：医疗术语、项目代号" onChange={event=>setName(event.target.value)}/></label><label>热词<textarea value={terms} placeholder="每行一个词，也支持逗号或分号分隔" onChange={event=>setTerms(event.target.value)}/><small>{parsed.length} / {limits?.max_terms_per_list||200} 个；重复词会自动合并。</small></label><div className="section-actions"><button className="primary" disabled={busy||!name.trim()||!parsed.length||parsed.length>(limits?.max_terms_per_list||200)} onClick={()=>void save()}>{busy?'正在保存…':'保存词表'}</button>{editing?<button className="button" onClick={()=>open()}>取消编辑</button>:null}</div></section></div></div>
}
