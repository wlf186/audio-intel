import {useEffect,useId,useMemo,useRef,useState} from 'react'
import {useTranslation} from 'react-i18next'
import {api} from '../lib/api'
import {handleTabKeys} from '../lib/tabs'
import {sectionKind,segmentationSummary} from '../lib/documentPresentation'
import type {SubmissionProgress} from '../lib/api'
import {DocumentLibrary} from './DocumentLibrary'
import type {DocumentCapability,DocumentImport,DocumentPreview,DocumentSection,DocumentSelection} from '../lib/types'
import {Modal} from './Modal'
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
 const chosen=useMemo(()=>new Set(selected),[selected])
 const readerText=useRef<HTMLDivElement>(null)
 const [mode,setMode]=useState<'auto'|'length'>(saved.current.segmentation_mode||'auto')
 const [target,setTarget]=useState(saved.current.target_section_chars||capability.default_target_section_chars)
 const [error,setError]=useState('')
 const [uploadError,setUploadError]=useState('')
 const [libraryBusy,setLibraryBusy]=useState(false)
 const [errorPhase,setErrorPhase]=useState<'query'|'parse'|'preview'>('query')
 const pendingFile=useRef<File|undefined>(undefined)
 const uploadController=useRef<AbortController|undefined>(undefined)
 const [progress,setProgress]=useState<SubmissionProgress>()
 const [library,setLibrary]=useState(false)
 const [libraryRevision,setLibraryRevision]=useState(0)
 const [busy,setBusy]=useState(false)
 const [retry,setRetry]=useState(0)
 const [page,setPage]=useState(0)
 const [splitOpen,setSplitOpen]=useState(false)
 const [compact,setCompact]=useState(()=>matchMedia('(max-width:1199px)').matches)
 const allInput=useRef<HTMLInputElement>(null)
 const listRef=useRef<HTMLOListElement>(null)
 const [reading,setReading]=useState<DocumentSection>()
 const [readerMode,setReaderMode]=useState<'original'|'flow'>('original')
 const readerId=useId()
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
   setErrorPhase('preview')
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
  onChange(busy||libraryBusy||error||!preview||!selected.length?undefined:{selected_chars:preview.sections.reduce((n,section)=>n+(chosen.has(section.id)?section.char_count:0),0),importId:id,name:record?.name||preview.title,section_ids:selected,preview_revision:preview.preview_revision,segmentation_mode:preview.segmentation_mode,target_section_chars:preview.target_section_chars})
  if(id&&(!saved.current.importId||saved.current.importId===id||preview)){saved.current=draft;sessionStorage.setItem(storageKey,JSON.stringify(draft))}

 },[id,preview,selected,record,busy,libraryBusy,error,mode,target,onChange,chosen])
 useEffect(()=>{
  let active=true
  setText(undefined);setTextError('')
  if(reading)void api.documentText(id,readStart,Math.min(4000,reading.end-readStart)).then(value=>{if(active)setText(value.text)}).catch(cause=>{if(active)setTextError((cause as Error).message)})
  return()=>{active=false}
 },[reading,id,readStart,textRetry])
 useEffect(()=>{if(readerText.current)readerText.current.scrollTop=0},[text,readerMode])
 useEffect(()=>()=>uploadController.current?.abort(),[])
 const upload=async(file:File)=>{
  if(uploadController.current)return
  pendingFile.current=file;setUploadError('')
  if(file.size>capability.max_upload_bytes){setUploadError(t('document.limits',{formats:capability.formats.join(', '),mb:Math.floor(capability.max_upload_bytes/1048576)}));return}
  setBusy(true);setReading(undefined);setProgress(undefined)
  const controller=new AbortController();uploadController.current=controller
  try{const data=new FormData();data.set('file',file);const value=await api.submitDocumentImport(data,{signal:controller.signal,onProgress:setProgress});epoch.current++;pendingFile.current=undefined;saved.current={importId:value.id};setPreview(undefined);setRecord(undefined);setSelected([]);setId(value.id);setRetry(v=>v+1);setPage(0);setLibraryRevision(v=>v+1)}catch(cause){setUploadError((cause as Error).message)}finally{uploadController.current=undefined;setProgress(undefined);setBusy(false)}
 }
 const split=async()=>{setErrorPhase('preview');setBusy(true);setError('');setReading(undefined);const current=++epoch.current;try{const value=await api.documentPreview(id,mode,target);if(current!==epoch.current)return;setPreview(value);setSelected(value.sections.map(s=>s.id));setPage(0);setSplitOpen(false)}catch(cause){if(epoch.current===current)setError((cause as Error).message)}finally{if(epoch.current===current)setBusy(false)}}

 const forget=()=>{epoch.current++;setId('');setRecord(undefined);setPreview(undefined);setSelected([]);setReading(undefined);setError('');setUploadError('');pendingFile.current=undefined;saved.current={};sessionStorage.removeItem(storageKey)}
 const retryFailed=async()=>{if(errorPhase==='preview'){await split();return}if(errorPhase==='parse'){setBusy(true);try{await api.retryDocumentImport(id);setRetry(v=>v+1);setLibraryRevision(v=>v+1)}catch(cause){setError((cause as Error).message)}finally{setBusy(false)}return}setRetry(v=>v+1)}
 useEffect(()=>{
  const media=matchMedia('(max-width:1199px)')
  const update=()=>{setCompact(media.matches);setPage(0)}
  media.addEventListener('change',update)
  return()=>media.removeEventListener('change',update)
 },[])
 useEffect(()=>{if(allInput.current)allInput.current.indeterminate=selected.length>0&&selected.length<(preview?.sections.length||0)},[selected,preview])
 useEffect(()=>{if(listRef.current)listRef.current.scrollTop=0},[page])
 const pageSize=compact?10:20
 const pages=Math.max(1,Math.ceil((preview?.sections.length||0)/pageSize))
 const selectedChars=preview?.sections.reduce((n,section)=>n+(chosen.has(section.id)?section.char_count:0),0)||0
 const readingIndex=preview?.sections.findIndex(section=>section.id===reading?.id)??-1
 const readSection=(section:DocumentSection)=>{if(!reading)setReaderMode('original');setReading(section);setReadStart(section.start)}
 const summary=useMemo(()=>preview?segmentationSummary(preview):undefined,[preview])
 return <section className="document-input" aria-label={t('document.workspace')}>
  <header className="document-source">
   <div><h2>{record?.name||t('document.upload')}</h2>{record?<p>{t(`document.importStates.${record.state}`)}{preview?` · ${t('document.chars',{count:preview.total_chars})}`:''}</p>:<p>{t('document.limits',{formats:capability.formats.join(', ').toUpperCase(),mb:Math.floor(capability.max_upload_bytes/1048576)})}</p>}</div>
   <div className="document-source-actions">
    <label className="document-upload button">{t('document.upload')}<input type="file" accept={capability.formats.map(f=>'.'+f).join(',')} disabled={busy} onChange={event=>{const file=event.target.files?.[0];event.target.value='';if(file)void upload(file)}}/></label>
    <button type="button" disabled={busy} onClick={()=>setLibrary(true)}>{t('document.library')}</button>
    {id?<button type="button" disabled={busy} onClick={forget}>{t('document.clearSelection')}</button>:null}
   </div>
  </header>
  {library?<Modal title={t('document.library')} closeLabel={t('common.dialog.closeNamed',{title:t('document.library')})} onClose={()=>{if(!libraryBusy)setLibrary(false)}}><DocumentLibrary revision={libraryRevision} currentImportId={id} onBusyChange={setLibraryBusy} onSelect={item=>{if(busy||libraryBusy||item.id===id)return;epoch.current++;setUploadError('');pendingFile.current=undefined;setPreview(undefined);setRecord(undefined);setSelected([]);saved.current={importId:item.id};setId(item.id);setRetry(v=>v+1);setPage(0);setReading(undefined);setLibrary(false)}} onDeleted={removed=>{if(removed===id)forget()}}/></Modal>:null}
  {busy?<p role="status">{t('common.states.processing')}{progress?.percent!==undefined?` · ${progress.percent}%`:''}{progress&&pendingFile.current?<button type="button" onClick={()=>uploadController.current?.abort()}>{t('document.cancelUpload')}</button>:null}</p>:null}
  {uploadError?<div className="document-error" role="alert"><p>{uploadError}</p><button type="button" disabled={busy} onClick={()=>{if(pendingFile.current)void upload(pendingFile.current)}}>{t('document.retry')}</button><button type="button" disabled={busy} onClick={()=>{setUploadError('');pendingFile.current=undefined}}>{t('document.dismissUploadError')}</button></div>:null}
  {error?<div className="document-error" role="alert"><p>{error}</p><button type="button" disabled={busy} onClick={()=>void retryFailed()}>{t('document.retry')}</button><button type="button" disabled={busy} onClick={()=>setLibrary(true)}>{t('document.library')}</button></div>:null}
  {id&&!record&&!error?<p role="status">{t('document.loading')}</p>:null}
  {record?.state==='queued'||record?.state==='running'?<p role="status">{t('document.parsing')}</p>:null}
  {record?.state==='ready'?<section className="document-segmentation">
   <button type="button" className="document-split-toggle" aria-expanded={splitOpen||!!error} aria-controls="document-split-controls" onClick={()=>setSplitOpen(value=>!value)}>{t('document.adjust')} · {t(preview?.segmentation_mode==='length'?'document.length':'document.auto')}</button>
   {summary?<p className="document-split-summary" role="status">{t(`document.segmentationSummary.${summary}`,{count:preview!.target_section_chars})}</p>:null}
   <div id="document-split-controls" hidden={!splitOpen&&!error}>
    <div className="document-controls"><label>{t('document.section')}<select value={mode} disabled={busy} onChange={e=>setMode(e.target.value as 'auto'|'length')}><option value="auto">{t('document.auto')}</option><option value="length">{t('document.length')}</option></select></label><label>{t(mode==='auto'?'document.backupTarget':'document.target')}<input type="number" disabled={busy} aria-describedby="document-split-help" min={capability.min_target_section_chars} max={capability.max_target_section_chars} value={target} onChange={e=>setTarget(Number(e.target.value))}/></label><button type="button" disabled={busy||!Number.isFinite(target)||target<capability.min_target_section_chars||target>capability.max_target_section_chars} onClick={()=>void split()}>{t('document.apply')}</button></div>
    <p id="document-split-help">{t(mode==='auto'?'document.structureHelp':'document.lengthHelp')}</p>
    <small>{t('document.resetNotice')}</small>
   </div>
  </section>:null}
  {preview?<>
   {preview.warnings.length?<details className="document-warnings"><summary>{t('document.warnings',{count:preview.warnings.length})}</summary><ul>{preview.warnings.map((warning,i)=><li key={i}>{warning}</li>)}</ul></details>:null}
   <div className="document-selection-bar"><label className="document-select-all"><input ref={allInput} type="checkbox" checked={selected.length===preview.sections.length} onChange={e=>setSelected(e.target.checked?preview.sections.map(s=>s.id):[])}/>{t('document.all')}</label><p role="status">{t('document.selected',{count:selected.length,chars:selectedChars})}</p></div>
   <ol ref={listRef} className="document-sections" start={page*pageSize+1}>{preview.sections.slice(page*pageSize,page*pageSize+pageSize).map(section=><li key={section.id}><label><input type="checkbox" checked={chosen.has(section.id)} onChange={e=>setSelected(ids=>e.target.checked?[...ids,section.id]:ids.filter(id=>id!==section.id))}/><span><b>{section.index}. {section.title}</b><small className={section.basis==='forced'?'document-forced':undefined}>{t('document.chars',{count:section.char_count})} · {t(`document.sectionKind.${sectionKind(section.basis)}`)} · {sectionKind(section.basis)==='unknown'?section.basis:t(`document.basis.${section.basis}` as 'document.basis.heading')}</small></span></label><button type="button" onClick={()=>readSection(section)}>{t('document.preview')}</button></li>)}</ol>
   <div className="document-pagination"><button type="button" disabled={!page} onClick={()=>setPage(p=>p-1)}>{t('document.previous')}</button><span>{t('document.page',{current:page+1,total:pages})}</span><button type="button" disabled={page+1>=pages} onClick={()=>setPage(p=>p+1)}>{t('document.next')}</button></div>
   {reading?<Modal title={reading.title} closeLabel={t('document.closeReader')} onClose={()=>setReading(undefined)}><section className="document-text-preview">
    <div className="document-reader-navigation"><button type="button" disabled={readingIndex<=0} onClick={()=>readSection(preview.sections[readingIndex-1])}>{t('document.previousSection')}</button><span>{t('document.readerSection',{current:readingIndex+1,total:preview.sections.length})}</span><button type="button" disabled={readingIndex+1>=preview.sections.length} onClick={()=>readSection(preview.sections[readingIndex+1])}>{t('document.nextSection')}</button></div>
    <div className="document-reader-mode" role="tablist" aria-label={t('document.readerMode')} onKeyDown={handleTabKeys}>
     {(['original','flow'] as const).map(mode=><button key={mode} type="button" role="tab" id={`${readerId}-${mode}`} aria-selected={readerMode===mode} aria-controls={`${readerId}-panel`} tabIndex={readerMode===mode?0:-1} onClick={()=>setReaderMode(mode)}>{t(mode==='original'?'document.originalLines':'document.continuousReading')}</button>)}
    </div>
    <p className="document-reader-help">{t(readerMode==='original'?'document.originalHelp':'document.flowHelp')}</p>
    <div className="document-reader-body" ref={readerText} tabIndex={0} role="tabpanel" id={`${readerId}-panel`} aria-labelledby={`${readerId}-${readerMode}`}>
    {textError?<p role="alert">{textError}<button type="button" onClick={()=>setTextRetry(v=>v+1)}>{t('document.retry')}</button></p>:text===undefined?<p role="status">{t('document.loading')}</p>:readerMode==='original'?<pre>{text}</pre>:<div className="document-reader-flow">{text.split(/\n[^\S\n]*\n+/).map((paragraph,index)=><p key={index}>{paragraph}</p>)}</div>}
    </div>
    <div className="document-reader-navigation"><button type="button" disabled={readStart<=reading.start} onClick={()=>setReadStart(n=>Math.max(reading.start,n-4000))}>{t('document.previous')}</button><span>{t('document.readerPage',{current:Math.floor((readStart-reading.start)/4000)+1,total:Math.max(1,Math.ceil((reading.end-reading.start)/4000))})}</span><button type="button" disabled={readStart+4000>=reading.end} onClick={()=>setReadStart(n=>n+4000)}>{t('document.next')}</button></div>
   </section></Modal>:null}
  </>:null}
 </section>
}
