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
} from 'lucide-react'
import { useMicrophoneRecorder } from '../hooks/useMicrophoneRecorder'
import { api, formatTime } from '../lib/api'
import {ConfirmDialog} from '../components/ConfirmDialog'
import {Modal} from '../components/Modal'
import {ResourceStatePanel} from '../components/ResourceStatePanel'
import { publicAsrLanguages } from '../lib/preferences'
import type { AsrModelCapability, ComputeDevice, Job, ResourceState, VoiceprintPerson } from '../lib/types'
import { handleTabKeys } from '../lib/tabs'
import { computeUnavailableReason } from '../lib/presentation'

type Props = {
  people: VoiceprintPerson[]
  state: ResourceState
  refresh: () => Promise<void>
  refreshPeopleAndHotwords: () => Promise<void>
  onJobSubmitted: (job: Job) => void
  gpuAvailable?: boolean
  asrModels: AsrModelCapability[]
  asrLanguages?: string[]
}
type SampleSource = 'upload' | 'record'

export function VoiceprintsPage({
  people,
  state,
  refresh,
  refreshPeopleAndHotwords,
  onJobSubmitted,
  gpuAvailable,
  asrModels,
  asrLanguages = publicAsrLanguages,
}: Props) {
  const [selectedId, setSelectedId] = useState('')
  const [source, setSource] = useState<SampleSource>('upload')
  const [file, setFile] = useState<File>()
  const [language, setLanguage] = useState('Auto')
  const [computeDevice, setComputeDevice] = useState<ComputeDevice>('gpu')
  const [model, setModel] = useState('qwen3-asr-0.6b')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [editorMode,setEditorMode]=useState<'create'|'edit'>()
  const [personName,setPersonName]=useState('')
  const [personNote,setPersonNote]=useState('')
  const [includeInHotwordLibrary,setIncludeInHotwordLibrary]=useState(true)
  const [confirmDelete,setConfirmDelete]=useState<'person'|string>()
  const fileRef = useRef<HTMLInputElement>(null)
  const recorder = useMicrophoneRecorder(30)
  const selected = useMemo(
    () => people.find((person) => person.id === selectedId) || people[0],
    [people, selectedId],
  )
  const microphoneActive =
    recorder.phase === 'requesting' || recorder.phase === 'recording'
  const selectedModel=asrModels.find(item=>item.id===model)||asrModels.find(item=>item.default)
  const modelGpu=selectedModel?.compute_devices.find(item=>item.id==='gpu')
  const effectiveComputeDevice:ComputeDevice=computeDevice==='gpu'&&(modelGpu?.available===false||gpuAvailable===false)?'cpu':computeDevice
  useEffect(() => {
    if (selected && !selectedId) setSelectedId(selected.id)
  }, [selected, selectedId])
  useEffect(() => {
    setFile(undefined)
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
    setPersonName('')
    setPersonNote('')
    setIncludeInHotwordLibrary(true)
    setEditorMode('create')
  }
  const openEdit = () => {
    if (!selected) return
    setPersonName(selected.name)
    setPersonNote(selected.note||'')
    setIncludeInHotwordLibrary(selected.include_in_hotword_library)
    setEditorMode('edit')
  }
  const savePerson = () => {
    if (!personName.trim()||personNote.trim().length>20) return
    const editing=editorMode==='edit'&&selected
    void run(async () => {
      const person=editing
        ?await api.updateVoiceprintPerson(editing.id,personName.trim(),personNote.trim()||null,includeInHotwordLibrary)
        :await api.addVoiceprintPerson(personName.trim(),personNote.trim()||null,includeInHotwordLibrary)
      setSelectedId(person.id)
      setEditorMode(undefined)
      setNotice(editing?'人员资料已更新；历史任务标签保持不变。':'人员已创建。')
    },refreshPeopleAndHotwords)
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
      setNotice('人员及其声纹样本已删除。')
    },refreshPeopleAndHotwords)
  }
  const submitSample = async (sampleFile: File, kind: SampleSource) => {
    if (!selected) return
    const personId = selected.id
    const succeeded = await run(async () => {
      const data = new FormData()
      data.set('file', sampleFile)
      data.set('model', model)
      data.set('language', language)
      data.set('compute_device', effectiveComputeDevice)
      const response = await api.uploadVoiceprintSample(personId, data)
      onJobSubmitted(response.job)
      setNotice('已创建“声纹样本入库”ASR 任务，可在任务记录查看进度。')
    })
    if (!succeeded) return
    if (kind === 'record') recorder.discard()
    else {
      setFile(undefined)
      if (fileRef.current) fileRef.current.value = ''
    }
  }
  const startRecording = () => {
    setError('')
    setNotice('')
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
      setNotice('声纹样本已删除。')
    })
  }
  const elapsed = Math.min(
    recorder.maxSeconds,
    Math.max(0, recorder.elapsedSeconds),
  )
  return (
    <div className="voiceprint-page page-pad hud-page">
      <div className="page-heading">
        <div>
          <h1 tabIndex={-1}>声纹库</h1>
          <p>本地保存人员声纹，用于 ASR 自动命名和 TTS 声音克隆</p>
        </div>
        <span className="online">
          <Fingerprint size={15} />
          LOCAL VOICE ID
        </span>
      </div>
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
          <button className="create-person-button" disabled={state!=='ready'||busy||microphoneActive} onClick={openCreate}>
            <Plus size={17}/>新建人员
          </button>
          <ResourceStatePanel state={state} loadingLabel="正在加载声纹人员…" errorLabel="声纹库加载失败。" retry={()=>void refresh()}/>
          {state==='ready'&&people.length ? (
            people.map((person) => (
              <button
                key={person.id}
                className={selected?.id === person.id ? 'active' : ''}
                disabled={busy || microphoneActive}
                onClick={() => setSelectedId(person.id)}
              >
                <Fingerprint />
                <span>
                  <b>{person.name}</b>
                  {person.note?<em>{person.note}</em>:null}
                  <small>{person.sample_count} 个样本</small>
                  <small>{person.include_in_hotword_library?'已加入人名热词':'未加入人名热词'}</small>
                </span>
              </button>
            ))
          ) : null}
        </aside>
        <section className="samples-panel">
          {selected ? (
            <>
              <header>
                <div>
                  <h2>{selected.name}</h2>
                  {selected.note?<p className="person-note">{selected.note}</p>:null}
                  <p>
                    {selected.sample_count} 个独立样本 · {selected.include_in_hotword_library?'同步到“声纹库人名”':'不加入人名热词'} · 人员资料修改不会重写历史任务
                  </p>
                </div>
                <button
                  className="icon-button"
                  aria-label={`编辑人员 ${selected.name}`}
                  disabled={busy || microphoneActive}
                  onClick={openEdit}
                >
                  <Pencil />
                </button>
                <button
                  className="icon-button danger"
                  aria-label={`删除人员 ${selected.name}`}
                  disabled={busy || microphoneActive}
                  onClick={removePerson}
                >
                  <Trash2 />
                </button>
              </header>
              <div
                className="sample-source-tabs"
                role="tablist"
                aria-label="添加声纹样本方式"
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
                  上传文件
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
                  麦克风录音
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
                    onChange={(event) => setFile(event.target.files?.[0])}
                  />
                  <button
                    className="select-like upload"
                    disabled={busy}
                    onClick={() => fileRef.current?.click()}
                  >
                    <Upload size={16} />
                    {file?.name || '选择单人语音样本'}
                  </button>
                  <p>选择干净、仅包含当前人员声音的音频或视频文件。</p>
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
                        <b>正在录音</b>
                        <strong>{formatTime(elapsed, false)} / 00:00:30</strong>
                      </div>
                      <progress
                        max={recorder.maxSeconds}
                        value={elapsed}
                        aria-label="录音进度"
                      />
                      <button className="record-stop" onClick={recorder.stop}>
                        <CircleStop size={17} />
                        停止并试听
                      </button>
                      <button
                        className="button secondary"
                        onClick={recorder.discard}
                      >
                        取消录音
                      </button>
                    </div>
                  ) : recorder.phase === 'requesting' ? (
                    <div className="recorder-requesting" role="status">
                      <Mic2 />
                      <p>正在请求麦克风权限…</p>
                    </div>
                  ) : recorder.recorded ? (
                    <div className="recording-preview">
                      <div>
                        <b>录音完成</b>
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
                          录音不足 5 秒，仍可入库，但建议重录 5–15 秒清晰语音。
                        </p>
                      ) : (
                        <p>录音仅暂存在当前页面，确认后才会发送到本地服务。</p>
                      )}
                      <button
                        className="button secondary"
                        disabled={busy}
                        onClick={startRecording}
                      >
                        <RotateCcw size={15} />
                        重新录制
                      </button>
                    </div>
                  ) : (
                    <div className="recorder-ready">
                      <Mic2 />
                      <div>
                        <b>直接录制单人语音</b>
                        <p>
                          建议录制 5–15 秒，最长 30
                          秒；请保持环境安静并自然说话。
                        </p>
                      </div>
                      <button
                        className="record-start"
                        disabled={busy}
                        onClick={startRecording}
                      >
                        <span aria-hidden="true" />
                        开始录音
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
              <div className="sample-options voiceprint-sample-options">
                <select aria-label="声纹入库 ASR 模型" value={model} disabled={busy||microphoneActive} onChange={event=>setModel(event.target.value)}>
                  {(asrModels.length?asrModels:[{id:'qwen3-asr-0.6b',name:'Qwen3-ASR 0.6B',installed:true} as AsrModelCapability]).map(item=><option key={item.id} value={item.id} disabled={!item.installed}>{item.name}{item.installed?'':'（未安装）'}</option>)}
                </select>
                <select
                  aria-label="声纹样本语言"
                  value={language}
                  disabled={busy || microphoneActive}
                  onChange={(event) => setLanguage(event.target.value)}
                >
                  {asrLanguages.map((item) => (
                    <option key={item} value={item}>
                      {item === 'Auto' ? '自动检测' : item}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="声纹入库计算设备"
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
                {computeDevice==='gpu'&&effectiveComputeDevice==='cpu'?<small className="device-hint">{computeUnavailableReason(modelGpu,'当前模型自动使用 CPU。')}</small>:null}
                <button
                  className="primary"
                  disabled={
                    busy || (source === 'upload' ? !file : !recorder.recorded)
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
                  {source === 'upload' ? '自动转写并入库' : '确认转写并入库'}
                </button>
              </div>
              <div className="sample-list">
                {selected.samples.length ? (
                  selected.samples.map((sample, index) => (
                    <article key={sample.id}>
                      <div className="sample-head">
                        <b>样本 {selected.samples.length - index}</b>
                        <span className={`sample-state ${sample.state}`}>
                          {sample.state === 'ready'
                            ? '可用'
                            : sample.state === 'pending'
                              ? '处理中'
                              : '失败'}
                        </span>
                        <span>
                          {sample.duration
                            ? formatTime(sample.duration)
                            : '等待分析'}
                        </span>
                        <button
                          className="icon-button danger"
                          aria-label={`删除声纹样本 ${sample.id}`}
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
                          '正在自动转写并提取声纹…'}
                      </p>
                      <small>
                        {sample.language} · CAM++{' '}
                        {sample.embedding_status === 'ready'
                          ? '已索引'
                          : sample.embedding_status === 'failed'
                            ? '索引失败'
                            : '将在下次 ASR 时索引'}
                        {sample.duration && sample.duration > 15
                          ? ' · TTS 使用时精确截断至 15 秒以内'
                          : ''}
                      </small>
                    </article>
                  ))
                ) : (
                  <div className="empty small">
                    <Fingerprint />
                    <p>
                      暂无样本
                      <br />
                      上传文件、浏览器录音，或从 ASR 结果选择段落加入
                    </p>
                  </div>
                )}
              </div>
            </>
          ) : state==='ready' ? (
            <div className="empty voiceprint-guide">
              <Fingerprint />
              <h2>先创建一个人员</h2>
              <p>在左侧输入人员名称并点击创建；之后可上传文件、直接录音，或从 ASR 结果加入样本。</p>
            </div>
          ):state==='loading'?<div className="empty voiceprint-guide" role="status"><Fingerprint/><h2>正在准备声纹库</h2><p>加载完成后即可创建人员并管理声纹样本。</p></div>:<div className="empty voiceprint-guide" role="alert"><Fingerprint/><h2>声纹库暂不可用</h2><p>加载失败，请重试后再创建或编辑人员。</p><button className="button" onClick={()=>void refresh()}>重新加载</button></div>}
        </section>
      </div>
      {editorMode?<Modal title={editorMode==='create'?'新建声纹人员':'编辑声纹人员'} closeLabel="关闭人员编辑" onClose={()=>setEditorMode(undefined)}><p>名字会用于声纹匹配标签；备注最多 20 字。后续修改不会重写历史任务。</p><label>名字（必填）<input value={personName} maxLength={80} autoFocus onChange={event=>setPersonName(event.target.value)}/></label><label>备注（选填）<input value={personNote} maxLength={20} placeholder="例如：外号、手机号、公司名称" onChange={event=>setPersonNote(event.target.value)}/><small>{personNote.trim().length} / 20 字</small></label><label className="toggle-label person-hotword-toggle"><input type="checkbox" checked={includeInHotwordLibrary} onChange={event=>setIncludeInHotwordLibrary(event.target.checked)}/><span>加入热词库</span><small>开启后，名字会自动同步到系统词表“声纹库人名”。</small></label><div className="modal-actions"><button className="button" disabled={busy} onClick={()=>setEditorMode(undefined)}>取消</button><button className="primary" disabled={busy||!personName.trim()||personNote.trim().length>20} onClick={savePerson}>{busy?'正在保存…':'保存人员'}</button></div></Modal>:null}
      {confirmDelete&&selected?<ConfirmDialog title={confirmDelete==='person'?'删除人员':'删除声纹样本'} description={confirmDelete==='person'?`永久删除“${selected.name}”及其全部声纹样本？此操作不可恢复。`:'永久删除这个声纹样本？此操作不可恢复。'} confirmLabel="永久删除" danger busy={busy} onClose={()=>setConfirmDelete(undefined)} onConfirm={confirmDelete==='person'?confirmRemovePerson:()=>confirmRemoveSample(confirmDelete)}/>:null}
    </div>
  )
}
