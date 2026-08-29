import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CircleStop,
  Download,
  Fingerprint,
  Mic2,
  RefreshCw,
  RotateCcw,
  Sparkles,
  Trash2,
  Upload,
} from 'lucide-react'
import { api, artifactUrl, formatTime } from '../lib/api'
import type {
  ComputeDevice,
  AsrModelCapability,
  Job,
  JobDetailResource,
  JobSummary,
  ResultRevealRequest,
  TtsModelCapability,
  VoiceprintPerson,
} from '../lib/types'
import { JobMini } from '../components/JobMini'
import { AudioTransport } from '../components/AudioTransport'
import { InfoTooltip } from '../components/InfoTooltip'
import { useMicrophoneRecorder } from '../hooks/useMicrophoneRecorder'
import {
  clearTtsPreferences,
  defaultTtsPreferences,
  loadTtsContent,
  loadTtsPreferences,
  saveTtsContent,
  saveTtsPreferences,
  type TtsContent,
  type TtsPreferences,
} from '../lib/preferences'
import { progressPresentation, visibleWorkspaceJobs } from '../lib/jobs'
import { handleTabKeys } from '../lib/tabs'
import { computeUnavailableReason } from '../lib/presentation'

type Props = {
  jobs: JobSummary[]
  jobDetails: Record<string, JobDetailResource>
  loadJobDetail: (job: JobSummary, force?: boolean) => void
  onJobSubmitted: (job: Job) => void
  selectedJobId?: string
  onSelect: (job: JobSummary) => void
  gpuAvailable?: boolean
  voiceprints: VoiceprintPerson[]
  asrModels: AsrModelCapability[]
  ttsModels: TtsModelCapability[]
  ttsLanguages?: string[]
  referenceLanguages?: string[]
  revealRequest?: ResultRevealRequest
  onRevealHandled: (token: number) => void
}
type ReferenceSource = 'upload' | 'record'

const fallbackTtsLanguages = [
  'Auto',
  'Chinese',
  'English',
  'Japanese',
  'Korean',
  'German',
  'French',
  'Russian',
  'Portuguese',
  'Spanish',
  'Italian',
]
const fallbackReferenceLanguages = [
  'Auto',
  'Chinese',
  'English',
  'Cantonese',
  'Japanese',
  'Korean',
  'German',
  'French',
  'Russian',
  'Portuguese',
  'Spanish',
  'Italian',
]
const fallbackTtsModels: TtsModelCapability[] = [{
  id: 'qwen3-tts-0.6b',
  name: 'Qwen3-TTS 0.6B',
  default: true,
  installed: true,
  installation_state: 'installed',
  voice_modes: ['preset', 'profile', 'inline_clone', 'voiceprint'],
  compute_devices: [
    { id: 'cpu', available: true, default: false, quantized: false, precision: 'FP32' },
    { id: 'gpu', available: true, default: true, quantized: false, precision: 'BF16', minimum_memory_mib: 3840 },
  ],
  controls: {
    instruction_voice_modes: [],
    instruction_required_voice_modes: [],
    max_instruction_chars: 1000,
    speaking_rate_parameter: false,
    pitch_parameter: false,
    sampling_parameters: false,
  },
  checkpoints: [],
}]
const instructionExamples = [
  '语速缓慢而沉稳',
  '语速明快，节奏紧凑',
  '音调偏高且起伏明显',
  '低沉温柔地表达',
  '带着开心和兴奋的情绪',
  '用悲伤克制的语气说',
  '用明显愤怒但吐字清晰的语气说',
]
const languageLabels:Record<string,string>={Auto:'自动检测',Chinese:'中文',English:'英语',Cantonese:'粤语',Japanese:'日语',Korean:'韩语',German:'德语',French:'法语',Russian:'俄语',Portuguese:'葡萄牙语',Spanish:'西班牙语',Italian:'意大利语'}

