import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CircleStop,
  Fingerprint,
  Mic2,
  Pencil,
  Plus,
  RotateCcw,
  Trash2,
  Upload,
  ArrowLeft,
  Search,
} from 'lucide-react'
import { useMicrophoneRecorder } from '../hooks/useMicrophoneRecorder'
import { api, HttpError, formatTime, isUploadCancelled, uploadLimitMessage, type SubmissionProgress as UploadProgress } from '../lib/api'
import {ConfirmDialog} from '../components/ConfirmDialog'
import {Modal} from '../components/Modal'
import {ResourceStatePanel} from '../components/ResourceStatePanel'
import {VoiceprintGroups,groupLabel,ungroupedId} from '../components/VoiceprintGroups'
import {PeopleIndex} from '../components/PeopleIndex'
import {SubmissionProgress} from '../components/SubmissionProgress'
import { publicAsrLanguages } from '../lib/preferences'
import type { AsrModelCapability, ComputeDevice, Job, ResourceState, VoiceprintGroup, VoiceprintPerson, VoiceprintSample } from '../lib/types'
import { handleTabKeys } from '../lib/tabs'
import { computeUnavailableReason, voiceprintSampleName, cloneReferenceUsage } from '../lib/presentation'
import {useTranslation} from 'react-i18next'
import {resolvedLocale} from '../i18n'
import {useCompactWorkspace} from '../hooks/useCompactWorkspace'
import {checkpointWorkspace,readPeopleSession,savePeopleSession} from '../lib/workspaceSession'
import type {NavigationGuard} from '../lib/navigation'

type Props = {
  detailId?:string
  onOpenPerson:(id:string)=>void
  onBack:()=>void
  registerNavigationGuard:(guard?:NavigationGuard)=>void
  groups: VoiceprintGroup[]
  groupsState: ResourceState
  refreshGroups: () => Promise<void>
  people: VoiceprintPerson[]
  state: ResourceState
  refresh: () => Promise<void>
  refreshPeopleAndHotwords: () => Promise<void>
  onJobSubmitted: (job: Job) => void
  gpuAvailable?: boolean
  defaultComputeDevice: ComputeDevice
  maxCloneReferenceSeconds: number
  maxUploadBytes?: number
  asrModels: AsrModelCapability[]
  asrLanguages?: string[]
}
type SampleSource = 'upload' | 'record'

