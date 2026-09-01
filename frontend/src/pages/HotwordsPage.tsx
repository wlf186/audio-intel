import {useEffect,useState} from 'react'
import {BookA,Eye,Pencil,Trash2} from 'lucide-react'
import {api} from '../lib/api'
import type {HotwordLibraryCapability,HotwordList,ResourceState} from '../lib/types'
import {ConfirmDialog} from '../components/ConfirmDialog'
import {ResourceStatePanel} from '../components/ResourceStatePanel'
import {useTranslation} from 'react-i18next'
import {hotwordListDisplayName} from '../lib/presentation'

function normalizeText(value:string){return value.normalize('NFKC').trim().replace(/\s+/g,' ')}
function parseTerms(value:string){const seen=new Set<string>();return value.split(/\r\n|\r|\n/).map(normalizeText).filter(term=>{const key=term.toLocaleLowerCase();if(!term||seen.has(key))return false;seen.add(key);return true})}

const draftKey='audio-intel:hotword-draft:v1'
type Draft={editingId?:string;name:string;terms:string}
function loadDraft():Draft{try{const value=JSON.parse(sessionStorage.getItem(draftKey)||'') as Partial<Draft>;return{editingId:typeof value.editingId==='string'?value.editingId:undefined,name:typeof value.name==='string'?value.name:'',terms:typeof value.terms==='string'?value.terms:''}}catch{return{name:'',terms:''}}}
export function HotwordsPage({items,state,limits,refresh}:{items:HotwordList[];state:ResourceState;limits?:HotwordLibraryCapability;refresh:()=>Promise<void>}){
 const {t}=useTranslation()
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
 const viewingShortSystem=editing?.id==='hotwords_voiceprint_people_short'
 const systemTermHelp=t(viewingShortSystem?'hotwords.shortSystemHelp':'hotwords.fullSystemHelp')
 const systemNote=t(viewingShortSystem?'hotwords.shortSystemNote':'hotwords.fullSystemNote')
 const open=(item?:HotwordList)=>{setEditingId(item?.id);setName(item?.name||'');setTerms(item?.terms.join('\n')||'');setError('');setNotice('');if(!item)sessionStorage.removeItem(draftKey)}
 useEffect(()=>{if(viewingSystem)sessionStorage.removeItem(draftKey);else if(name||terms||editingId)sessionStorage.setItem(draftKey,JSON.stringify({editingId,name,terms}));else sessionStorage.removeItem(draftKey)},[editingId,name,terms,viewingSystem])
 useEffect(()=>{if(state==='ready'&&editingId&&!editing){setEditingId(undefined);setNotice(t('hotwords.missingDraft'))}},[editing,editingId,state,t])
 const parsed=parseTerms(terms)
 const normalizedName=normalizeText(name)
 const maxName=limits?.max_name_chars||80
 const maxTerm=limits?.max_term_chars||64
 const maxTerms=limits?.max_terms_per_list||200
 const customCount=items.filter(item=>item.kind!=='system').length
 const createLimitError=!editing&&limits&&customCount>=limits.max_lists?t('hotwords.validation.listLimit',{count:limits.max_lists}):''
 const invalidTerm=parsed.find(term=>term.length>maxTerm||/[\u0000-\u001f\u007f]/.test(term))
 const validationError=viewingSystem?'':createLimitError||(!normalizedName||!parsed.length?t('hotwords.validation.required'):normalizedName.length>maxName?t('hotwords.validation.nameLength',{count:maxName}):invalidTerm?t('hotwords.validation.invalidTerm',{term:`${invalidTerm.slice(0,18)}${invalidTerm.length>18?'…':''}`,count:maxTerm}):parsed.length>maxTerms?t('hotwords.validation.termLimit',{count:maxTerms}):'')
 const save=async()=>{if(validationError){setError(validationError);return}setBusy(true);setError('');try{if(editing)await api.updateHotwordList(editing.id,normalizedName,parsed);else await api.addHotwordList(normalizedName,parsed);await refresh();open();setNotice(t('hotwords.saved'))}catch(cause){setError((cause as Error).message)}finally{setBusy(false)}}
 const remove=async()=>{const item=pendingDelete;if(!item)return;setBusy(true);setError('');try{await api.removeHotwordList(item.id);await refresh();if(editing?.id===item.id)open();setPendingDelete(undefined);setNotice(t('hotwords.deleted'))}catch(cause){setError((cause as Error).message)}finally{setBusy(false)}}
 return <div className="page-pad hud-page hotwords-page"><div className="page-heading"><div><h1 tabIndex={-1}>{t('hotwords.title')}</h1><p>{t('hotwords.subtitle')}</p></div><span className="online"><BookA size={15}/>LOCAL VOCABULARY</span></div>{error?<p className="error" role="alert">{error}</p>:null}{notice?<p className="notice" role="status">{notice}</p>:null}<div className="hotword-layout"><section className="hotword-list"><header><h2>{t('hotwords.scenarioLists')}</h2></header><ResourceStatePanel state={state} loadingLabel={t('hotwords.loading')} errorLabel={t('hotwords.loadFailed')} retry={()=>void refresh()}/>{state==='ready'&&items.length?items.map(item=>{const itemName=hotwordListDisplayName(item,t);return <article key={item.id}><div><b>{itemName}{item.kind==='system'?<em className="system-badge">{t('hotwords.system')}</em>:null}</b><small>{t('hotwords.termPreview',{count:item.term_count,terms:item.terms.slice(0,6).join(t('hotwords.termSeparator')),more:item.term_count>6?'…':''})}</small></div>{item.kind==='system'?<button className="icon-button" aria-label={t('hotwords.viewNamed',{name:itemName})} onClick={()=>open(item)}><Eye/></button>:<><button className="icon-button" aria-label={t('hotwords.editNamed',{name:itemName})} onClick={()=>open(item)}><Pencil/></button><button className="icon-button danger" aria-label={t('hotwords.deleteNamed',{name:itemName})} onClick={()=>setPendingDelete(item)}><Trash2/></button></>}</article>}):state==='ready'?<div className="empty small"><BookA/><p>{t('hotwords.empty')}</p></div>:null}</section><section className="hotword-editor">{state==='ready'?<><h2>{viewingSystem?t('hotwords.systemList'):editing?t('hotwords.editList'):t('hotwords.newList')}</h2><label>{t('hotwords.name')}<input maxLength={maxName} value={viewingSystem&&editing?hotwordListDisplayName(editing,t):name} disabled={viewingSystem} readOnly={viewingSystem} placeholder={t('hotwords.namePlaceholder')} onChange={event=>setName(event.target.value)}/><small>{t('hotwords.characterCount',{current:normalizedName.length,max:maxName})}</small></label><label>{t('hotwords.terms')}<textarea value={terms} disabled={viewingSystem} readOnly={viewingSystem} placeholder={t('hotwords.termsPlaceholder')} onChange={event=>setTerms(event.target.value)}/><small>{viewingSystem?t('hotwords.systemTermCount',{count:parsed.length,help:systemTermHelp}):t('hotwords.termCountHelp',{current:parsed.length,max:maxTerms})}</small></label>{validationError&&(createLimitError||name||terms)?<p className="field-error" role="status">{validationError}</p>:null}{viewingSystem?<div className="system-hotword-note" role="note">{systemNote} {t('hotwords.systemAdjustment')}</div>:<div className="section-actions"><button className="primary" disabled={busy||Boolean(validationError)} onClick={()=>void save()}>{busy?t('hotwords.saving'):t('hotwords.save')}</button>{editing||name||terms?<button className="button" disabled={busy} onClick={()=>open()}>{t('hotwords.clear')}</button>:null}</div>}</>:<div className={`editor-unavailable ${state}`} role={state==='error'?'alert':'status'}><BookA/><h2>{state==='error'?t('hotwords.editorUnavailable'):t('hotwords.editorPreparing')}</h2><p>{state==='error'?t('hotwords.editorErrorHelp'):t('hotwords.editorLoadingHelp')}</p></div>}</section></div>{pendingDelete?<ConfirmDialog title={t('hotwords.deleteTitle')} description={t('hotwords.deleteDescription',{name:pendingDelete.name})} confirmLabel={t('common.actions.deletePermanently')} danger busy={busy} onClose={()=>setPendingDelete(undefined)} onConfirm={()=>void remove()}/>:null}</div>
}
