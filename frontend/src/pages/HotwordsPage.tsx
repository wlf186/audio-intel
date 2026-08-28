import {useState} from 'react'
import {BookA,Pencil,Plus,Trash2} from 'lucide-react'
import {api} from '../lib/api'
import type {HotwordLibraryCapability,HotwordList} from '../lib/types'

function normalizeText(value:string){return value.normalize('NFKC').trim().replace(/\s+/g,' ')}
function parseTerms(value:string){const seen=new Set<string>();return value.split(/[\n,，;；]+/).map(normalizeText).filter(term=>{const key=term.toLocaleLowerCase();if(!term||seen.has(key))return false;seen.add(key);return true})}

export function HotwordsPage({items,limits,refresh}:{items:HotwordList[];limits?:HotwordLibraryCapability;refresh:()=>Promise<void>}){
 const [editing,setEditing]=useState<HotwordList>()
 const [name,setName]=useState('')
 const [terms,setTerms]=useState('')
 const [busy,setBusy]=useState(false)
 const [error,setError]=useState('')
 const open=(item?:HotwordList)=>{setEditing(item);setName(item?.name||'');setTerms(item?.terms.join('\n')||'');setError('')}
 const parsed=parseTerms(terms)
 const normalizedName=normalizeText(name)
 const maxName=limits?.max_name_chars||80
 const maxTerm=limits?.max_term_chars||64
 const maxTerms=limits?.max_terms_per_list||200
 const invalidTerm=parsed.find(term=>term.length>maxTerm||/[\u0000-\u001f\u007f]/.test(term))
 const validationError=!normalizedName||!parsed.length?'请输入场景名称和至少一个热词。':normalizedName.length>maxName?`场景名称不能超过 ${maxName} 个字符。`:invalidTerm?`热词“${invalidTerm.slice(0,18)}${invalidTerm.length>18?'…':''}”无效；单个热词不能超过 ${maxTerm} 个字符，也不能包含控制字符。`:parsed.length>maxTerms?`每个词表最多包含 ${maxTerms} 个热词。`:''
 const save=async()=>{if(validationError){setError(validationError);return}setBusy(true);setError('');try{if(editing)await api.updateHotwordList(editing.id,normalizedName,parsed);else await api.addHotwordList(normalizedName,parsed);await refresh();open()}catch(cause){setError((cause as Error).message)}finally{setBusy(false)}}
 const remove=async(item:HotwordList)=>{if(!confirm(`删除热词表“${item.name}”？已提交任务保留自己的快照。`))return;setBusy(true);setError('');try{await api.removeHotwordList(item.id);await refresh();if(editing?.id===item.id)open()}catch(cause){setError((cause as Error).message)}finally{setBusy(false)}}
 return <div className="page-pad hud-page hotwords-page"><div className="page-heading"><div><h1>热词库</h1><p>按业务场景维护词表，在创建 ASR 任务时按需选择</p></div><span className="online"><BookA size={15}/>LOCAL VOCABULARY</span></div>{error?<p className="error" role="alert">{error}</p>:null}<div className="hotword-layout"><section className="hotword-list"><header><h2>场景词表</h2><button className="button" disabled={busy||Boolean(limits&&items.length>=limits.max_lists)} onClick={()=>open(undefined)}><Plus size={16}/>新建词表</button></header>{items.length?items.map(item=><article key={item.id}><div><b>{item.name}</b><small>{item.term_count} 个词 · {item.terms.slice(0,6).join('、')}{item.term_count>6?'…':''}</small></div><button className="icon-button" aria-label={`编辑 ${item.name}`} onClick={()=>open(item)}><Pencil/></button><button className="icon-button danger" aria-label={`删除 ${item.name}`} onClick={()=>void remove(item)}><Trash2/></button></article>):<div className="empty small"><BookA/><p>还没有词表。可按项目、客户或专业领域创建。</p></div>}</section><section className="hotword-editor"><h2>{editing?'编辑词表':'新建词表'}</h2><label>场景名称<input maxLength={maxName} value={name} placeholder="例如：医疗术语、项目代号" onChange={event=>setName(event.target.value)}/><small>{normalizedName.length} / {maxName} 个字符</small></label><label>热词<textarea value={terms} placeholder="每行一个词，也支持逗号或分号分隔" onChange={event=>setTerms(event.target.value)}/><small>{parsed.length} / {maxTerms} 个；单个最多 {maxTerm} 个字符，重复词会自动合并。</small></label>{validationError&&(name||terms)?<p className="field-error" role="status">{validationError}</p>:null}<div className="section-actions"><button className="primary" disabled={busy||Boolean(validationError)} onClick={()=>void save()}>{busy?'正在保存…':'保存词表'}</button>{editing?<button className="button" onClick={()=>open()}>取消编辑</button>:null}</div></section></div></div>
}
