import {useCallback,useEffect,useRef,useState,type ReactNode} from 'react'
import {Archive,Download,Upload} from 'lucide-react'
import {useTranslation} from 'react-i18next'
import {api,isUploadCancelled,uploadLimitMessage,type SubmissionProgress as Progress} from '../lib/api'
import type {LibraryImport,LibraryMode,LibraryPreview,ResourceState} from '../lib/types'
import {Modal} from '../components/Modal'
import {ConfirmDialog} from '../components/ConfirmDialog'
import {SubmissionProgress} from '../components/SubmissionProgress'
import {ResourceStatePanel} from '../components/ResourceStatePanel'
import {handleTabKeys} from '../lib/tabs'
import {resolvedLocale} from '../i18n'

const pendingKey='audio-intel:library-import:v1'
function pendingId(){try{return sessionStorage.getItem(pendingKey)}catch{return null}}
function setPending(id?:string){try{if(id)sessionStorage.setItem(pendingKey,id);else sessionStorage.removeItem(pendingKey)}catch{/* Monitoring still works without storage. */}}

type Props={tab:'voiceprints'|'hotwords';onTabChange:(tab:'voiceprints'|'hotwords')=>void;maxUploadBytes?:number;onImported:(mode:LibraryMode)=>Promise<void>;children:ReactNode}
export function LibraryPage({tab,onTabChange,maxUploadBytes,onImported,children}:Props){
 const {t}=useTranslation()
 const [open,setOpen]=useState(false)
 const [file,setFile]=useState<File>()
 const [items,setItems]=useState<LibraryImport[]>([])
 const [state,setState]=useState<ResourceState>('loading')
 const [item,setItem]=useState<LibraryImport>()
 const [mode,setMode]=useState<LibraryMode>('merge')
 const [preview,setPreview]=useState<LibraryPreview>()
 const [previewState,setPreviewState]=useState<ResourceState>('loading')
 const [busy,setBusy]=useState(false)
 const [progress,setProgress]=useState<Progress>()
 const [error,setError]=useState('')
 const [notice,setNotice]=useState('')
 const [confirm,setConfirm]=useState(false)
 const controller=useRef<AbortController|undefined>(undefined)
 const frame=useRef<HTMLIFrameElement|undefined>(undefined)
 const onImportedRef=useRef(onImported)
 onImportedRef.current=onImported
 const acknowledged=useRef(new Set<string>())
 const previewGeneration=useRef(0)
 const selectedReceipt=useRef(item)
 selectedReceipt.current=item
 const active=item&&['queued','validating','applying'].includes(item.state)
 const refresh=useCallback(async()=>{
  setState('loading')
  try{
   const next=await api.libraryImports();setItems(next);setState('ready')
   const pending=pendingId()
   const resumed=next.find(row=>row.id===pending)||next.find(row=>['queued','validating','applying','ready'].includes(row.state))
   if(resumed&&!selectedReceipt.current){setItem(resumed);setMode(resumed.mode||'merge')}
  }catch(cause){setState('error');setError((cause as Error).message)}
 },[])
 useEffect(()=>{if(open)void refresh()},[open,refresh])
 useEffect(()=>()=>{previewGeneration.current++;controller.current?.abort();frame.current?.remove()},[])
 useEffect(()=>{
  if(!item||!active)return
  let cancelled=false
  let timeout:ReturnType<typeof setTimeout>
  const poll=async()=>{
   try{const next=await api.libraryImport(item.id);if(cancelled)return;setItem(next);setError('');if(['queued','validating','applying'].includes(next.state))timeout=setTimeout(()=>void poll(),700)}
   catch(cause){if(!cancelled){setError((cause as Error).message);timeout=setTimeout(()=>void poll(),2500)}}
  }
  timeout=setTimeout(()=>void poll(),500)
  return()=>{cancelled=true;clearTimeout(timeout)}
 },[item?.id,active])
 const requestPreview=useCallback(async()=>{
  const generation=++previewGeneration.current
  if(!item||item.state!=='ready')return
  setPreview(undefined);setPreviewState('loading');setError('')
  try{const result=await api.previewLibraryImport(item.id,mode);if(generation!==previewGeneration.current)return;setPreview(result);setPreviewState('ready')}
  catch(cause){if(generation!==previewGeneration.current)return;setPreviewState('error');setError((cause as Error).message)}
 },[item?.id,item?.state,mode])
 useEffect(()=>{void requestPreview()},[requestPreview])
 useEffect(()=>{
  if(item?.state!=='completed'||acknowledged.current.has(item.id)||pendingId()!==item.id)return
  acknowledged.current.add(item.id);setPending();setNotice(t('library.completed'))
  void onImportedRef.current(item.mode||'merge').catch(cause=>setError((cause as Error).message))
 },[item?.id,item?.state,item?.mode,t])
 const download=(url:string)=>{
  setError('');setNotice(t('library.downloadRequested'))
  if(!frame.current){frame.current=document.createElement('iframe');frame.current.hidden=true;frame.current.title=t('library.downloadWindow');document.body.appendChild(frame.current)}
  frame.current.onload=()=>{
   try{
    const doc=frame.current?.contentDocument
    if(!doc||doc.URL==='about:blank'||!doc.body?.textContent)return
    const problem=JSON.parse(doc.body.textContent) as {status?:number;detail?:string}
    if(problem.status===401)window.dispatchEvent(new Event('audio-intel:unauthorized'))
    setNotice('');setError(problem.detail||t('library.downloadFailed'))
   }catch{setNotice('');setError(t('library.downloadFailed'))}
  }
  frame.current.onerror=()=>{setNotice('');setError(t('library.downloadFailed'))}
  frame.current.src=url
 }
 const upload=async()=>{
  if(!file)return
  const limit=uploadLimitMessage(file,maxUploadBytes,t,resolvedLocale())
  if(limit){setError(limit);return}
  setBusy(true);setError('');setPreview(undefined);setNotice('')
  const abort=new AbortController();controller.current=abort
  try{
   const data=new FormData();data.set('file',file)
   const next=await api.submitLibraryImport(data,{signal:abort.signal,onProgress:setProgress});setItem(next);setItems(current=>[next,...current.filter(row=>row.id!==next.id)]);setFile(undefined);setPending(next.id)
  }catch(cause){if(isUploadCancelled(cause))setNotice(t('voiceprints.uploadCancelled'));else setError((cause as Error).message)}
  finally{setBusy(false);setProgress(undefined);controller.current=undefined}
 }
 const apply=async()=>{
  if(!item||!preview||preview.mode!==mode)return
  setBusy(true);setError('')
  try{setPending(item.id);const next=await api.applyLibraryImport(item.id,mode,preview.preview_revision);setItem(next);setConfirm(false)}
  catch(cause){setError((cause as Error).message);setConfirm(false);setPreview(undefined);setPreviewState('error')}
  finally{setBusy(false)}
 }
 const clear=async()=>{
  if(!item)return
  setBusy(true);setError('')
  try{await api.removeLibraryImport(item.id);setItems(current=>current.filter(row=>row.id!==item.id));setItem(undefined);setPreview(undefined);setPending()}
  catch(cause){setError((cause as Error).message)}finally{setBusy(false)}
 }
 return <div className="library-page hud-page">
  <div className="page-heading library-heading"><div><h1 tabIndex={-1}>{t('library.title')}</h1><p>{t('library.subtitle')}</p></div><div className="library-tools"><button className="button" onClick={()=>download('/api/v1/library/export')}><Download size={17}/>{t('library.export')}</button><button className="primary" onClick={()=>setOpen(true)}><Upload size={17}/>{t('library.import')}</button></div></div>
  {error&&!open?<p className="error library-message" role="alert">{error}</p>:null}
  {notice&&!open?<p className="notice library-message" role="status">{notice}</p>:null}
  {active&&!open?<button className="button library-message" onClick={()=>setOpen(true)}>{t(`library.states.${item.state as 'queued'|'validating'|'applying'}`)}</button>:null}
  <div className="library-tabs" role="tablist" aria-label={t('library.sections')} onKeyDown={handleTabKeys}>
   {(['voiceprints','hotwords'] as const).map(value=><button id={`library-tab-${value}`} key={value} role="tab" aria-selected={tab===value} aria-controls="library-panel" tabIndex={tab===value?0:-1} onClick={()=>onTabChange(value)}>{t(value==='voiceprints'?'voiceprints.title':'hotwords.title')}</button>)}
  </div>
  <div className="library-content" id="library-panel" role="tabpanel" aria-labelledby={`library-tab-${tab}`}>{children}</div>
  {open?<Modal title={t('library.importTitle')} closeLabel={t('common.actions.close')} onClose={()=>{if(!busy)setOpen(false)}}>
   <p>{t('library.packageHelp')}</p>
   <ResourceStatePanel state={state} loadingLabel={t('library.loadingImports')} errorLabel={t('library.loadFailed')} retry={()=>void refresh()}/>
   {state==='ready'&&items.length?<label>{t('library.recent')}<select disabled={busy||Boolean(active)} value={item?.id||''} onChange={event=>{const next=items.find(row=>row.id===event.target.value);setItem(next);setMode(next?.mode||'merge');setPreview(undefined);setError('')}}><option value="">{t('library.newImport')}</option>{items.map(row=><option key={row.id} value={row.id}>{row.name} · {t(`library.states.${row.state}`)}</option>)}</select></label>:null}
   {!active?<label>{t('library.archiveFile')}<input type="file" accept=".zip,application/zip" disabled={busy} onChange={event=>{setFile(event.target.files?.[0]);setError('')}}/></label>:null}
   {file?<button className="primary" disabled={busy} onClick={()=>void upload()}><Upload size={16}/>{t('library.upload')}</button>:null}
   {progress?<SubmissionProgress progress={progress} label={t('library.uploading')} onCancel={()=>controller.current?.abort()}/>:null}
   {item?<div className="library-import-status"><Archive size={20}/><b>{item.name}</b><span role="status">{t(`library.states.${item.state}`)}</span></div>:null}
   {item?.state==='ready'?<><fieldset className="library-mode"><legend>{t('library.mode')}</legend>{(['merge','replace'] as const).map(value=><label key={value}><input type="radio" name="library-mode" value={value} checked={mode===value} disabled={busy} onChange={()=>setMode(value)}/><span><b>{t(`library.modes.${value}`)}</b><small>{t(`library.modeHelp.${value}`)}</small></span></label>)}</fieldset>
    <ResourceStatePanel state={previewState} loadingLabel={t('library.previewLoading')} errorLabel={t('library.previewFailed')} retry={()=>void requestPreview()}/>
    {previewState==='ready'&&preview?<><table className="library-preview"><caption>{t('library.preview')}</caption><thead><tr><th>{t('library.contents')}</th><th>{t('library.inPackage')}</th><th>{t('library.existing')}</th><th>{t('library.added')}</th>{mode==='replace'?<th>{t('library.removed')}</th>:null}</tr></thead><tbody>{(['people','samples','hotword_lists'] as const).map(key=><tr key={key}><th>{t(`library.counts.${key}`)}</th><td>{preview.source[key]}</td><td>{preview.target[key]}</td><td>{preview.added[key]}</td>{mode==='replace'?<td>{preview.removed[key]}</td>:null}</tr>)}</tbody></table>
    <p>{t('library.mergeSummary',{people:preview.merged_people,lists:preview.merged_hotword_lists,skipped:preview.skipped_samples,renamed:preview.renamed_samples})}</p>{preview.warnings.length?<ul>{preview.warnings.map((warning,index)=><li key={index}>{warning}</li>)}</ul>:null}</>:null}
    <div className="modal-actions"><button className="button" disabled={busy} onClick={()=>void clear()}>{t('library.discard')}</button><button className={mode==='replace'?'button danger-action':'primary'} disabled={busy||previewState!=='ready'||!preview||preview.mode!==mode} onClick={()=>mode==='replace'?setConfirm(true):void apply()}>{t('library.apply')}</button></div>
   </>:null}
   {item?.backup_url?<button className="button" onClick={()=>download(item.backup_url!)}><Download size={16}/>{t('library.downloadBackup')}</button>:null}
   {item?.state==='completed'?<><p className="notice" role="status">{t('library.completed')}</p>{item.result?<><p>{t('library.resultSummary',{people:item.result.added.people,samples:item.result.added.samples,lists:item.result.added.hotword_lists})}</p><p>{t('library.mergeSummary',{people:item.result.merged_people,lists:item.result.merged_hotword_lists,skipped:item.result.skipped_samples,renamed:item.result.renamed_samples})}</p>{item.result.warnings.length?<ul>{item.result.warnings.map((warning,index)=><li key={index}>{warning}</li>)}</ul>:null}</>:null}</>:null}
   {item?.error?<p className="error" role="alert">{item.error}</p>:null}{error?<p className="error" role="alert">{error}</p>:null}{notice?<p className="notice" role="status">{notice}</p>:null}
   {item?.state==='failed'||item?.state==='expired'?<button className="button" disabled={busy} onClick={()=>void clear()}>{t('library.discard')}</button>:null}
  </Modal>:null}
  {confirm?<ConfirmDialog title={t('library.replaceTitle')} description={t('library.replaceWarning')} confirmLabel={t('library.replaceConfirm')} busy={busy} danger onClose={()=>{if(!busy)setConfirm(false)}} onConfirm={()=>void apply()}/>:null}
 </div>
}
