import {useEffect,useRef,useState} from 'react'
import {useTranslation} from 'react-i18next'
import {api,HttpError,size} from '../lib/api'
import type {DocumentImport} from '../lib/types'
import {Modal} from './Modal'

const removable=(item:DocumentImport)=>item.state==='ready'||item.state==='failed'
type RemovalResult={removed:number;missing:number;failures:{item:DocumentImport;message:string}[]}
type Props={onSelect:(item:DocumentImport)=>void;onDeleted:(id:string)=>void;revision:number;currentImportId:string;onBusyChange:(busy:boolean)=>void}

export function DocumentLibrary({onSelect,onDeleted,revision,currentImportId,onBusyChange}:Props){
 const {t,i18n}=useTranslation()
 const [page,setPage]=useState(0)
 const [items,setItems]=useState<DocumentImport[]>()
 const [error,setError]=useState('')
 const [retry,setRetry]=useState(0)
 const [selected,setSelected]=useState(new Map<string,DocumentImport>())
 const [removing,setRemoving]=useState<DocumentImport[]>()
 const [busy,setBusy]=useState(false)
 const [processed,setProcessed]=useState(0)
 const [result,setResult]=useState<RemovalResult>()
 const [excluded,setExcluded]=useState<string[]>([])
 const alive=useRef(true)
 const running=useRef(false)
 const stopped=useRef(false)
 const selection=useRef(selected)
 selection.current=selected
 const allInput=useRef<HTMLInputElement>(null)
 const visible=items?.slice(0,20)||[]
 const eligible=visible.filter(removable)
 const checked=eligible.filter(item=>selected.has(item.id)).length
 useEffect(()=>{
  alive.current=true;stopped.current=false
  const stop=()=>{stopped.current=true}
  window.addEventListener('audio-intel:unauthorized',stop)
  return()=>{alive.current=false;stopped.current=true;window.removeEventListener('audio-intel:unauthorized',stop)}
 },[])
 useEffect(()=>{
  let active=true
  setItems(undefined);setError('')
  void api.documentImports(page*20,21).then(value=>{
   if(!active)return
   const unavailable=value.filter(item=>!removable(item)&&selection.current.has(item.id))
   if(unavailable.length){
    setExcluded(unavailable.map(item=>item.name))
    setSelected(previous=>{const next=new Map(previous);for(const item of unavailable)next.delete(item.id);return next})
   }
   if(!value.length&&page>0)setPage(p=>p-1)
   else setItems(value)
  }).catch(cause=>{if(active)setError((cause as Error).message)})
  return()=>{active=false}
 },[page,retry,revision])
 useEffect(()=>{if(allInput.current)allInput.current.indeterminate=checked>0&&checked<eligible.length},[checked,eligible.length])
 const toggle=(item:DocumentImport,checked:boolean)=>setSelected(previous=>{const next=new Map(previous);if(checked)next.set(item.id,item);else next.delete(item.id);return next})
 const confirm=(documents:DocumentImport[])=>{if(!running.current&&documents.length){setRemoving(documents);setResult(undefined);setProcessed(0)}}
 const remove=async()=>{
  if(!removing?.length||running.current||stopped.current)return
  running.current=true;setBusy(true);onBusyChange(true)
  const outcome:RemovalResult={removed:0,missing:0,failures:[]}
  try{
   for(const item of removing){
    if(!alive.current||stopped.current)break
    let gone=false;let becameActive=false
    try{await api.removeDocumentImport(item.id);outcome.removed++;gone=true}
    catch(cause){
     if(cause instanceof HttpError&&cause.status===404){outcome.missing++;gone=true}
     else{
      outcome.failures.push({item,message:(cause as Error).message})
      if(cause instanceof HttpError&&cause.status===401)stopped.current=true
      if(cause instanceof HttpError&&cause.status===409&&alive.current&&!stopped.current){
       try{becameActive=!removable(await api.documentImport(item.id))}catch{/* Keep the removal failure available for retry. */}
      }
     }
    }
    if(!alive.current)break
    if(gone){onDeleted(item.id);setSelected(previous=>{const next=new Map(previous);next.delete(item.id);return next})}
    else if(becameActive){
     setSelected(previous=>{const next=new Map(previous);next.delete(item.id);return next})
     setExcluded(previous=>[...new Set([...previous,item.name])])
    }else setSelected(previous=>new Map(previous).set(item.id,item))
    setProcessed(value=>value+1)
   }
   if(alive.current){setResult(outcome);setRemoving(undefined);setRetry(value=>value+1)}
  }finally{
   running.current=false
   if(alive.current){setBusy(false);onBusyChange(false)}
  }
 }
 return <section className="document-library" aria-label={t('document.library')}>
  <p>{t('document.retention')}</p>
  <div className="document-library-actions">
   <button type="button" disabled={busy} onClick={()=>setRetry(value=>value+1)}>{t('document.refresh')}</button>
   <span role="status">{t('document.importsSelected',{count:selected.size})}</span>
   <button type="button" disabled={busy||!selected.size} onClick={()=>setSelected(new Map())}>{t('document.clearImportsSelection')}</button>
   <button type="button" className="danger-action" disabled={busy||!selected.size} onClick={()=>confirm([...selected.values()])}>{t('document.removeSelected')}</button>
  </div>
  {excluded.length?<p role="status">{t('document.importsBecameActive',{names:excluded.join('、')})}</p>:null}
  {result?<div className="document-removal-result" role={result.failures.length?'alert':'status'}>
   <p>{t('document.removalResult',{removed:result.removed,missing:result.missing,failed:result.failures.length})}</p>
   {result.failures.length?<><ul>{result.failures.map(({item,message})=><li key={item.id}>{item.name} — {message}</li>)}</ul><p>{t('document.retryRemovalHelp')}</p></>:null}
  </div>:null}
  {error?<p role="alert">{error}<button type="button" onClick={()=>setRetry(value=>value+1)}>{t('document.retry')}</button></p>:items===undefined?<p role="status">{t('document.loading')}</p>:items.length===0?<p>{t('document.noImports')}</p>:<>
   <label className="document-import-check document-import-all"><input ref={allInput} type="checkbox" disabled={busy||!eligible.length} checked={eligible.length>0&&checked===eligible.length} onChange={event=>{const checked=event.target.checked;setSelected(previous=>{const next=new Map(previous);for(const item of eligible){if(checked)next.set(item.id,item);else next.delete(item.id)}return next})}}/>{t('document.selectPageImports')}</label>
   <ul className="document-import-list">{visible.map(item=><li key={item.id}>
    <label className="document-import-check"><input type="checkbox" aria-label={t('document.selectImport',{name:item.name})} disabled={busy||!removable(item)} checked={selected.has(item.id)} onChange={event=>toggle(item,event.target.checked)}/></label>
    <div><strong>{item.name}</strong><small>{t(`document.importStates.${item.state}`)} · {new Date(item.created_at).toLocaleString(i18n.language)} · {size(item.storage_bytes,i18n.language)}</small>{!removable(item)?<small>{t('document.activeImportHelp')}</small>:null}</div>
    <button type="button" disabled={busy||item.id===currentImportId} onClick={()=>onSelect(item)}>{t(item.id===currentImportId?'document.currentImport':'document.useImport')}</button>
    <button type="button" disabled={busy||!removable(item)} onClick={()=>confirm([item])}>{t('document.remove')}</button>
   </li>)}</ul>
   <div className="document-pagination"><button type="button" disabled={busy||page===0} onClick={()=>setPage(value=>value-1)}>{t('document.previous')}</button><span>{page+1}</span><button type="button" disabled={busy||items.length<=20} onClick={()=>setPage(value=>value+1)}>{t('document.next')}</button></div>
  </>}
  {removing?<Modal title={t('document.removeSelected')} closeLabel={t('common.dialog.closeNamed',{title:t('document.removeSelected')})} onClose={()=>{if(!running.current)setRemoving(undefined)}}>
   <p>{t('document.confirmRemoval',{count:removing.length})} {t('document.removeDescription')}</p>
   {removing.some(item=>item.id===currentImportId)?<p>{t('document.removingCurrent')}</p>:null}
   <ul className="document-removal-names">{removing.map(item=><li key={item.id}>{item.name}</li>)}</ul>
   {busy?<p role="status">{t('document.removalProgress',{current:processed,total:removing.length})}</p>:null}
   <div className="modal-actions"><button type="button" disabled={busy} onClick={()=>setRemoving(undefined)}>{t('common.actions.cancel')}</button><button type="button" className="button danger-action" disabled={busy} onClick={()=>void remove()}>{t(busy?'common.states.processing':'document.removeSelected')}</button></div>
  </Modal>:null}
 </section>
}