export function TtsPage({
  jobs,
  jobDetails,
  loadJobDetail,
  onJobSubmitted,
  selectedJobId,
  onSelect,
  gpuAvailable,
  voiceprints,
  asrModels,
  ttsModels,
  ttsLanguages = fallbackTtsLanguages,
  referenceLanguages = fallbackReferenceLanguages,
  revealRequest,
  onRevealHandled,
}: Props) {
  const [preferences, setPreferences] =
    useState<TtsPreferences>(loadTtsPreferences)
  const [content, setContent] = useState<TtsContent>(loadTtsContent)
  const [referenceSource, setReferenceSource] =
    useState<ReferenceSource>('upload')
  const [referenceName, setReferenceName] = useState('')
  const [referenceAsrModel,setReferenceAsrModel]=useState('qwen3-asr-0.6b')
  const [referenceAsrDevice,setReferenceAsrDevice]=useState<ComputeDevice>('gpu')
  const [voices, setVoices] = useState<string[]>([])
  const [voicesLoading,setVoicesLoading]=useState(true)
  const [busy, setBusy] = useState(false)
  const [referenceBusy, setReferenceBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)
  const preview = useRef<HTMLElement>(null)
  const recorder = useMicrophoneRecorder(30)
  const ttsJobs = useMemo(
    () => jobs.filter((job) => job.kind === 'tts'),
    [jobs],
  )
  const selectedSummary =
    ttsJobs.find(
      (job) => job.id === selectedJobId && job.state === 'succeeded',
    ) || ttsJobs.find((job) => job.state === 'succeeded')
  const selected = selectedSummary ? jobDetails[selectedSummary.id]?.job : undefined
  const visibleJobs = useMemo(
    () => visibleWorkspaceJobs(ttsJobs, selectedSummary?.id),
    [ttsJobs, selectedSummary?.id],
  )
  const draft = { ...preferences, ...content }
  const person =
    voiceprints.find((item) => item.id === draft.personId) || voiceprints[0]
  const eligibleSamples =
    person?.samples.filter((sample) => sample.tts_eligible) || []
  const sample =
    eligibleSamples.find((item) => item.id === draft.sampleId) ||
    eligibleSamples[0]
  const referenceJob = jobs.find((job) => job.id === draft.refJobId)
  const referenceDetail = referenceJob ? jobDetails[referenceJob.id]?.job : undefined
  const referenceProgress = referenceJob
    ? progressPresentation(referenceJob)
    : undefined
  const referenceReady =
    referenceJob?.state === 'succeeded' && Boolean(referenceDetail?.result?.text)
  const microphoneActive =
    recorder.phase === 'requesting' || recorder.phase === 'recording'
  const elapsed = Math.min(
    recorder.maxSeconds,
    Math.max(0, recorder.elapsedSeconds),
  )
  const availableReferenceLanguages = useMemo(
    () => Array.from(new Set(['Auto', ...referenceLanguages])),
    [referenceLanguages],
  )
  const availableTtsModels = ttsModels.length ? ttsModels : fallbackTtsModels
  const selectedTtsModel =
    availableTtsModels.find((item) => item.id === draft.model) ||
    availableTtsModels.find((item) => item.default) ||
    availableTtsModels[0]
  const ttsGpu = selectedTtsModel?.compute_devices.find((item) => item.id === 'gpu')
  const effectiveTtsDevice: ComputeDevice =
    draft.computeDevice === 'gpu' &&
    (ttsGpu?.available === false || gpuAvailable === false)
      ? 'cpu'
      : draft.computeDevice
  const instructionSupported = Boolean(
    selectedTtsModel?.controls.instruction_voice_modes.includes(draft.mode),
  )
  const instructionRequired = Boolean(
    selectedTtsModel?.controls.instruction_required_voice_modes.includes(
      draft.mode,
    ),
  )
  const selectedReferenceModel=asrModels.find(item=>item.id===referenceAsrModel)||asrModels.find(item=>item.default)
  const referenceGpu=selectedReferenceModel?.compute_devices.find(item=>item.id==='gpu')
  const effectiveReferenceDevice:ComputeDevice=referenceAsrDevice==='gpu'&&(referenceGpu?.available===false||gpuAvailable===false)?'cpu':referenceAsrDevice
  const submitBlockReason=busy?'正在提交任务…':referenceBusy?'正在分析克隆参考…':!draft.text.trim()?'请输入需要合成的文本。':!selectedTtsModel?.installed?'所选 TTS 模型尚未完整安装。':instructionRequired&&!draft.instruct.trim()?'音色设计需要填写音色与表达指令。':draft.mode==='inline_clone'&&draft.cloneSource==='upload'&&(!referenceReady||!draft.refText.trim())?'请先完成参考音频自动识别，并确认识别文本。':draft.mode==='inline_clone'&&draft.cloneSource==='voiceprint'&&!sample?'请选择一个已完成转写的声纹样本。':''

  useEffect(() => {
    saveTtsContent(content)
  }, [content])
  useEffect(() => {
    if (selectedTtsModel && preferences.model !== selectedTtsModel.id)
      setPreferences((current) => {
        const next = { ...current, model: selectedTtsModel.id }
        saveTtsPreferences(next)
        return next
      })
  }, [preferences.model, selectedTtsModel])
  useEffect(() => {
    api
      .voices()
      .then((response) => {setVoices(response.preset_speakers);setVoicesLoading(false)})
      .catch((cause) => {setVoicesLoading(false);setError((cause as Error).message)})
  }, [])
  useEffect(() => {
    if (voices.length && !voices.includes(preferences.speaker))
      setPreferences((current) => {
        const next = { ...current, speaker: voices[0] }
        saveTtsPreferences(next)
        return next
      })
  }, [voices, preferences.speaker])
  useEffect(() => {
    if (person && draft.personId !== person.id)
      setPreferences((current) => {
        const next = {
          ...current,
          personId: person.id,
          sampleId: person.samples.find((item) => item.tts_eligible)?.id || '',
        }
        saveTtsPreferences(next)
        return next
      })
  }, [person, draft.personId])
  useEffect(() => {
    if (sample && draft.sampleId !== sample.id)
      setPreferences((current) => {
        const next = { ...current, sampleId: sample.id }
        saveTtsPreferences(next)
        return next
      })
  }, [sample, draft.sampleId])
  useEffect(() => {
    if (selectedSummary) loadJobDetail(selectedSummary)
  }, [loadJobDetail, selectedSummary])
  useEffect(() => {
    if (referenceJob?.state === 'succeeded') loadJobDetail(referenceJob)
  }, [loadJobDetail, referenceJob])
  useEffect(() => {
    if (referenceJob?.state !== 'succeeded' || !referenceDetail?.result?.text)
      return
    setContent((current) =>
      current.refJobId !== referenceJob.id || current.refText
        ? current
        : {
            ...current,
            refText: referenceDetail.result?.text || '',
            refLanguage: referenceDetail.result?.language || 'Auto',
          },
    )
  }, [
    referenceJob?.id,
    referenceDetail?.result?.language,
    referenceDetail?.result?.text,
    referenceJob?.state,
  ])
  useEffect(() => {
    if (!revealRequest || revealRequest.jobId !== selected?.id) return
    const frame = requestAnimationFrame(() => {
      if (matchMedia('(max-width: 900px)').matches)
        preview.current?.scrollIntoView({
          block: 'start',
          behavior: matchMedia('(prefers-reduced-motion: reduce)').matches
            ? 'auto'
            : 'smooth',
        })
      onRevealHandled(revealRequest.token)
    })
    return () => cancelAnimationFrame(frame)
  }, [onRevealHandled, revealRequest, selected?.id])

  const updatePreference = <K extends keyof TtsPreferences>(
    key: K,
    value: TtsPreferences[K],
  ) =>
    setPreferences((current) => {
      const next = { ...current, [key]: value }
      saveTtsPreferences(next)
      return next
    })
  const updateContent = <K extends keyof TtsContent>(
    key: K,
    value: TtsContent[K],
  ) => setContent((current) => ({ ...current, [key]: value }))
  const selectModel = (modelId: string) => {
    const model = availableTtsModels.find((item) => item.id === modelId)
    if (!model) return
    const mode =
      preferences.mode === 'voice_design' &&
      !model.voice_modes.includes('voice_design')
        ? 'preset'
        : preferences.mode
    const clearsInstruction = !model.controls.instruction_voice_modes.includes(mode)
    setPreferences((current) => {
      const next = { ...current, model: model.id, mode }
      saveTtsPreferences(next)
      return next
    })
    if (clearsInstruction && content.instruct) {
      setContent((current) => ({ ...current, instruct: '' }))
      setNotice('所选模型或音色模式不支持自然语言指令，已清空指令。')
    }
  }
  const selectMode = (mode: TtsPreferences['mode']) => {
    updatePreference('mode', mode)
    if (!selectedTtsModel?.controls.instruction_voice_modes.includes(mode) && content.instruct) {
      setContent((current) => ({ ...current, instruct: '' }))
      setNotice('该音色模式不支持自然语言指令，已清空指令。')
    }
  }
  const addInstructionExample = (example: string) =>
    setContent((current) => {
      if (current.instruct.includes(example)) return current
      const separator = current.instruct.trim() ? '，' : ''
      return { ...current, instruct: `${current.instruct.trim()}${separator}${example}` }
    })
  const cancelActiveReference = async () => {
    if (referenceJob && ['queued', 'running'].includes(referenceJob.state))
      await api
        .cancel(referenceJob.id)
        .then(onJobSubmitted)
        .catch(() => undefined)
  }
  const analyzeReference = async (file: File) => {
    setReferenceBusy(true)
    setError('')
    setNotice('')
    try {
      await cancelActiveReference()
      const data = new FormData()
      data.set('file', file)
      data.set('model',referenceAsrModel)
      data.set('compute_device', effectiveReferenceDevice)
      data.set('accelerate_single_task', String(draft.accelerateSingleTask))
      const job = await api.analyzeCloneReference(data)
      setReferenceName(file.name)
      setContent((current) => ({
        ...current,
        refJobId: job.id,
        refText: '',
        refLanguage: 'Auto',
      }))
      onJobSubmitted(job)
      setNotice('已创建克隆参考 ASR 分析任务，识别完成后可确认文本并生成。')
      return true
    } catch (cause) {
      setError((cause as Error).message)
      return false
    } finally {
      setReferenceBusy(false)
    }
  }
  const chooseReference = (file?: File) => {
    if (file) void analyzeReference(file)
    if (fileRef.current) fileRef.current.value = ''
  }
  const analyzeRecording = async () => {
    if (recorder.recorded && (await analyzeReference(recorder.recorded.file)))
      recorder.discard()
  }
  const retryReference = async () => {
    if (!referenceJob) return
    setReferenceBusy(true)
    setError('')
    try {
      const job = await api.retry(referenceJob.id)
      setContent((current) => ({
        ...current,
        refText: '',
        refLanguage: 'Auto',
      }))
      onJobSubmitted(job)
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setReferenceBusy(false)
    }
  }
  const clearReference = async () => {
    await cancelActiveReference()
    setContent((current) => ({
      ...current,
      refJobId: '',
      refText: '',
      refLanguage: 'Auto',
    }))
    setReferenceName('')
    recorder.discard()
    setNotice('已清除当前克隆参考。')
  }
  const submit = async () => {
    if (!draft.text.trim()) {
      setError('请输入需要合成的文本。')
      return
    }
    if (!selectedTtsModel?.installed) {
      setError('所选 TTS 模型尚未完整安装。')
      return
    }
    if (instructionRequired && !draft.instruct.trim()) {
      setError('音色设计需要填写音色与表达指令。')
      return
    }
    if (
      draft.mode === 'inline_clone' &&
      draft.cloneSource === 'upload' &&
      (!referenceReady || !draft.refText.trim())
    ) {
      setError('请先完成参考音频的自动识别，并确认识别文本。')
      return
    }
    if (
      draft.mode === 'inline_clone' &&
      draft.cloneSource === 'voiceprint' &&
      !sample
    ) {
      setError('请选择一个已完成转写的声纹样本。')
      return
    }
    setBusy(true)
    setError('')
    try {
      const data = new FormData()
      data.set('text', draft.text)
      data.set('model', selectedTtsModel.id)
      data.set('language', draft.language)
      data.set('response_format', 'wav')
      data.set('display_name', draft.text.slice(0, 18) || '语音合成')
      data.set('compute_device', effectiveTtsDevice)
      data.set('accelerate_single_task', String(draft.accelerateSingleTask))
      if (draft.mode === 'preset') {
        data.set('voice_mode', 'preset')
        data.set('speaker', draft.speaker)
        if (instructionSupported && draft.instruct.trim())
          data.set('instruct', draft.instruct.trim())
      } else if (draft.mode === 'voice_design') {
        data.set('voice_mode', 'voice_design')
        data.set('instruct', draft.instruct.trim())
      } else if (draft.cloneSource === 'voiceprint') {
        data.set('voice_mode', 'voiceprint')
        data.set('voiceprint_sample_id', sample!.id)
      } else {
        data.set('voice_mode', 'inline_clone')
        data.set('reference_job_id', draft.refJobId)
        data.set('reference_text', draft.refText)
        data.set('reference_language', draft.refLanguage)
      }
      const job = await api.submitTts(data)
      onJobSubmitted(job)
      setNotice('任务已提交，可在任务记录查看进度。')
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const resetPreferences = () => {
    const next = { ...defaultTtsPreferences }
    clearTtsPreferences()
    saveTtsPreferences(next)
    setPreferences(next)
    setContent((current) => ({ ...current, instruct: '' }))
    setNotice('已恢复 TTS 默认配置。')
    setError('')
  }

  return (
    <div className="tts-grid hud-page">
      <section className="tts-editor" data-module="TTS_CONSOLE / SYN_02">
        <div className="section-title">
          <div>
            <h1 tabIndex={-1}>语音合成</h1>
            <p>
              {effectiveTtsDevice === 'cpu'
                ? 'CPU 全精度离线生成，音质优先'
                : 'GPU 原生精度加速，无量化'}
            </p>
          </div>
          <div className="section-actions">
            <span className="performance-badge">
              {effectiveTtsDevice === 'cpu'
                ? draft.accelerateSingleTask
                  ? 'CPU · FP32 · SDPA · AUTO BATCH'
                  : 'CPU · FP32 · SDPA · BATCH 1'
                : draft.accelerateSingleTask
                  ? 'GPU · BF16 · SDPA · AUTO BATCH'
                  : 'GPU · BF16 · SDPA · BATCH 1'}
            </span>
            <button
              className="reset-settings"
              type="button"
              onClick={resetPreferences}
            >
              <RotateCcw size={14} />
              恢复默认配置
            </button>
          </div>
        </div>
        <div
          className="tabs"
          role="tablist"
          aria-label="TTS 音色模式"
          onKeyDown={handleTabKeys}
        >
          <button
            id="tts-mode-preset"
            role="tab"
            aria-selected={draft.mode === 'preset'}
            aria-controls="tts-mode-content"
            tabIndex={draft.mode === 'preset' ? 0 : -1}
            className={draft.mode === 'preset' ? 'active' : ''}
            onClick={() => selectMode('preset')}
          >
            预置音色
          </button>
          <button
            id="tts-mode-clone"
            role="tab"
            aria-selected={draft.mode === 'inline_clone'}
            aria-controls="tts-mode-content"
            tabIndex={draft.mode === 'inline_clone' ? 0 : -1}
            className={draft.mode === 'inline_clone' ? 'active' : ''}
            onClick={() => selectMode('inline_clone')}
          >
            声音克隆
          </button>
          {selectedTtsModel?.voice_modes.includes('voice_design') ? (
            <button
              id="tts-mode-voice-design"
              role="tab"
              aria-selected={draft.mode === 'voice_design'}
              aria-controls="tts-mode-content"
              tabIndex={draft.mode === 'voice_design' ? 0 : -1}
              className={draft.mode === 'voice_design' ? 'active' : ''}
              onClick={() => selectMode('voice_design')}
            >
              音色设计
            </button>
          ) : null}
        </div>
        <div
          id="tts-mode-content"
          role="tabpanel"
          aria-labelledby={
            draft.mode === 'preset'
              ? 'tts-mode-preset'
              : draft.mode === 'voice_design'
                ? 'tts-mode-voice-design'
                : 'tts-mode-clone'
          }
        >
          <label className="text-editor">
            合成文本
            <textarea
              value={draft.text}
              maxLength={50000}
              onChange={(event) => updateContent('text', event.target.value)}
            />
            <small>{draft.text.length} / 50,000</small>
          </label>
          {draft.mode === 'inline_clone' ? (
            <div
              className="clone-source"
              role="tablist"
              aria-label="声音克隆来源"
              onKeyDown={handleTabKeys}
            >
              <button
                role="tab"
                aria-selected={draft.cloneSource === 'upload'}
                aria-controls="tts-mode-content"
                tabIndex={draft.cloneSource === 'upload' ? 0 : -1}
                className={draft.cloneSource === 'upload' ? 'active' : ''}
                onClick={() => updatePreference('cloneSource', 'upload')}
              >
                <Upload size={15} />
                一次性参考
              </button>
              <button
                role="tab"
                aria-selected={draft.cloneSource === 'voiceprint'}
                aria-controls="tts-mode-content"
                tabIndex={draft.cloneSource === 'voiceprint' ? 0 : -1}
                className={draft.cloneSource === 'voiceprint' ? 'active' : ''}
                onClick={() => updatePreference('cloneSource', 'voiceprint')}
              >
                <Fingerprint size={15} />
                声纹库
              </button>
            </div>
          ) : null}
          <div className="two-cols">
            <label>
              TTS 模型
              <select
                aria-label="TTS 模型"
                value={selectedTtsModel?.id || draft.model}
                onChange={(event) => selectModel(event.target.value)}
              >
                {availableTtsModels.map((model) => (
                  <option key={model.id} value={model.id} disabled={!model.installed}>
                    {model.name}{model.default ? '（默认）' : ''}{model.installed ? '' : '（未安装）'}
                  </option>
                ))}
              </select>
              <small className="device-hint">
                1.7B 提供指令控制与音色设计；0.6B 启动更快。
              </small>
            </label>
            <label>
              输出语种
              <select
                value={draft.language}
                onChange={(event) =>
                  updatePreference('language', event.target.value)
                }
              >
                {ttsLanguages.map((language) => (
                  <option key={language} value={language}>{languageLabels[language]||language}</option>
                ))}
              </select>
              <small className="device-hint">
                已知语种时显式选择；未知或混合语种使用 Auto。
              </small>
            </label>
          </div>
          {draft.mode === 'preset' ? (
            <div className="two-cols">
              <label>
                音色
                <select
                  aria-label="音色"
                  value={draft.speaker}
                  onChange={(event) =>
                    updatePreference('speaker', event.target.value)
                  }
                >
                  {voicesLoading?<option value={draft.speaker}>正在加载音色…</option>:null}
                  {voices.map((voice) => (
                    <option key={voice}>{voice}</option>
                  ))}
                </select>
              </label>
            </div>
          ) : draft.mode === 'inline_clone' && draft.cloneSource === 'voiceprint' ? (
            <div className="two-cols">
              <label>
                声纹人员
                <select
                  value={person?.id || ''}
                  disabled={!voiceprints.length}
                  onChange={(event) =>
                    setPreferences((current) => {
                      const next = {
                        ...current,
                        personId: event.target.value,
                        sampleId: '',
                      }
                      saveTtsPreferences(next)
                      return next
                    })
                  }
                >
                  {voiceprints.length ? (
                    voiceprints.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                      </option>
                    ))
                  ) : (
                    <option value="">声纹库为空</option>
                  )}
                </select>
              </label>
            </div>
          ) : null}
          {draft.mode === 'inline_clone' && draft.cloneSource === 'upload' ? (
            <section
              className="clone-reference-panel"
              aria-label="一次性克隆参考"
            >
              <div className="clone-reference-head">
                <div>
                  <b>克隆参考自动识别</b>
                  <small>
                    选择单人音频后由 ASR 自动识别语种、文本和时间戳。
                  </small>
                </div>
                {draft.refJobId ? (
                  <button
                    className="icon-button danger"
                    aria-label="清除克隆参考"
                    onClick={() => void clearReference()}
                  >
                    <Trash2 />
                  </button>
                ) : null}
              </div>
              <div className="sample-options reference-asr-options">
                <select aria-label="克隆参考 ASR 模型" value={referenceAsrModel} disabled={referenceBusy||microphoneActive} onChange={event=>setReferenceAsrModel(event.target.value)}>
                  {(asrModels.length?asrModels:[{id:'qwen3-asr-0.6b',name:'Qwen3-ASR 0.6B',installed:true} as AsrModelCapability]).map(item=><option key={item.id} value={item.id} disabled={!item.installed}>{item.name}{item.installed?'':'（未安装）'}</option>)}
                </select>
                <select aria-label="克隆参考 ASR 计算设备" value={effectiveReferenceDevice} disabled={referenceBusy||microphoneActive} onChange={event=>setReferenceAsrDevice(event.target.value as ComputeDevice)}>
                  <option value="gpu" disabled={gpuAvailable===false||referenceGpu?.available===false}>GPU</option>
                  <option value="cpu">CPU</option>
                </select>
                {referenceAsrDevice==='gpu'&&effectiveReferenceDevice==='cpu'?<small className="device-hint">{computeUnavailableReason(referenceGpu,'当前模型自动使用 CPU。')}</small>:null}
              </div>
              <div
                className="sample-source-tabs"
                role="tablist"
                aria-label="克隆参考来源"
                onKeyDown={handleTabKeys}
              >
                <button
                  id="tts-reference-upload"
                  role="tab"
                  aria-selected={referenceSource === 'upload'}
                  aria-controls="tts-reference-upload-panel"
                  tabIndex={referenceSource === 'upload' ? 0 : -1}
                  className={referenceSource === 'upload' ? 'active' : ''}
                  disabled={referenceBusy || microphoneActive}
                  onClick={() => setReferenceSource('upload')}
                >
                  <Upload size={15} />
                  上传文件
                </button>
                <button
                  id="tts-reference-record"
                  role="tab"
                  aria-selected={referenceSource === 'record'}
                  aria-controls="tts-reference-record-panel"
                  tabIndex={referenceSource === 'record' ? 0 : -1}
                  className={referenceSource === 'record' ? 'active' : ''}
                  disabled={referenceBusy || microphoneActive}
                  onClick={() => setReferenceSource('record')}
                >
                  <Mic2 size={15} />
                  麦克风录音
                </button>
              </div>
              {referenceSource === 'upload' ? (
                <div
                  id="tts-reference-upload-panel"
                  className="sample-input-panel"
                  role="tabpanel"
                  aria-labelledby="tts-reference-upload"
                >
                  <button
                    className="select-like upload"
                    disabled={referenceBusy}
                    onClick={() => fileRef.current?.click()}
                  >
                    <Upload size={16} />
                    {referenceBusy
                      ? '正在提交分析…'
                      : referenceName ||
                        referenceJob?.display_name.replace(
                          'TTS 克隆参考分析 · ',
                          '',
                        ) ||
                        '选择后自动分析参考音频'}
                  </button>
                  <input
                    hidden
                    ref={fileRef}
                    type="file"
                    accept="audio/*"
                    onChange={(event) =>
                      chooseReference(event.target.files?.[0])
                    }
                  />
                  <p>
                    建议 5–15
                    秒、环境安静且只有一位说话人；较长音频会按字词边界截断。
                  </p>
                </div>
              ) : (
                <div
                  id="tts-reference-record-panel"
                  className="sample-input-panel recorder-panel"
                  role="tabpanel"
                  aria-labelledby="tts-reference-record"
                >
                  {!recorder.supported ? (
                    <div className="recorder-unavailable" role="note">
                      <Mic2 />
                      <p>{recorder.unavailableReason}</p>
                    </div>
                  ) : recorder.phase === 'recording' ? (
                    <div className="recording-live" role="status">
                      <span className="recording-dot" aria-hidden="true" />
                      <div>
                        <b>正在录音</b>
                        <strong>{formatTime(elapsed, false)} / 00:00:30</strong>
                      </div>
                      <progress
                        max={recorder.maxSeconds}
                        value={elapsed}
                        aria-label="TTS 克隆参考录音进度"
                      />
                      <button className="record-stop" onClick={recorder.stop}>
                        <CircleStop size={17} />
                        停止并试听
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
                        <b>参考录音完成</b>
                        <span>
                          {formatTime(recorder.recorded.durationSeconds, false)}
                        </span>
                      </div>
                      <audio
                        controls
                        preload="metadata"
                        src={recorder.recorded.url}
                      />
                      <button
                        className="primary"
                        disabled={referenceBusy}
                        onClick={() => void analyzeRecording()}
                      >
                        <Sparkles size={15} />
                        {referenceBusy ? '正在提交…' : '使用并自动分析'}
                      </button>
                      <button
                        className="button secondary"
                        onClick={() => void recorder.start()}
                      >
                        <RefreshCw size={15} />
                        重新录制
                      </button>
                    </div>
                  ) : (
                    <div className="recorder-ready">
                      <Mic2 />
                      <div>
                        <b>直接录制克隆参考</b>
                        <p>停止后可先试听，再确认提交自动识别。</p>
                      </div>
                      <button
                        className="record-start"
                        disabled={referenceBusy}
                        onClick={() => void recorder.start()}
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
              {referenceJob ? (
                <div className={`clone-analysis ${referenceJob.state}`}>
                  <div className="clone-analysis-status">
                    <b>
                      {referenceJob.state === 'queued'
                        ? '等待 ASR 分析'
                        : referenceJob.state === 'running'
                          ? '正在 ASR 分析'
                          : referenceJob.state === 'succeeded'
                            ? '参考识别完成'
                            : referenceJob.state === 'failed'
                              ? '参考识别失败'
                              : '参考分析已取消'}
                    </b>
                    <span>
                      {referenceProgress?.percent}%
                      {referenceProgress?.estimated ? ' 估算' : ''} ·{' '}
                      {referenceProgress?.stage}
                      {referenceProgress?.detail
                        ? ` · ${referenceProgress.detail}`
                        : ''}
                    </span>
                  </div>
                  {referenceJob.state === 'running' ||
                  referenceJob.state === 'queued' ? (
                    <progress
                      max={1}
                      value={referenceJob.progress}
                      aria-label="克隆参考分析进度"
                    />
                  ) : null}
                  {referenceJob.state === 'failed' ||
                  referenceJob.state === 'cancelled' ? (
                    <>
                      <p className="error">
                        {referenceJob.error_message || '参考音频未能完成识别。'}
                      </p>
                      <button
                        className="button secondary"
                        disabled={referenceBusy}
                        onClick={() => void retryReference()}
                      >
                        <RefreshCw size={15} />
                        重试分析
                      </button>
                    </>
                  ) : null}
                  {referenceJob.state === 'succeeded' && jobDetails[referenceJob.id]?.state === 'loading' ? (
                    <p className="notice" role="status">正在加载参考音频识别结果…</p>
                  ) : null}
                  {referenceJob.state === 'succeeded' && jobDetails[referenceJob.id]?.state === 'error' ? (
                    <div role="alert">
                      <p className="error">{jobDetails[referenceJob.id]?.error || '参考识别结果加载失败。'}</p>
                      <button className="button secondary" onClick={() => loadJobDetail(referenceJob, true)}>
                        <RefreshCw size={15} />重新加载结果
                      </button>
                    </div>
                  ) : null}
                  {referenceReady ? (
                    <div className="clone-analysis-result">
                      <label>
                        参考音频语种
                        <select
                          value={draft.refLanguage}
                          onChange={(event) =>
                            updateContent('refLanguage', event.target.value)
                          }
                        >
                          {availableReferenceLanguages.map((language) => (
                            <option key={language} value={language}>{languageLabels[language]||language}</option>
                          ))}
                        </select>
                      </label>
                      <label>
                        自动识别文本（可修正）
                        <textarea
                          className="short"
                          value={draft.refText}
                          onChange={(event) =>
                            updateContent('refText', event.target.value)
                          }
                          placeholder="请核对文本与参考音频逐字一致。"
                        />
                      </label>
                      {referenceDetail?.result?.artifacts?.some(
                        (item) => item.name === 'reference.wav',
                      ) ? (
                        <audio
                          controls
                          preload="metadata"
                          src={artifactUrl(referenceJob.id, 'reference.wav')}
                        />
                      ) : null}
                      <small>
                        修正后将使用当前文本；超过 15
                        秒时会按修正内容重新精确对齐。
                      </small>
                    </div>
                  ) : null}
                </div>
              ) : draft.refJobId ? (
                <p className="error">
                  参考分析任务已不存在，请重新上传或录音。
                </p>
              ) : null}
            </section>
          ) : null}
          {draft.mode === 'inline_clone' &&
          draft.cloneSource === 'voiceprint' ? (
            <label>
              声纹样本
              <select
                aria-label="TTS 声纹样本"
                value={sample?.id || ''}
                disabled={!eligibleSamples.length}
                onChange={(event) =>
                  updatePreference('sampleId', event.target.value)
                }
              >
                {eligibleSamples.length ? (
                  eligibleSamples.map((item, index) => (
                    <option key={item.id} value={item.id}>
                      样本 {eligibleSamples.length - index} ·{' '}
                      {formatTime(item.duration || 0)}
                      {item.duration && item.duration > 15 ? ' · 自动截断' : ''}
                    </option>
                  ))
                ) : (
                  <option value="">没有可用于 TTS 的样本</option>
                )}
              </select>
              {sample ? (
                <small className="sample-summary">
                  {sample.language} · {sample.transcript}
                  {sample.duration && sample.duration > 15
                    ? ' · 克隆前将按字词边界精确截断至 15 秒以内。'
                    : ''}
                </small>
              ) : null}
            </label>
          ) : null}
          {instructionSupported ? (
            <section className="tts-instruction-panel" aria-label="TTS 高级指令">
              <label>
                {draft.mode === 'voice_design' ? '音色与表达指令' : '风格与表达指令（可选）'}
                <textarea
                  name="instruct"
                  className="short"
                  value={draft.instruct}
                  maxLength={selectedTtsModel.controls.max_instruction_chars}
                  placeholder={
                    draft.mode === 'voice_design'
                      ? '例如：温暖成熟的女性声音，音调略低，语速舒缓，带着克制的喜悦。'
                      : '例如：语速稍慢，用温柔而开心的语气说。'
                  }
                  onChange={(event) => updateContent('instruct', event.target.value)}
                />
                <small>
                  {draft.instruct.length} / {selectedTtsModel.controls.max_instruction_chars}；
                  语速、音调和情绪均通过自然语言描述，不是数值参数。
                </small>
              </label>
              <div className="instruction-examples" aria-label="指令示例">
                {instructionExamples.map((example) => (
                  <button
                    key={example}
                    type="button"
                    onClick={() => addInstructionExample(example)}
                  >
                    {example}
                  </button>
                ))}
              </div>
            </section>
          ) : null}
          <label className="device-control">
            计算设备
            <select
              aria-label="TTS 计算设备"
              value={effectiveTtsDevice}
              onChange={(event) =>
                updatePreference(
                  'computeDevice',
                  event.target.value as ComputeDevice,
                )
              }
            >
              <option value="gpu" disabled={gpuAvailable === false || ttsGpu?.available === false}>
                GPU · BF16{gpuAvailable === false || ttsGpu?.available === false ? '（不可用）' : '（默认）'}
              </option>
              <option value="cpu">CPU · FP32</option>
            </select>
            {draft.computeDevice === 'gpu' && effectiveTtsDevice === 'cpu' ? (
              <small className="device-hint">
                {computeUnavailableReason(ttsGpu,'所选模型无法使用当前 GPU，本次自动使用 CPU。')}
              </small>
            ) : (
              <small className="device-hint">
                API 显式请求不可用 GPU 时返回 503；页面会按模型能力改用 CPU。
              </small>
            )}
          </label>
          <div className="acceleration-control">
            <label>
              <input
                type="checkbox"
                checked={draft.accelerateSingleTask}
                onChange={(event) =>
                  updatePreference('accelerateSingleTask', event.target.checked)
                }
              />
              <span>单任务加速</span>
            </label>
            <InfoTooltip
              id="tts-acceleration-help"
              text="按 CPU 核心与可用内存或 GPU 显存自动提高任务内部批次。长文本收益更明显；不改变模型、精度或解码方式，内存不足时会自动回退。"
            />
          </div>
          <div className="submission-actions">
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
            {submitBlockReason&&!busy?<p id="tts-submit-reason" className="submit-block-reason" role="status">{submitBlockReason}</p>:null}
            <button
              className="primary synth"
              disabled={Boolean(submitBlockReason)}
              aria-describedby={submitBlockReason?'tts-submit-reason':undefined}
              onClick={() => void submit()}
            >
              <Sparkles size={18} />
              {busy ? '正在提交…' : '生成语音'}
            </button>
          </div>
          <div className="quality-note">
            <b>本地质量策略</b>
            <span>
              官方 {selectedTtsModel?.name.replace('Qwen3-TTS ', '') || '0.6B'} 权重 · 无量化 ·{' '}
              {effectiveTtsDevice === 'cpu'
                ? 'CPU FP32；兼容性优先。'
                : 'GPU BF16；官方原生精度加速。'}
            </span>
            {!instructionSupported ? (
              <small className="tts-control-note">
                当前模型与音色模式根据文本语义和标点自动处理韵律，不接受自然语言高级指令。
              </small>
            ) : (
              <small className="tts-control-note">
                当前使用官方自然语言指令控制；独立语速、音高与底层采样参数仍保持关闭。
              </small>
            )}
          </div>
        </div>
      </section>
      <aside
        ref={preview}
        className="tts-preview"
        data-module="RENDER_QUEUE / Q_02"
      >
        <h2>当前合成结果</h2>
        {selectedSummary && jobDetails[selectedSummary.id]?.state === 'loading' ? (
          <div className="empty small" role="status">
            <Sparkles />
            <p>正在按需加载完整合成结果…</p>
          </div>
        ) : selectedSummary && jobDetails[selectedSummary.id]?.state === 'error' ? (
          <div className="empty small" role="alert">
            <Sparkles />
            <p>{jobDetails[selectedSummary.id]?.error || '合成结果加载失败。'}</p>
            <button onClick={() => loadJobDetail(selectedSummary, true)}>重新加载</button>
          </div>
        ) : selected?.result ? (
          <div className="audio-card">
            <div>
              <b>{selected.display_name}</b>
              <span>
                {selected.result.speaker || (selected.request.voice_mode === 'voice_design' ? '设计音色' : '克隆音色')} ·{' '}
                {selected.result.model_name || selected.result.model || (selected.request.model as string | undefined) || 'Qwen3-TTS 0.6B'} ·{' '}
                {selected.result.duration}s ·{' '}
                {(
                  selected.result.compute_device ||
                  selected.request.compute_device ||
                  'gpu'
                )
                  .toString()
                  .toUpperCase()}{' '}
                {selected.result.precision || ''}
              </span>
              {selected.result.instruct ? (
                <small className="tts-result-instruction">指令：{selected.result.instruct}</small>
              ) : null}
            </div>
            {selected.result.artifacts?.[0] ? (
              <AudioTransport
                src={artifactUrl(
                  selected.id,
                  selected.result.artifacts[0].name,
                )}
                peaks={selected.result.waveform}
                duration={selected.result.duration}
              />
            ) : null}
            {selected.result.artifacts?.[0] ? (
              <a
                className="button primary"
                href={artifactUrl(
                  selected.id,
                  selected.result.artifacts[0].name,
                )}
              >
                <Download size={16} />
                下载 {selected.result.format?.toUpperCase() || 'WAV'}
              </a>
            ) : null}
          </div>
        ) : (
          <div className="empty small">
            <Sparkles />
            <p>合成完成后可在这里试听和下载</p>
          </div>
        )}
        <h2>任务列表</h2>
        {visibleJobs.map((job) => (
          <JobMini
            key={job.id}
            job={job}
            isSelected={job.id === selectedSummary?.id}
            onOpen={(item) => item.state === 'succeeded' && onSelect(item)}
          />
        ))}
      </aside>
    </div>
  )
}