export function VoiceprintsPage({
  detailId,onOpenPerson,onBack,registerNavigationGuard,
  people,
  groups,
  groupsState,
  refreshGroups,
  state,
  refresh,
  refreshPeopleAndHotwords,
  onJobSubmitted,
  gpuAvailable,
  defaultComputeDevice,
  maxUploadBytes,
  maxCloneReferenceSeconds,
  asrModels,
  asrLanguages = publicAsrLanguages,
}: Props) {
  const {t}=useTranslation()
  const locale=resolvedLocale()
  const compact=useCompactWorkspace()
  const [selectedId, setSelectedId] = useState(()=>detailId||readPeopleSession().selectedId)
  const [groupFilter,setGroupFilter]=useState(()=>readPeopleSession().groupId)
  const [peopleQuery,setPeopleQuery]=useState(()=>readPeopleSession().query)
  const [personGroup,setPersonGroup]=useState(ungroupedId)
  const [savedPerson,setSavedPerson]=useState<VoiceprintPerson>()
  const [addingSample,setAddingSample]=useState(false)
  const [leaveAction,setLeaveAction]=useState<(()=>void)>()
  const detailHeading=useRef<HTMLHeadingElement>(null)
  const selectAll=useRef<HTMLInputElement>(null)
  const [checked,setChecked]=useState<Set<string>>(()=>new Set())
  const [moveGroup,setMoveGroup]=useState(ungroupedId)
  const [confirmBatchDelete,setConfirmBatchDelete]=useState(false)
  const visiblePeople=useMemo(()=>{const query=peopleQuery.normalize('NFKC').trim().toLocaleLowerCase();return people.filter(person=>(!groupFilter||(person.group_id||ungroupedId)===groupFilter)&&(!query||`${person.name} ${person.note||''}`.normalize('NFKC').toLocaleLowerCase().includes(query)))},[people,groupFilter,peopleQuery])
  const changeGroup=(id:string)=>{checkpointWorkspace();setGroupFilter(id);setChecked(new Set());setSelectedId('');savePeopleSession({groupId:id,selectedId:'',scrollTop:0,alphabetScrollTop:0});if(detailId)onBack()}
  useEffect(()=>{if(groupsState==='ready'&&groupFilter&&!groups.some(group=>group.id===groupFilter))changeGroup('')},[groups,groupsState,groupFilter])
  useEffect(()=>{if(state==='ready')setChecked(current=>new Set([...current].filter(id=>people.some(person=>person.id===id))))},[people,state])
  const [source, setSource] = useState<SampleSource>('upload')
  const [file, setFile] = useState<File>()
  const [language, setLanguage] = useState('Auto')
  const [computeDevice, setComputeDevice] = useState<ComputeDevice>(defaultComputeDevice)
  const [model, setModel] = useState('qwen3-asr-0.6b')
  const [busy, setBusy] = useState(false)
  const [openAfterSave,setOpenAfterSave]=useState<string>()
  const [sampleProgress,setSampleProgress]=useState<UploadProgress>()
  const [sampleError,setSampleError]=useState('')
  const [sampleNotice,setSampleNotice]=useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [editorError,setEditorError]=useState('')
  const [renamingSample,setRenamingSample]=useState<VoiceprintSample>()
  const [sampleName,setSampleName]=useState('')
  const [renameError,setRenameError]=useState('')
  const personNameRef=useRef<HTMLInputElement>(null)
  const sampleNameRef=useRef<HTMLInputElement>(null)
  const [editorMode,setEditorMode]=useState<'create'|'edit'>()
  const [personName,setPersonName]=useState('')
  const [personNote,setPersonNote]=useState('')
  const [includeInHotwordLibrary,setIncludeInHotwordLibrary]=useState(true)
  const [confirmDelete,setConfirmDelete]=useState<'person'|string>()
  const fileRef = useRef<HTMLInputElement>(null)
  const sampleUploadController=useRef<AbortController|undefined>(undefined)
  const recorder = useMicrophoneRecorder(30)
  const selected = useMemo(()=>{const id=detailId||selectedId;return id?(people.find(person=>person.id===id)||(savedPerson?.id===id?savedPerson:undefined)):visiblePeople[0]},[people,visiblePeople,selectedId,detailId,savedPerson])
  const microphoneActive =
    recorder.phase === 'requesting' || recorder.phase === 'recording'
  const dirtySample=Boolean(file||recorder.recorded||microphoneActive)
  const discardDraft=()=>{setFile(undefined);if(fileRef.current)fileRef.current.value='';recorder.discard()}
  useEffect(()=>{
    if(!busy&&!dirtySample){registerNavigationGuard(undefined);return}
    registerNavigationGuard((proceed,destination)=>{
      if(busy){setNotice(t('workspaceUx.finishAction'));return}
      if(destination==='#library/voiceprints'&&!microphoneActive){proceed();return}
      setLeaveAction(()=>proceed)
    })
    return()=>registerNavigationGuard(undefined)
  },[busy,dirtySample,microphoneActive,registerNavigationGuard,t])
  useEffect(()=>{if(openAfterSave&&!busy){setOpenAfterSave(undefined);onOpenPerson(openAfterSave)}},[openAfterSave,busy,onOpenPerson])
  useEffect(()=>{if(detailId){setSelectedId(detailId);if(compact)detailHeading.current?.focus({preventScroll:true})}else document.querySelectorAll<HTMLAudioElement>('.samples-panel audio').forEach(audio=>audio.pause())},[detailId,compact,selected?.id])
  useEffect(()=>{savePeopleSession({groupId:groupFilter,query:peopleQuery,selectedId})},[groupFilter,peopleQuery,selectedId])
  useEffect(()=>{if(selectAll.current)selectAll.current.indeterminate=checked.size>0&&!visiblePeople.every(person=>checked.has(person.id))},[checked,visiblePeople])
  useEffect(()=>{if(state==='ready'&&selectedId&&!detailId&&!people.some(person=>person.id===selectedId)&&savedPerson?.id!==selectedId)setSelectedId('')},[people,state,selectedId,detailId,savedPerson])
  const selectedModel=asrModels.find(item=>item.id===model)||asrModels.find(item=>item.default)
  const modelGpu=selectedModel?.compute_devices.find(item=>item.id==='gpu')
  const effectiveComputeDevice:ComputeDevice=computeDevice==='gpu'&&(modelGpu?.available===false||gpuAvailable===false)?'cpu':computeDevice
  const selectedSampleFile=source==='upload'?file:recorder.recorded?.file
  const fileLimitError=selectedSampleFile?uploadLimitMessage(selectedSampleFile,maxUploadBytes,t,locale):''
  const submitLabel=sampleProgress?.phase==='creating'?t('voiceprints.creatingTask'):sampleProgress?.phase==='uploading'&&sampleProgress.percent!==undefined?t('voiceprints.uploadPercent',{percent:sampleProgress.percent}):sampleProgress?t('voiceprints.preparingUpload'):source==='upload'?t('voiceprints.transcribeAndImportAction'):t('voiceprints.confirmAndImportAction')
  useEffect(() => {
    if (selected && !selectedId) setSelectedId(selected.id)
  }, [selected, selectedId])
  useEffect(() => {
    setFile(undefined)
    setSampleProgress(undefined)
    setSampleError('')
    setSampleNotice('')
    if (fileRef.current) fileRef.current.value = ''
    recorder.discard()
  }, [selected?.id, recorder.discard])
  const run = async (action: () => Promise<void>, refreshAction=refresh) => {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await action()
      await refreshAction()
      return true
    } catch (cause) {
      setError((cause as Error).message)
      return false
    } finally {
      setBusy(false)
    }
  }
  const openCreate = () => {
    setEditorError('')
    setPersonName('')
    setPersonNote('')
    setIncludeInHotwordLibrary(true)
    setPersonGroup(groupFilter||ungroupedId)
    setEditorMode('create')
  }
  const openEdit = () => {
    setEditorError('')
    if (!selected) return
    setPersonName(selected.name)
    setPersonNote(selected.note||'')
    setIncludeInHotwordLibrary(selected.include_in_hotword_library)
    setPersonGroup(selected.group_id||ungroupedId)
    setEditorMode('edit')
  }
  const savePerson = async () => {
    if (!personName.trim()||personNote.trim().length>20||groupsState!=='ready'||!groups.some(group=>group.id===personGroup)) return
    const editing=editorMode==='edit'&&selected
    setBusy(true)
    setEditorError('')
    try {
      const person=editing
        ?await api.updateVoiceprintPerson(editing.id,personName.trim(),personNote.trim()||null,includeInHotwordLibrary,personGroup)
        :await api.addVoiceprintPerson(personName.trim(),personNote.trim()||null,includeInHotwordLibrary,personGroup)
      setSavedPerson(person)
      if(groupFilter)setGroupFilter(person.group_id||personGroup)
      if(!editing){setPeopleQuery('');setChecked(new Set());savePeopleSession({scrollTop:0,alphabetScrollTop:0})}
      setSelectedId(person.id)
      setEditorMode(undefined)
      setNotice(t(editing?'voiceprints.personUpdated':'voiceprints.personCreated'))
      await refreshPeopleAndHotwords().catch(cause=>setError((cause as Error).message))
      if(compact)setOpenAfterSave(person.id)
    } catch(cause) {
      setEditorError(cause instanceof HttpError&&cause.status===409?t('voiceprintNames.personConflict'):(cause as Error).message)
      personNameRef.current?.focus()
    } finally { setBusy(false) }
  }
  const saveSampleName = async () => {
    if(!renamingSample)return
    const clean=sampleName.normalize('NFKC').trim().replace(/\s+/g,' ')
    if(!clean||Array.from(clean).length>80||/[\u0000-\u001f\u007f-\u009f]/.test(sampleName)){
      setRenameError(t('voiceprintNames.invalidName'))
      sampleNameRef.current?.focus()
      return
    }
    setBusy(true)
    setRenameError('')
    try {
      await api.renameVoiceprintSample(renamingSample.person_id,renamingSample.id,clean)
      setRenamingSample(undefined)
      setNotice(t('voiceprintNames.sampleRenamed'))
      await refresh().catch(cause=>setError((cause as Error).message))
    } catch(cause) {
      setRenameError(cause instanceof HttpError&&cause.status===409?t('voiceprintNames.sampleConflict'):(cause as Error).message)
      sampleNameRef.current?.focus()
    } finally {setBusy(false)}
  }
  const removePerson = () => {
    if (!selected) return
    setConfirmDelete('person')
  }
  const confirmRemovePerson = () => {
    if(!selected)return
    void run(async () => {
      await api.removeVoiceprintPerson(selected.id)
      setSelectedId('')
      setConfirmDelete(undefined)
      setNotice(t('voiceprints.personDeleted'))
    },refreshPeopleAndHotwords)
  }
  const submitSample = async (sampleFile: File, kind: SampleSource) => {
    if (!selected) return
    const limitError=uploadLimitMessage(sampleFile,maxUploadBytes,t,locale)
    setSampleError('')
    setSampleNotice('')
    if(limitError){setSampleError(limitError);return}
    const personId = selected.id
    setBusy(true)
    setError('')
    setNotice('')
    const controller=new AbortController()
    sampleUploadController.current=controller
    try {
      const data = new FormData()
      data.set('file', sampleFile)
      if(kind==='record')data.set('name',t('voiceprintNames.recordingName',{time:new Date(sampleFile.lastModified).toISOString().replace('T',' ').slice(0,19)+' UTC'}))
      data.set('model', model)
      data.set('language', language)
      data.set('compute_device', effectiveComputeDevice)
      const response = await api.uploadVoiceprintSample(personId, data,{signal:controller.signal,onProgress:setSampleProgress})
      onJobSubmitted(response.job)
      setSampleNotice(t('voiceprints.sampleTaskCreatedNotice'))
      if (kind === 'record') recorder.discard()
      else {
        setFile(undefined)
        if (fileRef.current) fileRef.current.value = ''
      }
      await refresh().catch(cause=>setError(t('voiceprints.refreshAfterCreateFailed',{message:(cause as Error).message})))
    } catch(cause) {
      if(isUploadCancelled(cause))setSampleNotice(t('voiceprints.uploadCancelled'))
      else setSampleError((cause as Error).message)
    } finally {
      sampleUploadController.current=undefined
      setSampleProgress(undefined)
      setBusy(false)
    }
  }
  const chooseSample=(next?:File)=>{
    setFile(next)
    setSampleError('')
    setSampleNotice('')
  }
  const startRecording = () => {
    setError('')
    setNotice('')
    setSampleError('')
    setSampleNotice('')
    void recorder.start()
  }
  const removeSample = (sampleId: string) => {
    if (!selected)return
    setConfirmDelete(sampleId)
  }
  const confirmRemoveSample = (sampleId:string) => {
    if(!selected)return
    void run(async () => {
      await api.removeVoiceprintSample(selected.id, sampleId)
      setConfirmDelete(undefined)
      setNotice(t('voiceprints.sampleDeleted'))
    })
  }
  const moveSelected=()=>void run(async()=>{await api.moveVoiceprintPeople([...checked],moveGroup);setChecked(new Set());setNotice(t('voiceprintGroups.moved'))})
  const deleteSelected=()=>void run(async()=>{
    const result=await api.deleteVoiceprintPeople([...checked])
    const failures=result.items.filter(item=>!item.deleted)
    setChecked(new Set(failures.map(item=>item.person_id)));setConfirmBatchDelete(false)
    setNotice(t('voiceprintGroups.deletedCount',{count:result.items.filter(item=>item.deleted).length}))
    if(failures.length)setError(failures.map(item=>`${people.find(person=>person.id===item.person_id)?.name||item.person_id}: ${item.detail}`).join('; '))
  },refreshPeopleAndHotwords)
  const elapsed = Math.min(
    recorder.maxSeconds,
    Math.max(0, recorder.elapsedSeconds),
  )
  return (
    <div className={`voiceprint-page page-pad hud-page ${detailId?'person-detail-open':'person-list-open'}`}>
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p className="notice" role="status">
          {notice}
        </p>
      ) : null}
      <div className="voiceprint-layout">
        <aside className="people-panel">
          <label className="people-search"><span><Search size={16}/>{t('workspaceUx.searchPeople')}</span><input type="search" value={peopleQuery} disabled={busy||microphoneActive} onChange={event=>{setPeopleQuery(event.target.value);setChecked(new Set());savePeopleSession({query:event.target.value,scrollTop:0,alphabetScrollTop:0})}}/></label>
          <VoiceprintGroups groups={groups} state={groupsState} value={groupFilter} onChange={id=>{if(dirtySample)setLeaveAction(()=>()=>{discardDraft();changeGroup(id)});else changeGroup(id)}} refresh={refreshGroups} onChanged={refresh} disabled={busy||microphoneActive}/>
          <button className="create-person-button" disabled={state!=='ready'||busy||microphoneActive} onClick={openCreate}>
            <Plus size={17}/>{t('voiceprints.newPerson')}
          </button>
          <ResourceStatePanel state={state} loadingLabel={t('voiceprints.loadingPeople')} errorLabel={t('voiceprints.loadFailed')} retry={()=>void refresh().catch(()=>{})}/>
          {state==='ready'&&visiblePeople.length?<div className="voiceprint-batch-controls">
            <label className="batch-select-all"><input ref={selectAll} type="checkbox" checked={visiblePeople.every(person=>checked.has(person.id))} disabled={busy||microphoneActive} onChange={event=>setChecked(event.target.checked?new Set(visiblePeople.map(person=>person.id)):new Set())}/>{t('voiceprintGroups.selectAll')}</label>
            <span>{t('voiceprintGroups.selectedCount',{count:checked.size})}</span>
            {checked.size?<><select aria-label={t('voiceprintGroups.moveTo')} value={moveGroup} disabled={busy||groupsState!=='ready'} onChange={event=>setMoveGroup(event.target.value)}>{groupsState==='ready'?groups.map(group=><option key={group.id} value={group.id}>{groupLabel(group,t('voiceprintGroups.ungrouped'))}</option>):null}</select>
            <button className="button" disabled={busy||microphoneActive||!checked.size||groupsState!=='ready'} onClick={moveSelected}>{t('voiceprintGroups.move')}</button>
            <button className="button danger-action" disabled={busy||microphoneActive||!checked.size} onClick={()=>setConfirmBatchDelete(true)}>{t('voiceprintGroups.deletePeople')}</button></>:null}
          </div>:null}
          {state==='ready'&&visiblePeople.length ? (
            <PeopleIndex people={visiblePeople} positionKey={`${groupFilter}:${peopleQuery}`} shown={!compact||!detailId} scrollTop={readPeopleSession().scrollTop} alphabetScrollTop={readPeopleSession().alphabetScrollTop} onPositionChange={(scrollTop,alphabetScrollTop)=>savePeopleSession({scrollTop,alphabetScrollTop})}>{person => (
              <div className="person-select-row" key={person.id}><label className="person-select-check"><input type="checkbox" aria-label={t('voiceprintGroups.selectPerson',{name:person.name})} checked={checked.has(person.id)} disabled={busy||microphoneActive} onChange={event=>setChecked(current=>{const next=new Set(current);if(event.target.checked)next.add(person.id);else next.delete(person.id);return next})}/></label>
              <button
                key={person.id}
                className={`person-row${selected?.id === person.id ? ' active' : ''}`}
                disabled={busy || microphoneActive}
                onClick={() => {checkpointWorkspace();onOpenPerson(person.id)}}
              >
                <Fingerprint />
                <span>
                  <b>{person.name}</b>
                  {person.note?<em>{person.note}</em>:null}
                  <small>{t('voiceprints.sampleCount',{count:person.sample_count})}</small>
                  <small>{t(person.include_in_hotword_library?'voiceprints.hotwordIncludedLabel':'voiceprints.hotwordExcludedLabel')}</small>
                </span>
              </button></div>
            )}</PeopleIndex>
          ) : state==='ready'?<p className="people-empty" role="status">{t(peopleQuery?'workspaceUx.noPeopleMatches':groupFilter?'workspaceUx.emptyGroup':'workspaceUx.emptyLibrary')}</p>:null}
        </aside>
        <section className="samples-panel">
          {detailId?<button className="button mobile-person-back" onClick={onBack}><ArrowLeft size={16}/>{t('workspaceUx.backPeople')}</button>:null}
          {selected ? (
            <>
              <header>
                <div>
                  <h2 ref={detailHeading} tabIndex={-1}>{selected.name}</h2>
                  {selected.note?<p className="person-note">{selected.note}</p>:null}
                  <p>
                    {t('voiceprints.personSummary',{count:selected.sample_count,hotword:t(selected.include_in_hotword_library?'voiceprints.hotwordSync':'voiceprints.hotwordNoSync')})}
                  </p>
                </div>
                <button
                  className="icon-button"
                  aria-label={t('voiceprints.editPersonNamed',{name:selected.name})}
                  disabled={busy || microphoneActive}
                  onClick={openEdit}
                >
                  <Pencil />
                </button>
                <button
                  className="icon-button danger"
                  aria-label={t('voiceprints.deletePersonNamed',{name:selected.name})}
                  disabled={busy || microphoneActive}
                  onClick={removePerson}
                >
                  <Trash2 />
                </button>
              </header>
              {!visiblePeople.some(person=>person.id===selected.id)?<p role="status">{t('workspaceUx.detailOutsideFilter')}</p>:null}
              <button className="button add-sample-toggle" aria-expanded={addingSample} onClick={()=>setAddingSample(value=>!value)} disabled={busy||microphoneActive}><Plus size={16}/>{t(addingSample?'workspaceUx.closeAddSample':'workspaceUx.addSample')}</button>
              <div className="sample-add-form" hidden={!addingSample}>
              <div
                className="sample-source-tabs"
                role="tablist"
                aria-label={t('voiceprints.addMethod')}
                onKeyDown={handleTabKeys}
              >
                <button
                  id="voiceprint-upload-tab"
                  role="tab"
                  aria-selected={source === 'upload'}
                  aria-controls="voiceprint-upload-panel"
                  tabIndex={source === 'upload' ? 0 : -1}
                  className={source === 'upload' ? 'active' : ''}
                  disabled={busy || microphoneActive}
                  onClick={() => setSource('upload')}
                >
                  <Upload size={15} />
                  {t('voiceprints.uploadFile')}
                </button>
                <button
                  id="voiceprint-record-tab"
                  role="tab"
                  aria-selected={source === 'record'}
                  aria-controls="voiceprint-record-panel"
                  tabIndex={source === 'record' ? 0 : -1}
                  className={source === 'record' ? 'active' : ''}
                  disabled={busy || microphoneActive}
                  onClick={() => setSource('record')}
                >
                  <Mic2 size={15} />
                  {t('voiceprints.microphone')}
                </button>
              </div>
              {source === 'upload' ? (
                <div
                  id="voiceprint-upload-panel"
                  className="sample-input-panel"
                  role="tabpanel"
                  aria-labelledby="voiceprint-upload-tab"
                >
                  <input
                    ref={fileRef}
                    hidden
                    type="file"
                    accept="audio/*,video/*"
                    onChange={(event) => chooseSample(event.target.files?.[0])}
                  />
                  <button
                    className="select-like upload"
                    disabled={busy}
                    onClick={() => fileRef.current?.click()}
                  >
                    <Upload size={16} />
                    {file?.name || t('voiceprints.selectSampleAction')}
                  </button>
                  <p>{t('voiceprints.cleanSampleHelp')}</p>
                </div>
              ) : (
                <div
                  id="voiceprint-record-panel"
                  className="sample-input-panel recorder-panel"
                  role="tabpanel"
                  aria-labelledby="voiceprint-record-tab"
                >
                  {!recorder.supported ? (
                    <div className="recorder-unavailable" role="note">
                      <Mic2 />
                      <p>{recorder.unavailableReason}</p>
                    </div>
                  ) : recorder.phase === 'recording' ? (
                    <div
                      className="recording-live"
                      role="status"
                      aria-live="polite"
                    >
                      <span className="recording-dot" aria-hidden="true" />
                      <div>
                        <b>{t('voiceprints.recording')}</b>
                        <strong>{formatTime(elapsed, false)} / 00:00:30</strong>
                      </div>
                      <progress
                        max={recorder.maxSeconds}
                        value={elapsed}
                        aria-label={t('voiceprints.recordingProgress')}
                      />
                      <button className="record-stop" onClick={recorder.stop}>
                        <CircleStop size={17} />
                        {t('voiceprints.stopAndPreview')}
                      </button>
                      <button
                        className="button secondary"
                        onClick={recorder.discard}
                      >
                        {t('voiceprints.cancelRecording')}
                      </button>
                    </div>
                  ) : recorder.phase === 'requesting' ? (
                    <div className="recorder-requesting" role="status">
                      <Mic2 />
                      <p>{t('voiceprints.requestingMicrophone')}</p>
                    </div>
                  ) : recorder.recorded ? (
                    <div className="recording-preview">
                      <div>
                        <b>{t('voiceprints.recordingComplete')}</b>
                        <span>
                          {formatTime(recorder.recorded.durationSeconds, false)}{' '}
                          · {recorder.recorded.mimeType.split(';')[0]}
                        </span>
                      </div>
                      <audio
                        controls
                        preload="metadata"
                        src={recorder.recorded.url}
                      />
                      {recorder.recorded.durationSeconds < 5 ? (
                        <p className="recording-warning">
                          {t('voiceprints.shortRecordingWarning')}
                        </p>
                      ) : (
                        <p>{t('voiceprints.recordingLocalOnly')}</p>
                      )}
                      <button
                        className="button secondary"
                        disabled={busy}
                        onClick={startRecording}
                      >
                        <RotateCcw size={15} />
                        {t('voiceprints.recordAgain')}
                      </button>
                    </div>
                  ) : (
                    <div className="recorder-ready">
                      <Mic2 />
                      <div>
                        <b>{t('voiceprints.recordDirectly')}</b>
                        <p>{t('voiceprints.recordingAdvice')}</p>
                      </div>
                      <button
                        className="record-start"
                        disabled={busy}
                        onClick={startRecording}
                      >
                        <span aria-hidden="true" />
                        {t('voiceprints.startRecording')}
                      </button>
                      {recorder.error ? (
                        <p className="recording-error" role="alert">
                          {recorder.error}
                        </p>
                      ) : null}
                    </div>
                  )}
                </div>
              )}
              {sampleProgress ? (
                <SubmissionProgress
                  label={t('voiceprints.sampleLabel')}
                  progress={sampleProgress}
                  onCancel={() => sampleUploadController.current?.abort()}
                />
              ) : null}
              {sampleError || fileLimitError ? (
                <div className="submission-feedback" role="alert">
                  <p className="error">{sampleError || fileLimitError}</p>
                  {sampleError && selectedSampleFile ? (
                    <button className="button secondary" disabled={busy} onClick={() => void submitSample(selectedSampleFile,source)}>
                      <RotateCcw size={15}/>{t('voiceprints.uploadAgain')}
                    </button>
                  ) : null}
                </div>
              ) : null}
              {sampleNotice ? (
                <div className="submission-feedback" role="status">
                  <p className="notice">{sampleNotice}</p>
                  {selectedSampleFile && sampleNotice===t('voiceprints.uploadCancelled') ? (
                    <button className="button secondary" disabled={busy} onClick={() => void submitSample(selectedSampleFile,source)}>
                      <RotateCcw size={15}/>{t('voiceprints.uploadAgain')}
                    </button>
                  ) : null}
                </div>
              ) : null}
              <div className="sample-options voiceprint-sample-options">
                <select aria-label={t('voiceprints.asrModelAria')} value={model} disabled={busy||microphoneActive} onChange={event=>setModel(event.target.value)}>
                  {(asrModels.length?asrModels:[{id:'qwen3-asr-0.6b',name:'Qwen3-ASR 0.6B',installed:true} as AsrModelCapability]).map(item=><option key={item.id} value={item.id} disabled={!item.installed}>{item.name}{item.installed?'':t('voiceprints.notInstalled')}</option>)}
                </select>
                <select
                  aria-label={t('voiceprints.sampleLanguageAria')}
                  value={language}
                  disabled={busy || microphoneActive}
                  onChange={(event) => setLanguage(event.target.value)}
                >
                  {asrLanguages.map((item) => (
                    <option key={item} value={item}>
                      {t(`common.languages.${item}` as 'common.languages.Auto',{defaultValue:item})}
                    </option>
                  ))}
                </select>
                <select
                  aria-label={t('voiceprints.computeDeviceAria')}
                  value={effectiveComputeDevice}
                  disabled={busy || microphoneActive}
                  onChange={(event) =>
                    setComputeDevice(event.target.value as ComputeDevice)
                  }
                >
                  <option value="gpu" disabled={gpuAvailable === false||modelGpu?.available===false}>
                    GPU
                  </option>
                  <option value="cpu">CPU</option>
                </select>
                {computeDevice==='gpu'&&effectiveComputeDevice==='cpu'?<small className="device-hint">{computeUnavailableReason(modelGpu,t,t('voiceprints.modelCpuFallback'))}</small>:null}
                <button
                  className="primary"
                  disabled={
                    busy || Boolean(fileLimitError) || !selectedSampleFile
                  }
                  onClick={() =>
                    source === 'upload' && file
                      ? void submitSample(file, 'upload')
                      : recorder.recorded
                        ? void submitSample(recorder.recorded.file, 'record')
                        : undefined
                  }
                >
                  <Mic2 size={16} />
                  {submitLabel}
                </button>
              </div>
              </div>
              <div className="sample-list">
                {selected.samples.length ? (
                  selected.samples.map((sample) => (
                    <article key={sample.id}>
                      <div className="sample-head">
                        <b>{voiceprintSampleName(sample,selected.samples,t)}</b>
                        <span className={`sample-state ${sample.state}`}>
                          {sample.state === 'ready'
                            ? t('voiceprints.sampleStates.ready')
                            : sample.state === 'pending'
                              ? t('voiceprints.sampleStates.pending')
                              : t('voiceprints.sampleStates.failed')}
                        </span>
                        <span>
                          {sample.duration
                            ? formatTime(sample.duration)
                            : t('voiceprints.waitingAnalysis')}
                        </span>
                        <button className="icon-button" disabled={busy} aria-label={t('voiceprintNames.renameNamed',{name:voiceprintSampleName(sample,selected.samples,t)})} onClick={()=>{setRenamingSample(sample);setSampleName(voiceprintSampleName(sample,selected.samples,t));setRenameError('')}}><Pencil/></button>
                        <button
                          className="icon-button danger"
                          aria-label={t('voiceprints.deleteSampleNamed',{id:sample.id})}
                          disabled={sample.state === 'pending'}
                          onClick={() => removeSample(sample.id)}
                        >
                          <Trash2 />
                        </button>
                      </div>
                      {sample.audio_url ? (
                        <audio controls preload="none" src={sample.audio_url} />
                      ) : null}
                      <p>
                        {sample.transcript ||
                          sample.error_message ||
                          t('voiceprints.extracting')}
                      </p>
                      <small>
                        {sample.language} · CAM++{' '}
                        {sample.embedding_status === 'ready'
                          ? t('voiceprints.embedding.ready')
                          : sample.embedding_status === 'failed'
                            ? t('voiceprints.embedding.failed')
                            : t('voiceprints.embedding.pending')}
                        {sample.duration && sample.duration > maxCloneReferenceSeconds
                          ? ` · ${cloneReferenceUsage(sample.duration,maxCloneReferenceSeconds,t)}`
                          : ''}
                      </small>
                    </article>
                  ))
                ) : (
                  <div className="empty small">
                    <Fingerprint />
                    <p>
                      {t('voiceprints.noSamples')}
                      <br />
                      {t('voiceprints.noSamplesHelp')}
                    </p>
                  </div>
                )}
              </div>
            </>
          ) : state==='ready' ? (
            <div className="empty voiceprint-guide">
              <Fingerprint />
              <h2>{t(detailId?'workspaceUx.personMissing':peopleQuery?'workspaceUx.noPeopleMatches':groupFilter?'workspaceUx.emptyGroup':'voiceprints.createFirst')}</h2>
              <p>{t(detailId?'workspaceUx.personMissingHelp':groupFilter?'workspaceUx.emptyGroupHelp':'voiceprints.createFirstHelp')}</p>
            </div>
          ):state==='loading'?<div className="empty voiceprint-guide" role="status"><Fingerprint/><h2>{t('voiceprints.preparingLibrary')}</h2><p>{t('voiceprints.preparingHelp')}</p></div>:<div className="empty voiceprint-guide" role="alert"><Fingerprint/><h2>{t('voiceprints.unavailable')}</h2><p>{t('voiceprints.unavailableHelp')}</p><button className="button" onClick={()=>void refresh().catch(()=>{})}>{t('common.actions.reload')}</button></div>}
        </section>
      </div>
      {confirmBatchDelete?<ConfirmDialog title={t('voiceprintGroups.deletePeople')} description={t('voiceprintGroups.deletePeopleDescription',{count:checked.size})} confirmLabel={t('common.actions.deletePermanently')} danger busy={busy} onClose={()=>{if(!busy)setConfirmBatchDelete(false)}} onConfirm={deleteSelected}/>:null}
      {leaveAction?<ConfirmDialog title={t('workspaceUx.discardSampleTitle')} description={t('workspaceUx.discardSampleHelp')} confirmLabel={t('workspaceUx.discardAndContinue')} danger onClose={()=>setLeaveAction(undefined)} onConfirm={()=>{const proceed=leaveAction;setLeaveAction(undefined);discardDraft();proceed()}}/>:null}
      {editorMode?<Modal title={t(editorMode==='create'?'voiceprints.newPersonDialogTitle':'voiceprints.editPersonTitle')} closeLabel={t('voiceprints.closePersonEditor')} onClose={()=>{if(!busy)setEditorMode(undefined)}}><p>{t('voiceprints.editorHelp')} {t('voiceprintNames.personIdentityHelp')}</p>{editorError?<p className="error" role="alert">{editorError}</p>:null}<ResourceStatePanel state={groupsState} loadingLabel={t('voiceprintGroups.loading')} errorLabel={t('voiceprintGroups.failed')} retry={()=>void refreshGroups().catch(()=>{})}/><label>{t('workspaceUx.personGroup')}<select value={personGroup} disabled={busy||groupsState!=='ready'} onChange={event=>setPersonGroup(event.target.value)}>{groups.map(group=><option key={group.id} value={group.id}>{groupLabel(group,t('voiceprintGroups.ungrouped'))}</option>)}</select></label><label>{t('voiceprints.nameRequiredLabel')}<input ref={personNameRef} aria-invalid={Boolean(editorError)} value={personName} maxLength={80} autoFocus onChange={event=>setPersonName(event.target.value)}/></label><label>{t('voiceprints.noteOptional')}<input value={personNote} maxLength={20} placeholder={t('voiceprints.notePlaceholder')} onChange={event=>setPersonNote(event.target.value)}/><small>{t('voiceprints.noteCount',{current:personNote.trim().length})}</small></label><label className="toggle-label person-hotword-toggle"><input type="checkbox" checked={includeInHotwordLibrary} onChange={event=>setIncludeInHotwordLibrary(event.target.checked)}/><span>{t('voiceprints.addToHotwords')}</span><small>{t('voiceprints.addToHotwordsHelp')}</small></label><div className="modal-actions"><button className="button" disabled={busy} onClick={()=>setEditorMode(undefined)}>{t('common.actions.cancel')}</button><button className="primary" disabled={busy||!personName.trim()||personNote.trim().length>20||groupsState!=='ready'||!groups.some(group=>group.id===personGroup)} onClick={savePerson}>{busy?t('voiceprints.saving'):t('voiceprints.savePerson')}</button></div></Modal>:null}
      {renamingSample?<Modal title={t('voiceprintNames.renameSample')} closeLabel={t('common.actions.cancel')} onClose={()=>{if(!busy)setRenamingSample(undefined)}}>{renameError?<p className="error" role="alert">{renameError}</p>:null}<label>{t('voiceprintNames.sampleName')}<input ref={sampleNameRef} aria-invalid={Boolean(renameError)} autoFocus value={sampleName} maxLength={80} onChange={event=>setSampleName(event.target.value)}/><small>{t('voiceprintNames.nameHelp')}</small></label><div className="modal-actions"><button className="button" disabled={busy} onClick={()=>setRenamingSample(undefined)}>{t('common.actions.cancel')}</button><button className="primary" disabled={busy||!sampleName.trim()} onClick={()=>void saveSampleName()}>{busy?t('voiceprints.saving'):t('voiceprintNames.renameSample')}</button></div></Modal>:null}
      {confirmDelete&&selected?<ConfirmDialog title={t(confirmDelete==='person'?'voiceprints.deletePersonTitle':'voiceprints.deleteSampleTitle')} description={confirmDelete==='person'?t('voiceprints.deletePersonDescription',{name:selected.name}):t('voiceprints.deleteSampleDescription')} confirmLabel={t('common.actions.deletePermanently')} danger busy={busy} onClose={()=>setConfirmDelete(undefined)} onConfirm={confirmDelete==='person'?confirmRemovePerson:()=>confirmRemoveSample(confirmDelete)}/>:null}
    </div>
  )
}
