import {useEffect,useState} from 'react'
import {BookA,Eye,Pencil,Trash2} from 'lucide-react'
import {api} from '../lib/api'
import type {HotwordLibraryCapability,HotwordList,ResourceState} from '../lib/types'
import {ConfirmDialog} from '../components/ConfirmDialog'
import {ResourceStatePanel} from '../components/ResourceStatePanel'

function normalizeText(value:string){return value.normalize('NFKC').trim().replace(/\s+/g,' ')}
function parseTerms(value:string){const seen=new Set<string>();return value.split(/\r\n|\r|\n/).map(normalizeText).filter(term=>{const key=term.toLocaleLowerCase();if(!term||seen.has(key))return false;seen.add(key);return true})}

const draftKey='audio-intel:hotword-draft:v1'
type Draft={editingId?:string;name:string;terms:string}
function loadDraft():Draft{try{const value=JSON.parse(sessionStorage.getItem(draftKey)||'') as Partial<Draft>;return{editingId:typeof value.editingId==='string'?value.editingId:undefined,name:typeof value.name==='string'?value.name:'',terms:typeof value.terms==='string'?value.terms:''}}catch{return{name:'',terms:''}}}

export function HotwordsPage({items,state,limits,refresh}:{items:HotwordList[];state:ResourceState;limits?:HotwordLibraryCapability;refresh:()=>Promise<void>}){
 const initial=loadDraft()
 const [editingId,setEditingId]=useState(initial.editingId)
 const [name,setName]=useState(initial.name)
 const [terms,setTerms]=useState(initial.terms)
 const [busy,setBusy]=useState(false)
 const [error,setError]=useState('')
 const [notice,setNotice]=useState('')
 const [pendingDelete,setPendingDelete]=useState<HotwordList>()
 const editing=items.find(item=>item.id===editingId)
 const viewingSystem=editing?.kind==='system'
 const open=(item?:HotwordList)=>{setEditingId(item?.id);setName(item?.name||'');setTerms(item?.terms.join('\n')||'');setError('');setNotice('');if(!item)sessionStorage.removeItem(draftKey)}
 useEffect(()=>{if(viewingSystem)sessionStorage.removeItem(draftKey);else if(name||terms||editingId)sessionStorage.setItem(draftKey,JSON.stringify({editingId,name,terms}));else sessionStorage.removeItem(draftKey)},[editingId,name,terms,viewingSystem])
 useEffect(()=>{if(state==='ready'&&editingId&&!editing){setEditingId(undefined);setNotice('原词表已不存在，已将未保存内容恢复为新词表草稿。')}},[editing,editingId,state])
 const parsed=parseTerms(terms)
 const normalizedName=normalizeText(name)
 const maxName=limits?.max_name_chars||80
 const maxTerm=limits?.max_term_chars||64
 const maxTerms=limits?.max_terms_per_list||200
 const customCount=items.filter(item=>item.kind!=='system').length
 const createLimitError=!editing&&limits&&customCount>=limits.max_lists?`最多只能创建 ${limits.max_lists} 个自定义词表；请删除现有词表后再新建。`:''
 const invalidTerm=parsed.find(term=>term.length>maxTerm||/[\u0000-\u001f\u007f]/.test(term))
 const validationError=viewingSystem?'':createLimitError||(!normalizedName||!parsed.length?'请输入场景名称和至少一个热词。':normalizedName.length>maxName?`场景名称不能超过 ${maxName} 个字符。`:invalidTerm?`热词“${invalidTerm.slice(0,18)}${invalidTerm.length>18?'…':''}”无效；单个热词不能超过 ${maxTerm} 个字符，也不能包含控制字符。`:parsed.length>maxTerms?`每个词表最多包含 ${maxTerms} 个热词。`:'')
 const save=async()=>{if(validationError){setError(validationError);return}setBusy(true);setError('');try{if(editing)await api.updateHotwordList(editing.id,normalizedName,parsed);else await api.addHotwordList(normalizedName,parsed);await refresh();open();setNotice('词表已保存。')}catch(cause){setError((cause as Error).message)}finally{setBusy(false)}}
 const remove=async()=>{const item=pendingDelete;if(!item)return;setBusy(true);setError('');try{await api.removeHotwordList(item.id);await refresh();if(editing?.id===item.id)open();setPendingDelete(undefined);setNotice('词表已删除；历史任务仍保留提交时的热词快照。')}catch(cause){setError((cause as Error).message)}finally{setBusy(false)}}
 return <div className="page-pad hud-page hotwords-page"><div className="page-heading"><div><h1 tabIndex={-1}>热词库</h1><p>按业务场景维护词表，在创建 ASR 任务时按需选择</p></div><span className="online"><BookA size={15}/>LOCAL VOCABULARY</span></div>{error?<p className="error" role="alert">{error}</p>:null}{notice?<p className="notice" role="status">{notice}</p>:null}<div className="hotword-layout"><section className="hotword-list"><header><h2>场景词表</h2></header><ResourceStatePanel state={state} loadingLabel="正在加载场景词表…" errorLabel="场景词表加载失败。" retry={()=>void refresh()}/>{state==='ready'&&items.length?items.map(item=><article key={item.id}><div><b>{item.name}{item.kind==='system'?<em className="system-badge">系统</em>:null}</b><small>{item.term_count} 个词 · {item.terms.slice(0,6).join('、')}{item.term_count>6?'…':''}</small></div>{item.kind==='system'?<button className="icon-button" aria-label={`查看 ${item.name}`} onClick={()=>open(item)}><Eye/></button>:<><button className="icon-button" aria-label={`编辑 ${item.name}`} onClick={()=>open(item)}><Pencil/></button><button className="icon-button danger" aria-label={`删除 ${item.name}`} onClick={()=>setPendingDelete(item)}><Trash2/></button></>}</article>):state==='ready'?<div className="empty small"><BookA/><p>还没有词表。可按项目、客户或专业领域创建。</p></div>:null}</section><section className="hotword-editor"><h2>{viewingSystem?'系统词表':editing?'编辑词表':'新建词表'}</h2><label>场景名称<input maxLength={maxName} value={name} disabled={viewingSystem} readOnly={viewingSystem} placeholder="例如：医疗术语、项目代号" onChange={event=>setName(event.target.value)}/><small>{normalizedName.length} / {maxName} 个字符</small></label><label>热词<textarea value={terms} disabled={viewingSystem} readOnly={viewingSystem} placeholder="每行一个热词，按回车分隔" onChange={event=>setTerms(event.target.value)}/><small>{viewingSystem?parsed.length+' 个；自动同步已开启“加入热词库”的声纹人员名字。':parsed.length+' / '+maxTerms+' 个；每行只保存一个热词，逗号和分号会保留在词内，重复词会自动合并。'}</small></label>{validationError&&(createLimitError||name||terms)?<p className="field-error" role="status">{validationError}</p>:null}{viewingSystem?<div className="system-hotword-note" role="note">此词表由系统维护，不能修改或删除。请在声纹人员资料中使用“加入热词库”开关调整内容。</div>:<div className="section-actions"><button className="primary" disabled={busy||Boolean(validationError)} onClick={()=>void save()}>{busy?'正在保存…':'保存词表'}</button>{editing||name||terms?<button className="button" disabled={busy} onClick={()=>open()}>取消并清空</button>:null}</div>}</section></div>{pendingDelete?<ConfirmDialog title="删除热词表" description={`永久删除“${pendingDelete.name}”？已提交任务仍保留自己的热词快照。`} confirmLabel="永久删除" danger busy={busy} onClose={()=>setPendingDelete(undefined)} onConfirm={()=>void remove()}/>:null}</div>
}
