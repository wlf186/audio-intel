import {useEffect,useState} from 'react'
import {useTranslation} from 'react-i18next'
import {api,size} from '../lib/api'
import type {DocumentImport} from '../lib/types'
import {ConfirmDialog} from './ConfirmDialog'

export function DocumentLibrary({onSelect,onDeleted,revision}:{onSelect:(item:DocumentImport)=>void;onDeleted:(id:string)=>void;revision:number}){
 const {t,i18n}=useTranslation()
 const [page,setPage]=useState(0)
 const [items,setItems]=useState<DocumentImport[]>()
 const [error,setError]=useState('')
 const [retry,setRetry]=useState(0)
 const [removing,setRemoving]=useState<DocumentImport>()
 const [busy,setBusy]=useState(false)
 const [removeError,setRemoveError]=useState('')
 useEffect(()=>{
  let active=true
  setItems(undefined);setError('')
  void api.documentImports(page*20,21).then(value=>{if(active){if(!value.length&&page>0)setPage(p=>p-1);else setItems(value)}}).catch(cause=>{if(active)setError((cause as Error).message)})
  return()=>{active=false}
 },[page,retry,revision])
 const remove=async()=>{
  if(!removing)return
  setBusy(true);setRemoveError('')
  try{await api.removeDocumentImport(removing.id);onDeleted(removing.id);setRemoving(undefined);setRetry(v=>v+1)}catch(cause){setRemoveError((cause as Error).message)}finally{setBusy(false)}
 }
 return <section className="document-library" aria-label={t('document.library')}>
  <h3>{t('document.library')}</h3><p>{t('document.retention')}</p>
  <button type="button" onClick={()=>setRetry(v=>v+1)}>{t('document.refresh')}</button>
  {error?<p role="alert">{error}<button type="button" onClick={()=>setRetry(v=>v+1)}>{t('document.retry')}</button></p>:items===undefined?<p role="status">{t('document.loading')}</p>:items.length===0?<p>{t('document.noImports')}</p>:<>
   <ul>{items.slice(0,20).map(item=><li key={item.id}>
    <div><strong>{item.name}</strong><small>{t(`document.importStates.${item.state}`)} · {new Date(item.created_at).toLocaleString(i18n.language)} · {size(item.storage_bytes,i18n.language)}</small></div>
    <button type="button" onClick={()=>onSelect(item)}>{t('document.useImport')}</button>
    <button type="button" disabled={busy||item.state==='queued'||item.state==='running'} onClick={()=>{setRemoveError('');setRemoving(item)}}>{t('document.remove')}</button>
   </li>)}</ul>
   <div className="document-pagination"><button type="button" disabled={page===0} onClick={()=>setPage(p=>p-1)}>{t('document.previous')}</button><span>{page+1}</span><button type="button" disabled={items.length<=20} onClick={()=>setPage(p=>p+1)}>{t('document.next')}</button></div>
  </>}
  {removing?<ConfirmDialog title={t('document.remove')} description={`${removing.name} — ${t('document.removeDescription')} ${removeError}`} confirmLabel={t('document.remove')} busy={busy} danger onConfirm={()=>void remove()} onClose={()=>setRemoving(undefined)}/>:null}
 </section>
}
