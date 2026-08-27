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
import { publicAsrLanguages } from '../lib/preferences'
import type { AsrModelCapability, ComputeDevice, Job, VoiceprintPerson } from '../lib/types'
import { handleTabKeys } from '../lib/tabs'

type Props = {
  people: VoiceprintPerson[]
  refresh: () => Promise<void>
  onJobSubmitted: (job: Job) => void
  gpuAvailable?: boolean
  asrModels: AsrModelCapability[]
  asrLanguages?: string[]
}
type SampleSource = 'upload' | 'record'

export function VoiceprintsPage({
  people,
  refresh,
  onJobSubmitted,
  gpuAvailable,
  asrModels,
  asrLanguages = publicAsrLanguages,
}: Props) {
  const [selectedId, setSelectedId] = useState('')
  const [newName, setNewName] = useState('')
  const [source, setSource] = useState<SampleSource>('upload')
  const [file, setFile] = useState<File>()
  const [language, setLanguage] = useState('Auto')
  const [computeDevice, setComputeDevice] = useState<ComputeDevice>('gpu')
  const [model, setModel] = useState('qwen3-asr-0.6b')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
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
  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await action()
      await refresh()
      return true
    } catch (cause) {
      setError((cause as Error).message)
      return false
    } finally {
      setBusy(false)
    }
  }
  const create = () =>
    void run(async () => {
      if (!newName.trim()) throw new Error('请输入人员名称。')
      const person = await api.addVoiceprintPerson(newName.trim())
      setSelectedId(person.id)
      setNewName('')
      setNotice('人员已创建。')
    })
  const rename = () => {
    if (!selected) return
    const name = window.prompt('新的人员名称', selected.name)?.trim()
    if (name)
      void run(async () => {
        await api.renameVoiceprintPerson(selected.id, name)
        setNotice('人员名称已更新；历史任务名称保持不变。')
      })
  }
  const removePerson = () => {
    if (
      !selected ||
      !window.confirm(`永久删除 ${selected.name} 及其全部声纹样本？`)
    )
      return
    void run(async () => {
      await api.removeVoiceprintPerson(selected.id)
      setSelectedId('')
      setNotice('人员及其声纹样本已删除。')
    })
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
    if (!selected || !window.confirm('永久删除这个声纹样本？')) return
    void run(async () => {
      await api.removeVoiceprintSample(selected.id, sampleId)
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
          <div className="create-person">
            <input
              value={newName}
              maxLength={80}
              placeholder="新人员名称"
              disabled={busy || microphoneActive}
              onChange={(event) => setNewName(event.target.value)}
            />
            <button
              aria-label="创建声纹人员"
              disabled={busy || microphoneActive || !newName.trim()}
              onClick={create}
            >
              <Plus />
            </button>
          </div>
          {people.length ? (
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
                  <small>{person.sample_count} 个样本</small>
                </span>
              </button>
            ))
          ) : (
            <div className="empty small">
              <Fingerprint />
              <p>
                还没有人员
                <br />
                可在这里创建，或从 ASR 段落加入
              </p>
            </div>
          )}
        </aside>
        <section className="samples-panel">
          {selected ? (
            <>
              <header>
                <div>
                  <h2>{selected.name}</h2>
                  <p>
                    {selected.sample_count} 个独立样本 ·
                    人员改名不会修改历史任务
                  </p>
                </div>
                <button
                  className="icon-button"
                  aria-label={`重命名人员 ${selected.name}`}
                  disabled={busy || microphoneActive}
                  onClick={rename}
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
              <div className="sample-options">
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
                {computeDevice==='gpu'&&effectiveComputeDevice==='cpu'?<small className="device-hint">{modelGpu?.unavailable_reason||'当前模型自动使用 CPU。'}</small>:null}
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
          ) : (
            <div className="empty">
              <Fingerprint />
              <h2>先创建一个人员</h2>
              <p>人员可以拥有多个声纹样本。</p>
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
