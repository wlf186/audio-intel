import {useEffect,useRef,useState} from 'react'
import {useTranslation} from 'react-i18next'
import {api} from '../lib/api'
import type {SubmissionProgress} from '../lib/api'
import {DocumentLibrary} from './DocumentLibrary'
import type {DocumentCapability,DocumentImport,DocumentPreview,DocumentSection,DocumentSelection} from '../lib/types'
import {ConfirmDialog} from './ConfirmDialog'
import './document.css'

const storageKey='audio-intel:document-draft'
function restore():Partial<DocumentSelection>{try{return JSON.parse(sessionStorage.getItem(storageKey)||'{}')}catch{return {}}}
export function DocumentInput({capability,onChange}:{capability:DocumentCapability;onChange:(value:DocumentSelection|undefined)=>void}){
 const {t}=useTranslation()
 const [initialDraft]=useState(restore)
 const saved=useRef(initialDraft)
 const [id,setId]=useState(saved.current.importId||'')
 const [record,setRecord]=useState<DocumentImport>()
 const [preview,setPreview]=useState<DocumentPreview>()
 const [selected,setSelected]=useState<string[]>(saved.current.section_ids||[])
 const [mode,setMode]=useState<'auto'|'length'>(saved.current.segmentation_mode||'auto')
 const [target,setTarget]=useState(saved.current.target_section_chars||capability.default_target_section_chars)
 const [error,setError]=useState('')
 const [errorPhase,setErrorPhase]=useState<'upload'|'query'|'parse'|'preview'>('query')
 const pendingFile=useRef<File|undefined>(undefined)
 const uploadController=useRef<AbortController|undefined>(undefined)
 const [progress,setProgress]=useState<SubmissionProgress>()
 const [library,setLibrary]=useState(false)
 const [libraryRevision,setLibraryRevision]=useState(0)
 const [busy,setBusy]=useState(false)
 const [retry,setRetry]=useState(0)
 const [page,setPage]=useState(0)
 const [remove,setRemove]=useState(false)
 const [removeError,setRemoveError]=useState('')
 const [reading,setReading]=useState<DocumentSection>()
 const [readStart,setReadStart]=useState(0)
 const [text,setText]=useState<string>()
 const [textError,setTextError]=useState('')
 const [textRetry,setTextRetry]=useState(0)
 const epoch=useRef(0)
 useEffect(()=>{
  if(!id)return
  const current=++epoch.current
  let stopped=false
  let timer:ReturnType<typeof setTimeout>
  setError('');setErrorPhase('query');setRecord(undefined);setPreview(undefined)
  const poll=async()=>{try{
   const item=await api.documentImport(id)
   if(stopped||epoch.current!==current)return
   setRecord(item)
   if(item.state==='queued'||item.state==='running'){timer=setTimeout(()=>void poll(),1000);return}
   if(item.state==='failed'){setErrorPhase('parse');setError(item.error||t('document.previewError'));return}
   const value=await api.documentPreview(id,saved.current.importId===id?saved.current.segmentation_mode||'auto':'auto',saved.current.importId===id?saved.current.target_section_chars||capability.default_target_section_chars:capability.default_target_section_chars)
   if(stopped||epoch.current!==current)return
   setPreview(value);setMode(value.segmentation_mode);setTarget(value.target_section_chars)
   const valid=new Set(value.sections.map(s=>s.id))
   setSelected(saved.current.importId===id&&saved.current.preview_revision===value.preview_revision?(saved.current.section_ids||[]).filter(s=>valid.has(s)):value.sections.map(s=>s.id))
  }catch(cause){if(!stopped&&epoch.current===current)setError((cause as Error).message)}}
  void poll()
  return()=>{stopped=true;clearTimeout(timer);epoch.current++}
 },[id,retry,capability.default_target_section_chars])
 useEffect(()=>{
  const draft=preview?{version:2,importId:id,name:record?.name||preview.title,preview_revision:preview.preview_revision,segmentation_mode:preview.segmentation_mode,target_section_chars:preview.target_section_chars,section_ids:selected}:{...saved.current,importId:id,section_ids:selected}
  onChange(busy||error||!preview||!selected.length?undefined:{importId:id,name:record?.name||preview.title,section_ids:selected,preview_revision:preview.preview_revision,segmentation_mode:preview.segmentation_mode,target_section_chars:preview.target_section_chars})
  if(id&&(!saved.current.importId||saved.current.importId===id||preview)){saved.current=draft;sessionStorage.setItem(storageKey,JSON.stringify(draft))}

 },[id,preview,selected,record,busy,error,mode,target,onChange])
 useEffect(()=>{
  let active=true
  setText(undefined);setTextError('')
  if(reading)void api.documentText(id,readStart,Math.min(4000,reading.end-readStart)).then(value=>{if(active)setText(value.text)}).catch(cause=>{if(active)setTextError((cause as Error).message)})
  return()=>{active=false}
 },[reading,id,readStart,textRetry])
 useEffect(()=>()=>uploadController.current?.abort(),[])
 const upload=async(file:File)=>{
  pendingFile.current=file;setErrorPhase('upload')
  if(file.size>capability.max_upload_bytes){setError(t('document.limits',{formats:capability.formats.join(', '),mb:Math.floor(capability.max_upload_bytes/1048576)}));return}
  epoch.current++;setBusy(true);setError('');setReading(undefined);setProgress(undefined)
  const controller=new AbortController();uploadController.current=controller
  try{const data=new FormData();data.set('file',file);const value=await api.submitDocumentImport(data,{signal:controller.signal,onProgress:setProgress});pendingFile.current=undefined;saved.current={importId:value.id};setPreview(undefined);setRecord(undefined);setSelected([]);setId(value.id);setRetry(v=>v+1);setPage(0);setLibraryRevision(v=>v+1)}catch(cause){setError((cause as Error).message)}finally{setBusy(false)}
 }
 const split=async()=>{setErrorPhase('preview');setBusy(true);setError('');setReading(undefined);const current=++epoch.current;try{const value=await api.documentPreview(id,mode,target);if(current!==epoch.current)return;setPreview(value);setSelected(value.sections.map(s=>s.id));setPage(0)}catch(cause){if(epoch.current===current)setError((cause as Error).message)}finally{if(epoch.current===current)setBusy(false)}}
 const clear=async()=>{setBusy(true);setRemoveError('');try{await api.removeDocumentImport(id);epoch.current++;setId('');setRecord(undefined);setPreview(undefined);setSelected([]);setReading(undefined);sessionStorage.removeItem(storageKey);setRemove(false);setError('');saved.current={};setLibraryRevision(v=>v+1)}catch(cause){setRemoveError((cause as Error).message)}finally{setBusy(false)}}
 const forget=()=>{epoch.current++;setId('');setRecord(undefined);setPreview(undefined);setSelected([]);setReading(undefined);setError('');saved.current={};sessionStorage.removeItem(storageKey)}
 const retryFailed=async()=>{if(errorPhase==='upload'&&pendingFile.current){await upload(pendingFile.current);return}if(errorPhase==='preview'){await split();return}if(errorPhase==='parse'){setBusy(true);try{await api.retryDocumentImport(id);setRetry(v=>v+1);setLibraryRevision(v=>v+1)}catch(cause){setError((cause as Error).message)}finally{setBusy(false)}return}setRetry(v=>v+1)}
 const chosen=new Set(selected)
 const pages=Math.max(1,Math.ceil((preview?.sections.length||0)/20))
 return <section className="document-input">
  <p>{t('document.limits',{formats:capability.formats.join(', ').toUpperCase(),mb:Math.floor(capability.max_upload_bytes/1048576)})}</p>
  <label className="document-upload">{t('document.upload')}<input type="file" accept={capability.formats.map(f=>'.'+f).join(',')} disabled={busy} onChange={event=>{const file=event.target.files?.[0];event.target.value='';if(file)void upload(file)}}/></label>
  <button type="button" onClick={()=>setLibrary(v=>!v)}>{t('document.library')}</button>
  {library?<DocumentLibrary revision={libraryRevision} onSelect={item=>{if(busy)return;setPreview(undefined);setRecord(undefined);setSelected([]);saved.current={importId:item.id};setId(item.id);setRetry(v=>v+1);setPage(0);setReading(undefined);setLibrary(false)}} onDeleted={removed=>{if(removed===id)forget()}}/>:null}
  {busy?<p role="status">{t('common.states.processing')}{progress?.percent!==undefined?` · ${progress.percent}%`:''}{progress&&pendingFile.current?<button type="button" onClick={()=>uploadController.current?.abort()}>{t('document.cancelUpload')}</button>:null}</p>:null}
  {error?<div role="alert"><p>{error}</p><button type="button" disabled={busy} onClick={()=>void retryFailed()}>{t('document.retry')}</button><button type="button" disabled={busy} onClick={()=>{forget();setLibrary(true)}}>{t('document.clearSelection')}</button></div>:null}
  {id&&!record&&!error?<p role="status">{t('document.loading')}</p>:null}
  {record?<><h3>{record.name}</h3>{record.state==='queued'||record.state==='running'?<p role="status">{t('document.parsing')}</p>:<button type="button" disabled={busy} onClick={()=>{setRemoveError('');setRemove(true)}}>{t('document.remove')}</button>}</>:null}
  {record?.state==='ready'?<>
   <div className="document-controls"><label>{t('document.section')}<select value={mode} disabled={busy} onChange={e=>setMode(e.target.value as 'auto'|'length')}><option value="auto">{t('document.auto')}</option><option value="length">{t('document.length')}</option></select></label><label>{t('document.target')}<input type="number" min={capability.min_target_section_chars} max={capability.max_target_section_chars} value={target} onChange={e=>setTarget(Number(e.target.value))}/></label><button type="button" disabled={busy||target<capability.min_target_section_chars||target>capability.max_target_section_chars} onClick={()=>void split()}>{t('document.apply')}</button></div>
   <small>{t('document.resetNotice')}</small>
  </>:null}
  {preview?<>
   {preview.warnings.length?<details><summary>{preview.warnings.length} ⚠</summary><ul>{preview.warnings.map((warning,i)=><li key={i}>{warning}</li>)}</ul></details>:null}
   <p role="status">{t('document.selected',{count:selected.length,chars:preview.sections.reduce((n,s)=>n+(chosen.has(s.id)?s.char_count:0),0)})}</p>
   <label className="document-select-all"><input type="checkbox" checked={selected.length===preview.sections.length} onChange={e=>setSelected(e.target.checked?preview.sections.map(s=>s.id):[])}/>{t('document.all')}</label>
   <ol className="document-sections" start={page*20+1}>{preview.sections.slice(page*20,page*20+20).map(section=><li key={section.id}><label><input type="checkbox" checked={chosen.has(section.id)} onChange={e=>setSelected(ids=>e.target.checked?[...ids,section.id]:ids.filter(id=>id!==section.id))}/><span>{section.title}<small>{t('document.chars',{count:section.char_count})} · {t(`document.basis.${section.basis}` as 'document.basis.heading')}</small></span></label><button type="button" onClick={()=>{setReading(section);setReadStart(section.start)}}>{t('document.preview')}</button></li>)}</ol>
   <div className="document-pagination"><button type="button" disabled={!page} onClick={()=>setPage(p=>p-1)}>{t('document.previous')}</button><span>{t('document.page',{current:page+1,total:pages})}</span><button type="button" disabled={page+1>=pages} onClick={()=>setPage(p=>p+1)}>{t('document.next')}</button></div>
   {reading?<section className="document-text-preview"><h4>{reading.title}</h4>{textError?<p role="alert">{textError}<button type="button" onClick={()=>setTextRetry(v=>v+1)}>{t('document.retry')}</button></p>:text===undefined?<p role="status">{t('document.loading')}</p>:<pre>{text}</pre>}<button type="button" disabled={readStart<=reading.start} onClick={()=>setReadStart(n=>Math.max(reading.start,n-4000))}>{t('document.previous')}</button><button type="button" disabled={readStart+4000>=reading.end} onClick={()=>setReadStart(n=>n+4000)}>{t('document.readMore')}</button></section>:null}
  </>:null}
  {remove?<ConfirmDialog title={t('document.remove')} description={`${t('document.removeDescription')} ${removeError}`} confirmLabel={t('document.remove')} busy={busy} danger onConfirm={()=>void clear()} onClose={()=>setRemove(false)}/>:null}
 </section>
}
