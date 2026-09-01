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
import { api, artifactUrl, formatTime, isUploadCancelled, uploadLimitMessage, type SubmissionProgress as UploadProgress } from '../lib/api'
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
import { SubmissionProgress } from '../components/SubmissionProgress'
import { useTranslation } from 'react-i18next'

type Props = {
  jobs: JobSummary[]
  jobDetails: Record<string, JobDetailResource>
  loadJobDetail: (job: JobSummary, force?: boolean) => void
  onJobSubmitted: (job: Job) => void
  selectedJobId?: string
  onSelect: (job: JobSummary) => void
  gpuAvailable?: boolean
  maxUploadBytes?: number
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
const instructionExampleKeys = ['slowSteady','brisk','highPitch','lowGentle','happy','sad','angry'] as const

export function TtsPage({
  jobs,
  jobDetails,
  loadJobDetail,
  onJobSubmitted,
  selectedJobId,
  onSelect,
  gpuAvailable,
  maxUploadBytes,
  voiceprints,
  asrModels,
  ttsModels,
  ttsLanguages = fallbackTtsLanguages,
  referenceLanguages = fallbackReferenceLanguages,
  revealRequest,
  onRevealHandled,
}: Props) {
  const { t, i18n } = useTranslation()
  const [preferences, setPreferences] =
    useState<TtsPreferences>(loadTtsPreferences)
  const [content, setContent] = useState<TtsContent>(()=>loadTtsContent(t('tts.defaultText')))
  const [referenceSource, setReferenceSource] =
    useState<ReferenceSource>('upload')
  const [referenceName, setReferenceName] = useState('')
  const [referenceFile,setReferenceFile]=useState<File>()
  const [referenceAsrModel,setReferenceAsrModel]=useState('qwen3-asr-0.6b')
  const [referenceAsrDevice,setReferenceAsrDevice]=useState<ComputeDevice>('gpu')
  const [voices, setVoices] = useState<string[]>([])
  const [voicesLoading,setVoicesLoading]=useState(true)
  const [busy, setBusy] = useState(false)
  const [referenceBusy, setReferenceBusy] = useState(false)
  const [referenceUploadProgress,setReferenceUploadProgress]=useState<UploadProgress>()
  const [referenceError,setReferenceError]=useState('')
  const [referenceNotice,setReferenceNotice]=useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)
  const preview = useRef<HTMLElement>(null)
  const referenceUploadController=useRef<AbortController|undefined>(undefined)
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
    ? progressPresentation(referenceJob,t)
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
  const referenceBusyReason=referenceUploadProgress?.phase==='creating'?t('tts.reference.creating'):referenceUploadProgress?t('tts.reference.uploading'):t('tts.reference.analyzing')
  const submitBlockReason=busy?t('tts.validation.submitting'):referenceBusy?referenceBusyReason:!draft.text.trim()?t('tts.validation.textRequired'):!selectedTtsModel?.installed?t('tts.validation.modelMissing'):instructionRequired&&!draft.instruct.trim()?t('tts.validation.instructionRequired'):draft.mode==='inline_clone'&&draft.cloneSource==='upload'&&(!referenceReady||!draft.refText.trim())?t('tts.validation.referenceRequired'):draft.mode==='inline_clone'&&draft.cloneSource==='voiceprint'&&!sample?t('tts.validation.sampleRequired'):''

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
      setNotice(t('tts.notices.instructionClearedForModel'))
    }
  }
  const selectMode = (mode: TtsPreferences['mode']) => {
    updatePreference('mode', mode)
    if (!selectedTtsModel?.controls.instruction_voice_modes.includes(mode) && content.instruct) {
      setContent((current) => ({ ...current, instruct: '' }))
      setNotice(t('tts.notices.instructionClearedForMode'))
    }
  }
  const addInstructionExample = (example: string) =>
    setContent((current) => {
      if (current.instruct.includes(example)) return current
      const separator = current.instruct.trim() ? t('tts.instruction.separator') : ''
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
    const limitError=uploadLimitMessage(file,maxUploadBytes,t,i18n.resolvedLanguage||'zh-CN')
    setReferenceError('')
    setReferenceNotice('')
    if(limitError){setReferenceError(limitError);return false}
    setReferenceBusy(true)
    setReferenceUploadProgress({phase:'preparing',loadedBytes:0})
    setError('')
    setNotice('')
    try {
      await cancelActiveReference()
      const controller=new AbortController()
      referenceUploadController.current=controller
      const data = new FormData()
      data.set('file', file)
      data.set('model',referenceAsrModel)
      data.set('compute_device', effectiveReferenceDevice)
      data.set('accelerate_single_task', String(draft.accelerateSingleTask))
      const job = await api.analyzeCloneReference(data,{signal:controller.signal,onProgress:setReferenceUploadProgress})
      setReferenceName(file.name)
      setReferenceFile(undefined)
      setContent((current) => ({
        ...current,
        refJobId: job.id,
        refText: '',
        refLanguage: 'Auto',
      }))
      onJobSubmitted(job)
      setNotice(t('tts.notices.referenceCreated'))
      return true
    } catch (cause) {
      if(isUploadCancelled(cause))setReferenceNotice(t('tts.notices.referenceUploadCancelled'))
      else setReferenceError((cause as Error).message)
      return false
    } finally {
      referenceUploadController.current=undefined
      setReferenceUploadProgress(undefined)
      setReferenceBusy(false)
    }
  }
  const chooseReference = (file?: File) => {
    if (file){setReferenceFile(file);setReferenceName(file.name);setReferenceError('');setReferenceNotice('');void analyzeReference(file)}
    if (fileRef.current) fileRef.current.value = ''
  }
  const analyzeRecording = async () => {
    if(recorder.recorded){setReferenceFile(undefined);setReferenceName(recorder.recorded.file.name);if(await analyzeReference(recorder.recorded.file))recorder.discard()}
  }
  const retryReference = async () => {
    if (!referenceJob) return
    setReferenceBusy(true)
    setReferenceError('')
    try {
      const job = await api.retry(referenceJob.id)
      setContent((current) => ({
        ...current,
        refText: '',
        refLanguage: 'Auto',
      }))
      onJobSubmitted(job)
    } catch (cause) {
      setReferenceError((cause as Error).message)
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
    setReferenceFile(undefined)
    setReferenceError('')
    setReferenceNotice('')
    recorder.discard()
    setNotice(t('tts.notices.referenceCleared'))
  }
  const submit = async () => {
    if (!draft.text.trim()) {
      setError(t('tts.validation.textRequired'))
      return
    }
    if (!selectedTtsModel?.installed) {
      setError(t('tts.validation.modelMissing'))
      return
    }
    if (instructionRequired && !draft.instruct.trim()) {
      setError(t('tts.validation.instructionRequired'))
      return
    }
    if (
      draft.mode === 'inline_clone' &&
      draft.cloneSource === 'upload' &&
      (!referenceReady || !draft.refText.trim())
    ) {
      setError(t('tts.validation.referenceRequired'))
      return
    }
    if (
      draft.mode === 'inline_clone' &&
      draft.cloneSource === 'voiceprint' &&
      !sample
    ) {
      setError(t('tts.validation.sampleRequired'))
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
      data.set('display_name', draft.text.slice(0, 18) || t('tts.title'))
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
      setNotice(t('tts.notices.submitted'))
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
    setNotice(t('tts.notices.defaultsRestored'))
    setError('')
  }

  return (
    <div className="tts-grid hud-page">
      <section className="tts-editor" data-module="TTS_CONSOLE / SYN_02">
        <div className="section-title">
          <div>
            <h1 tabIndex={-1}>{t('tts.title')}</h1>
            <p>
              {effectiveTtsDevice === 'cpu'
                ? t('tts.subtitleCpu')
                : t('tts.subtitleGpu')}
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
              {t('tts.restoreDefaults')}
            </button>
          </div>
        </div>
        <div
          className="tabs"
          role="tablist"
          aria-label={t('tts.mode.label')}
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
            {t('tts.mode.preset')}
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
            {t('tts.mode.clone')}
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
              {t('tts.mode.design')}
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
            {t('tts.text')}
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
              aria-label={t('tts.cloneSource.label')}
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
                {t('tts.cloneSource.upload')}
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
                {t('tts.cloneSource.voiceprint')}
              </button>
            </div>
          ) : null}
          <div className="two-cols">
            <label>
              {t('tts.model')}
              <select
                aria-label={t('tts.model')}
                value={selectedTtsModel?.id || draft.model}
                onChange={(event) => selectModel(event.target.value)}
              >
                {availableTtsModels.map((model) => (
                  <option key={model.id} value={model.id} disabled={!model.installed}>
                    {model.name}{model.default ? t('tts.defaultMark') : ''}{model.installed ? '' : t('tts.notInstalled')}
                  </option>
                ))}
              </select>
              <small className="device-hint">
                {t('tts.modelHelp')}
              </small>
            </label>
            <label>
              {t('tts.outputLanguage')}
              <select
                value={draft.language}
                onChange={(event) =>
                  updatePreference('language', event.target.value)
                }
              >
                {ttsLanguages.map((language) => (
                  <option key={language} value={language}>{t(`common.languages.${language}`,{defaultValue:language})}</option>
                ))}
              </select>
              <small className="device-hint">
                {t('tts.languageHelp')}
              </small>
            </label>
          </div>
          {draft.mode === 'preset' ? (
            <div className="two-cols">
              <label>
                {t('tts.voice')}
                <select
                  aria-label={t('tts.voice')}
                  value={draft.speaker}
                  onChange={(event) =>
                    updatePreference('speaker', event.target.value)
                  }
                >
                  {voicesLoading?<option value={draft.speaker}>{t('tts.loadingVoices')}</option>:null}
                  {voices.map((voice) => (
                    <option key={voice}>{voice}</option>
                  ))}
                </select>
              </label>
            </div>
          ) : draft.mode === 'inline_clone' && draft.cloneSource === 'voiceprint' ? (
            <div className="two-cols">
              <label>
                {t('tts.voiceprintPerson')}
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
                    <option value="">{t('tts.emptyVoiceprints')}</option>
                  )}
                </select>
              </label>
            </div>
          ) : null}
          {draft.mode === 'inline_clone' && draft.cloneSource === 'upload' ? (
            <section
              className="clone-reference-panel"
              aria-label={t('tts.reference.panel')}
            >
              <div className="clone-reference-head">
                <div>
                  <b>{t('tts.reference.title')}</b>
                  <small>
                    {t('tts.reference.help')}
                  </small>
                </div>
                {draft.refJobId ? (
                  <button
                    className="icon-button danger"
                    aria-label={t('tts.reference.clear')}
                    onClick={() => void clearReference()}
                  >
                    <Trash2 />
                  </button>
                ) : null}
              </div>
              <div className="sample-options reference-asr-options">
                <select aria-label={t('tts.reference.asrModel')} value={referenceAsrModel} disabled={referenceBusy||microphoneActive} onChange={event=>setReferenceAsrModel(event.target.value)}>
                  {(asrModels.length?asrModels:[{id:'qwen3-asr-0.6b',name:'Qwen3-ASR 0.6B',installed:true} as AsrModelCapability]).map(item=><option key={item.id} value={item.id} disabled={!item.installed}>{item.name}{item.installed?'':t('tts.notInstalled')}</option>)}
                </select>
                <select aria-label={t('tts.reference.computeDevice')} value={effectiveReferenceDevice} disabled={referenceBusy||microphoneActive} onChange={event=>setReferenceAsrDevice(event.target.value as ComputeDevice)}>
                  <option value="gpu" disabled={gpuAvailable===false||referenceGpu?.available===false}>GPU</option>
                  <option value="cpu">CPU</option>
                </select>
                {referenceAsrDevice==='gpu'&&effectiveReferenceDevice==='cpu'?<small className="device-hint">{computeUnavailableReason(referenceGpu,t,t('tts.reference.cpuFallback'))}</small>:null}
              </div>
              <div
                className="sample-source-tabs"
                role="tablist"
                aria-label={t('tts.reference.source')}
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
                  {t('voiceprints.uploadFile')}
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
                  {t('voiceprints.microphone')}
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
                      ? referenceUploadProgress?.phase === 'creating'
                        ? t('tts.reference.creatingShort')
                        : referenceUploadProgress?.phase === 'uploading' && referenceUploadProgress.percent !== undefined
                          ? t('tts.reference.uploadPercent',{percent:referenceUploadProgress.percent})
                          : t('tts.reference.preparing')
                      : referenceName ||
                        referenceJob?.display_name.replace(
                          t('tts.reference.jobPrefix'),
                          '',
                        ) ||
                        t('tts.reference.select')}
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
                    {t('tts.reference.audioAdvice')}
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
                        <b>{t('voiceprints.recording')}</b>
                        <strong>{formatTime(elapsed, false)} / 00:00:30</strong>
                      </div>
                      <progress
                        max={recorder.maxSeconds}
                        value={elapsed}
                        aria-label={t('tts.reference.recordingProgress')}
                      />
                      <button className="record-stop" onClick={recorder.stop}>
                        <CircleStop size={17} />
                        {t('voiceprints.stopAndPreview')}
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
                        <b>{t('tts.reference.recordingComplete')}</b>
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
                    {referenceBusy
                      ? referenceUploadProgress?.phase === 'creating'
                        ? t('tts.reference.creatingShort')
                        : referenceUploadProgress?.phase === 'uploading' && referenceUploadProgress.percent !== undefined
                          ? t('tts.reference.uploadPercent',{percent:referenceUploadProgress.percent})
                          : t('tts.reference.preparing')
                      : t('tts.reference.useAndAnalyze')}
                      </button>
                      <button
                        className="button secondary"
                        onClick={() => void recorder.start()}
                      >
                        <RefreshCw size={15} />
                        {t('voiceprints.recordAgain')}
                      </button>
                    </div>
                  ) : (
                    <div className="recorder-ready">
                      <Mic2 />
                      <div>
                        <b>{t('tts.reference.recordDirectly')}</b>
                        <p>{t('tts.reference.recordHelp')}</p>
                      </div>
                      <button
                        className="record-start"
                        disabled={referenceBusy}
                        onClick={() => void recorder.start()}
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
              {referenceUploadProgress ? (
                <SubmissionProgress
                  label={t('tts.reference.uploadLabel')}
                  progress={referenceUploadProgress}
                  onCancel={() => referenceUploadController.current?.abort()}
                />
              ) : null}
              {referenceError ? (
                <div className="submission-feedback" role="alert">
                  <p className="error">{referenceError}</p>
                  {referenceFile && !uploadLimitMessage(referenceFile,maxUploadBytes,t,i18n.resolvedLanguage||'zh-CN') ? (
                    <button className="button secondary" disabled={referenceBusy} onClick={() => void analyzeReference(referenceFile)}>
                      <RefreshCw size={15} />{t('voiceprints.uploadAgain')}
                    </button>
                  ) : null}
                </div>
              ) : null}
              {referenceNotice ? (
                <div className="submission-feedback" role="status">
                  <p className="notice">{referenceNotice}</p>
                  {referenceFile ? (
                    <button className="button secondary" disabled={referenceBusy} onClick={() => void analyzeReference(referenceFile)}>
                      <RefreshCw size={15} />{t('voiceprints.uploadAgain')}
                    </button>
                  ) : null}
                </div>
              ) : null}
              {referenceJob ? (
                <div className={`clone-analysis ${referenceJob.state}`}>
                  <div className="clone-analysis-status">
                    <b>
                      {referenceJob.state === 'queued'
                        ? t('tts.reference.states.queued')
                        : referenceJob.state === 'running'
                          ? t('tts.reference.states.running')
                          : referenceJob.state === 'succeeded'
                            ? t('tts.reference.states.succeeded')
                            : referenceJob.state === 'failed'
                              ? t('tts.reference.states.failed')
                              : t('tts.reference.states.cancelled')}
                    </b>
                    <span>
                      {referenceProgress?.percent}%
                      {referenceProgress?.estimated ? ` ${t('jobs.estimated')}` : ''} ·{' '}
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
                      aria-label={t('tts.reference.analysisProgress')}
                    />
                  ) : null}
                  {referenceJob.state === 'failed' ||
                  referenceJob.state === 'cancelled' ? (
                    <>
                      <p className="error">
                        {referenceJob.error_message || t('tts.reference.failedFallback')}
                      </p>
                      <button
                        className="button secondary"
                        disabled={referenceBusy}
                        onClick={() => void retryReference()}
                      >
                        <RefreshCw size={15} />
                        {t('tts.reference.retry')}
                      </button>
                    </>
                  ) : null}
                  {referenceJob.state === 'succeeded' && jobDetails[referenceJob.id]?.state === 'loading' ? (
                    <p className="notice" role="status">{t('tts.reference.loadingResult')}</p>
                  ) : null}
                  {referenceJob.state === 'succeeded' && jobDetails[referenceJob.id]?.state === 'error' ? (
                    <div role="alert">
                      <p className="error">{jobDetails[referenceJob.id]?.error || t('tts.reference.loadFailed')}</p>
                      <button className="button secondary" onClick={() => loadJobDetail(referenceJob, true)}>
                        <RefreshCw size={15} />{t('tts.reference.reloadResult')}
                      </button>
                    </div>
                  ) : null}
                  {referenceReady ? (
                    <div className="clone-analysis-result">
                      <label>
                        {t('tts.reference.language')}
                        <select
                          value={draft.refLanguage}
                          onChange={(event) =>
                            updateContent('refLanguage', event.target.value)
                          }
                        >
                          {availableReferenceLanguages.map((language) => (
                            <option key={language} value={language}>{t(`common.languages.${language}`,{defaultValue:language})}</option>
                          ))}
                        </select>
                      </label>
                      <label>
                        {t('tts.reference.transcript')}
                        <textarea
                          className="short"
                          value={draft.refText}
                          onChange={(event) =>
                            updateContent('refText', event.target.value)
                          }
                          placeholder={t('tts.reference.transcriptPlaceholder')}
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
                        {t('tts.reference.correctionHelp')}
                      </small>
                    </div>
                  ) : null}
                </div>
              ) : draft.refJobId ? (
                <p className="error">
                  {t('tts.reference.missing')}
                </p>
              ) : null}
            </section>
          ) : null}
          {draft.mode === 'inline_clone' &&
          draft.cloneSource === 'voiceprint' ? (
            <label>
              {t('tts.sample.label')}
              <select
                aria-label={t('tts.sample.ariaLabel')}
                value={sample?.id || ''}
                disabled={!eligibleSamples.length}
                onChange={(event) =>
                  updatePreference('sampleId', event.target.value)
                }
              >
                {eligibleSamples.length ? (
                  eligibleSamples.map((item, index) => (
                    <option key={item.id} value={item.id}>
                      {t('tts.sample.number',{number:eligibleSamples.length-index})} ·{' '}
                      {formatTime(item.duration || 0)}
                      {item.duration && item.duration > 15 ? ` · ${t('tts.sample.autoTruncate')}` : ''}
                    </option>
                  ))
                ) : (
                  <option value="">{t('tts.sample.empty')}</option>
                )}
              </select>
              {sample ? (
                <small className="sample-summary">
                  {sample.language} · {sample.transcript}
                  {sample.duration && sample.duration > 15
                    ? ` · ${t('tts.sample.truncateHelp')}`
                    : ''}
                </small>
              ) : null}
            </label>
          ) : null}
          {instructionSupported ? (
            <section className="tts-instruction-panel" aria-label={t('tts.instruction.panel')}>
              <label>
                {draft.mode === 'voice_design' ? t('tts.instruction.designLabel') : t('tts.instruction.styleLabel')}
                <textarea
                  name="instruct"
                  className="short"
                  value={draft.instruct}
                  maxLength={selectedTtsModel.controls.max_instruction_chars}
                  placeholder={
                    draft.mode === 'voice_design'
                      ? t('tts.instruction.designPlaceholder')
                      : t('tts.instruction.stylePlaceholder')
                  }
                  onChange={(event) => updateContent('instruct', event.target.value)}
                />
                <small>
                  {draft.instruct.length} / {selectedTtsModel.controls.max_instruction_chars}；
                  {t('tts.instruction.help')}
                </small>
              </label>
              <div className="instruction-examples" aria-label={t('tts.instruction.examples')}>
                {instructionExampleKeys.map((key) => {
                  const example=t(`tts.instruction.example.${key}`)
                  return (
                  <button
                    key={example}
                    type="button"
                    onClick={() => addInstructionExample(example)}
                  >
                    {example}
                  </button>
                  )
                })}
              </div>
            </section>
          ) : null}
          <label className="device-control">
            {t('tts.computeDevice')}
            <select
              aria-label={t('tts.computeDeviceAria')}
              value={effectiveTtsDevice}
              onChange={(event) =>
                updatePreference(
                  'computeDevice',
                  event.target.value as ComputeDevice,
                )
              }
            >
              <option value="gpu" disabled={gpuAvailable === false || ttsGpu?.available === false}>
                GPU · BF16{gpuAvailable === false || ttsGpu?.available === false ? t('tts.unavailableMark') : t('tts.defaultMark')}
              </option>
              <option value="cpu">CPU · FP32</option>
            </select>
            {draft.computeDevice === 'gpu' && effectiveTtsDevice === 'cpu' ? (
              <small className="device-hint">
                {computeUnavailableReason(ttsGpu,t,t('tts.gpuFallback'))}
              </small>
            ) : (
              <small className="device-hint">
                {t('tts.computeHelp')}
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
              <span>{t('tts.acceleration')}</span>
            </label>
            <InfoTooltip
              id="tts-acceleration-help"
              text={t('tts.accelerationHelp')}
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
              {busy ? t('tts.submit.submitting') : t('tts.submit.generate')}
            </button>
          </div>
          <div className="quality-note">
            <b>{t('tts.quality.title')}</b>
            <span>
              {t('tts.quality.official',{model:selectedTtsModel?.name.replace('Qwen3-TTS ','')||'0.6B'})} ·{' '}
              {effectiveTtsDevice === 'cpu'
                ? t('tts.quality.cpu')
                : t('tts.quality.gpu')}
            </span>
            {!instructionSupported ? (
              <small className="tts-control-note">
                {t('tts.quality.noInstruction')}
              </small>
            ) : (
              <small className="tts-control-note">
                {t('tts.quality.instruction')}
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
        <h2>{t('tts.results.title')}</h2>
        {selectedSummary && jobDetails[selectedSummary.id]?.state === 'loading' ? (
          <div className="empty small" role="status">
            <Sparkles />
            <p>{t('tts.results.loading')}</p>
          </div>
        ) : selectedSummary && jobDetails[selectedSummary.id]?.state === 'error' ? (
          <div className="empty small" role="alert">
            <Sparkles />
            <p>{jobDetails[selectedSummary.id]?.error || t('tts.results.loadFailed')}</p>
            <button onClick={() => loadJobDetail(selectedSummary, true)}>{t('common.actions.reload')}</button>
          </div>
        ) : selected?.result ? (
          <div className="audio-card">
            <div>
              <b>{selected.display_name}</b>
              <span>
                {selected.result.speaker || (selected.request.voice_mode === 'voice_design' ? t('tts.results.designedVoice') : t('tts.results.clonedVoice'))} ·{' '}
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
                <small className="tts-result-instruction">{t('tts.results.instruction',{value:selected.result.instruct})}</small>
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
                {t('tts.results.download',{format:selected.result.format?.toUpperCase()||'WAV'})}
              </a>
            ) : null}
          </div>
        ) : (
          <div className="empty small">
            <Sparkles />
            <p>{t('tts.results.empty')}</p>
          </div>
        )}
        <h2>{t('tts.taskList')}</h2>
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
