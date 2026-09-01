import { memo, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { Download, FileAudio, Pause, Pencil, Play, RotateCcw, Search, UploadCloud, UserPlus, X } from 'lucide-react'
import { api, artifactUrl, formatTime, isUploadCancelled, sourceUrl, uploadLimitMessage, type SubmissionProgress as UploadProgress } from '../lib/api'
import type { AsrModelCapability, ComputeDevice, HotwordLibraryCapability, HotwordList, Job, JobDetailResource, JobResult, JobSummary, ResourceState, ResultRevealRequest, Segment, Speaker, VoiceprintPerson } from '../lib/types'
import { Waveform } from '../components/Waveform'
import { JobMini } from '../components/JobMini'
import { Modal } from '../components/Modal'
import { InfoTooltip } from '../components/InfoTooltip'
import { clearAsrPreferences, defaultAsrPreferences, loadAsrPreferences, publicAlignerLanguages, publicAsrLanguages, saveAsrPreferences, type AsrPreferences } from '../lib/preferences'
import { visibleWorkspaceJobs } from '../lib/jobs'
import { computeUnavailableReason } from '../lib/presentation'
import { SubmissionProgress } from '../components/SubmissionProgress'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
const segmentBatchSize=40
function nameKey(value: string) {
  return value.normalize('NFKC').trim().replace(/\s+/g, ' ').toLocaleLowerCase()
}
function hotwordStats(lists: HotwordList[], ids: Set<string>) {
  const unique = new Map<string, string>()
  lists
    .filter((item) => ids.has(item.id))
    .forEach((item) =>
      item.terms.forEach((raw) => {
        const term = raw.normalize('NFKC').trim().replace(/\s+/g, ' ')
        const key = term.toLocaleLowerCase()
        if (term && !unique.has(key)) unique.set(key, term)
      }),
    )
  const terms = [...unique.values()]
  return {
    terms: terms.length,
    promptChars: terms.length ? `Vocabulary: ${terms.join(', ')}.`.length : 0,
  }
}
function hotwordLimitIssue(listCount: number, stats: { terms: number; promptChars: number }, limits: HotwordLibraryCapability | undefined, prefix: string, t: TFunction) {
  const violations: string[] = []
  const maxLists = limits?.max_selected_lists || 8
  const maxTerms = limits?.max_selected_terms || 500
  const maxPromptChars = limits?.max_prompt_chars || 8000
  if (listCount > maxLists) violations.push(t('asr.hotwords.listLimit', { count: listCount, max: maxLists }))
  if (stats.terms > maxTerms) violations.push(t('asr.hotwords.termLimit', { count: stats.terms, max: maxTerms }))
  if (stats.promptChars > maxPromptChars) violations.push(t('asr.hotwords.characterLimit', { count: stats.promptChars, max: maxPromptChars }))
  return violations.length ? t('asr.hotwords.limitIssue', { prefix, violations: violations.join(t('common.separator.semicolon')) }) : ''
}

const SegmentRow = memo(function SegmentRow({ segment, speaker, active, currentTime, onPlay, onSeekWord, selected, selectionSpeaker, onToggle, onRename }: { segment: Segment; speaker?: Speaker; active: boolean; currentTime: number; onPlay: (segment: Segment) => void; onSeekWord: (start: number) => void; selected: boolean; selectionSpeaker?: string; onToggle: (segment: Segment) => void; onRename: (segment: Segment) => void }) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const disabled = Boolean(selectionSpeaker && selectionSpeaker !== segment.speaker)
  const match = speaker?.label_source === 'voiceprint' ? speaker.voiceprint_match : undefined
  return (
    <article className={`${active ? 'active ' : ''}${selected ? 'selected-segment' : ''}`}>
      <div className="segment-select">
        <input type="checkbox" checked={selected} disabled={disabled} aria-label={t('asr.segment.select', { number: segment.id + 1 })} onChange={() => onToggle(segment)} />
      </div>
      <div className="segment-time">
        <b>{formatTime(segment.start)}</b>
        <span>—</span>
        <b>{formatTime(segment.end)}</b>
      </div>
      <button className={`speaker s${Number(segment.speaker.split('_')[1]) % 4}`} aria-label={t('asr.segment.renameNamed', { name: segment.speaker_label })} title={t('asr.segment.renameHelp')} onClick={() => onRename(segment)}>
        <i />
        <span className="speaker-identity">
          <b>{match?.name || segment.speaker_label}</b>
          {match?.note ? <small>（{match.note}）</small> : null}
        </span>
        <Pencil size={12} />
      </button>
      <div className="segment-copy">
        {expanded && segment.words?.length ? (
          <div className="words">
            {segment.words.map((word, index) => (
              <button className={active && currentTime >= word.start && currentTime < word.end ? 'active' : ''} key={index} onClick={() => onSeekWord(word.start)}>
                {word.text}
                <small>{formatTime(word.start)}</small>
              </button>
            ))}
          </div>
        ) : (
          <p>{segment.text}</p>
        )}
        {segment.words?.length ? (
          <button className="word-toggle" onClick={() => setExpanded((value) => !value)}>
            {expanded ? t('asr.segment.collapseWords') : t('asr.segment.viewWords', { count: segment.words.length })}
          </button>
        ) : null}
      </div>
      <button className="icon-button" aria-label={t('asr.segment.play', { number: segment.id + 1 })} onClick={() => onPlay(segment)}>
        <Play size={17} />
      </button>
    </article>
  )
})

type Props = {
  jobs: JobSummary[]
  jobDetails: Record<string, JobDetailResource>
  loadJobDetail: (job: JobSummary, force?: boolean) => void
  onJobSubmitted: (job: Job) => void
  onJobResultUpdated: (jobId: string, result: JobResult) => void
  selectedJobId?: string
  onSelect: (job: JobSummary) => void
  gpuAvailable?: boolean
  defaultComputeDevice: ComputeDevice
  maxSpeakers: number
  maxUploadBytes?: number
  asrLanguages?: string[]
  alignerLanguages?: string[]
  asrModels: AsrModelCapability[]
  hotwordLists: HotwordList[]
  hotwordsState: ResourceState
  hotwordLimits?: HotwordLibraryCapability
  voiceprints: VoiceprintPerson[]
  refreshVoiceprints: () => Promise<void>
  refreshPeopleAndHotwords: () => Promise<void>
  revealRequest?: ResultRevealRequest
  onRevealHandled: (token: number) => void
}

export function AsrPage({ jobs, jobDetails, loadJobDetail, onJobSubmitted, onJobResultUpdated, selectedJobId, onSelect, gpuAvailable, defaultComputeDevice, maxSpeakers, maxUploadBytes, asrLanguages = publicAsrLanguages, alignerLanguages = publicAlignerLanguages, asrModels, hotwordLists, hotwordsState, hotwordLimits, voiceprints, refreshVoiceprints, refreshPeopleAndHotwords, revealRequest, onRevealHandled }: Props) {
  const { t, i18n } = useTranslation()
  const [file, setFile] = useState<File>()
  const [preferences, setPreferences] = useState<AsrPreferences>(() => loadAsrPreferences(maxSpeakers,defaultComputeDevice))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [uploadProgress,setUploadProgress]=useState<UploadProgress>()
  const [mediaError, setMediaError] = useState('')
  const [query, setQuery] = useState('')
  const deferredQuery = useDeferredValue(query)
  const [speakerFilter, setSpeakerFilter] = useState('all')
  const [visibleSegmentCount, setVisibleSegmentCount] = useState(segmentBatchSize)
  const [selectedSegments, setSelectedSegments] = useState<Set<number>>(() => new Set())
  const [renameSpeaker, setRenameSpeaker] = useState<{
    id: string
    label: string
  }>()
  const [renameValue, setRenameValue] = useState('')
  const [libraryOpen, setLibraryOpen] = useState(false)
  const [targetPersonId, setTargetPersonId] = useState('')
  const [newPersonName, setNewPersonName] = useState('')
  const [newPersonNote, setNewPersonNote] = useState('')
  const [newPersonHotword, setNewPersonHotword] = useState(true)
  const [currentTime, setCurrentTime] = useState(0)
  const [playing, setPlaying] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const audio = useRef<HTMLAudioElement>(null)
  const resultPanel = useRef<HTMLElement>(null)
  const segmentList = useRef<HTMLDivElement>(null)
  const loadMoreSentinel = useRef<HTMLDivElement>(null)
  const stopAt = useRef<number | undefined>(undefined)
  const uploadController=useRef<AbortController|undefined>(undefined)
  const asrJobs = useMemo(() => jobs.filter((job) => job.kind === 'asr'), [jobs])
  const selectedSummary = asrJobs.find((job) => job.id === selectedJobId && job.state === 'succeeded') || asrJobs.find((job) => job.state === 'succeeded')
  const visibleJobs = useMemo(() => visibleWorkspaceJobs(asrJobs, selectedSummary?.id), [asrJobs, selectedSummary?.id])
  const detail = selectedSummary ? jobDetails[selectedSummary.id] : undefined
  const selected = detail?.job
  const result = selected?.result
  const speakerById = useMemo(() => new Map((result?.speakers || []).map((speaker) => [speaker.id, speaker])), [result?.speakers])
  const alignmentDowngraded = Boolean(selected?.request.language === 'Auto' && selected.request.align === true && result?.timestamp_precision === 'segment' && result.language && !alignerLanguages.includes(result.language))
  const duration = result?.duration || 0
  const normalizedQuery = deferredQuery.trim().toLocaleLowerCase()
  const segments = useMemo(() => {
    const values = result?.segments || []
    return values.filter((segment) => (!normalizedQuery || segment.text.toLocaleLowerCase().includes(normalizedQuery)) && (speakerFilter === 'all' || segment.speaker === speakerFilter))
  }, [result?.segments, normalizedQuery, speakerFilter])
  const visibleSegments = useMemo(() => segments.slice(0, visibleSegmentCount), [segments, visibleSegmentCount])
  const activeSegmentIndex = useMemo(() => segments.findIndex((segment) => currentTime >= segment.start && currentTime < segment.end), [currentTime, segments])
  const selectionSpeaker = useMemo(() => result?.segments?.find((segment) => selectedSegments.has(segment.id))?.speaker, [result?.segments, selectedSegments])
  const selectionLabel = result?.speakers?.find((speaker) => speaker.id === selectionSpeaker)?.label || selectionSpeaker?.replace('_', ' ') || ''
  const models: AsrModelCapability[] = asrModels.length
    ? asrModels
    : [
        {
          id: 'qwen3-asr-0.6b',
          name: 'Qwen3-ASR 0.6B',
          default: true,
          installed: true,
          installation_state: 'installed',
          revision: '',
          compute_devices: [
            {
              id: 'cpu',
              available: true,
              default: gpuAvailable === false,
              quantized: false,
              label: 'CPU',
              precision: 'FP32',
            },
            {
              id: 'gpu',
              available: gpuAvailable !== false,
              default: gpuAvailable !== false,
              quantized: false,
              label: 'GPU',
              precision: 'BF16',
            },
          ],
        },
      ]
  const chosenModel = models.find((item) => item.id === preferences.model) || models.find((item) => item.default) || models[0]
  const gpuCapability = chosenModel.compute_devices.find((item) => item.id === 'gpu')
  const { language, speakerCount: speakers, align, useVoiceprints, computeDevice, accelerateSingleTask } = preferences
  const model = chosenModel.id
  const effectiveComputeDevice: ComputeDevice = computeDevice === 'gpu' && gpuCapability?.available === false ? 'cpu' : computeDevice
  const selectedHotwordIds = useMemo(() => new Set(preferences.hotwordListIds), [preferences.hotwordListIds])
  const selectedHotwordStats = useMemo(() => hotwordStats(hotwordLists, selectedHotwordIds), [hotwordLists, selectedHotwordIds])
  const selectedHotwordIssue = hotwordLimitIssue(selectedHotwordIds.size, selectedHotwordStats, hotwordLimits, t('asr.hotwords.currentSelection'), t)
  const fileLimitError=file?uploadLimitMessage(file,maxUploadBytes,t,i18n.resolvedLanguage||'zh-CN'):''
  const submitLabel=uploadProgress?.phase==='creating'?t('asr.submit.creating'):uploadProgress?.phase==='uploading'&&uploadProgress.percent!==undefined?t('asr.submit.uploading',{percent:uploadProgress.percent}):uploadProgress?t('asr.submit.preparing'):busy?t('common.states.processing'):t('asr.submit.start')
  const hotwordSelectionIssue = (item: HotwordList) => {
    if (selectedHotwordIds.has(item.id)) return ''
    if (!item.term_count) return t('asr.hotwords.emptySystemList')
    const ids = new Set(selectedHotwordIds)
    ids.add(item.id)
    return hotwordLimitIssue(ids.size, hotwordStats(hotwordLists, ids), hotwordLimits, t('asr.hotwords.selectionWouldExceed',{name:item.name}), t)
  }
  const updatePreference = <K extends keyof AsrPreferences>(key: K, value: AsrPreferences[K]) =>
    setPreferences((current) => {
      const next = { ...current, [key]: value }
      saveAsrPreferences(next)
      return next
    })

  useEffect(() => {
    if (speakers !== 'auto' && Number(speakers) > maxSpeakers) updatePreference('speakerCount', 'auto')
  }, [maxSpeakers, speakers])
  useEffect(() => {
    if (hotwordsState !== 'ready') return
    const selectableIds = new Set(hotwordLists.filter((item) => item.term_count > 0).map((item) => item.id))
    setPreferences((current) => {
      const hotwordListIds = current.hotwordListIds.filter((id) => selectableIds.has(id))
      if (hotwordListIds.length === current.hotwordListIds.length) return current
      const next = { ...current, hotwordListIds }
      saveAsrPreferences(next)
      return next
    })
  }, [hotwordLists, hotwordsState])
  useEffect(() => {
    if (selectedSummary) loadJobDetail(selectedSummary)
  }, [loadJobDetail, selectedSummary])
  useEffect(() => {
    const player = audio.current
    if (player) {
      player.pause()
      player.currentTime = 0
      player.load()
    }
    stopAt.current = undefined
    setCurrentTime(0)
    setPlaying(false)
    setMediaError('')
    setSelectedSegments(new Set())
    setSpeakerFilter('all')
  }, [selectedSummary?.id])
  useEffect(() => setVisibleSegmentCount(segmentBatchSize), [normalizedQuery, selectedSummary?.id, speakerFilter])
  useEffect(() => {
    if (activeSegmentIndex < visibleSegmentCount) return
    setVisibleSegmentCount(Math.ceil((activeSegmentIndex + 1) / segmentBatchSize) * segmentBatchSize)
  }, [activeSegmentIndex, visibleSegmentCount])
  const loadMoreSegments = useCallback(() => setVisibleSegmentCount((current) => Math.min(segments.length, current + segmentBatchSize)), [segments.length])
  useEffect(() => {
    const root = segmentList.current
    const target = loadMoreSentinel.current
    if (!root || !target || visibleSegmentCount >= segments.length) return
    const observer = new IntersectionObserver((entries) => {if(entries.some((entry) => entry.isIntersecting))loadMoreSegments()}, {root,rootMargin:'0px 0px 360px'})
    observer.observe(target)
    return () => observer.disconnect()
  }, [loadMoreSegments, segments.length, visibleSegmentCount])
  useEffect(() => {
    if (!revealRequest || revealRequest.jobId !== selectedSummary?.id) return
    const frame = requestAnimationFrame(() => {
      if (matchMedia('(max-width: 900px)').matches)
        resultPanel.current?.scrollIntoView({
          block: 'start',
          behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
        })
      onRevealHandled(revealRequest.token)
    })
    return () => cancelAnimationFrame(frame)
  }, [onRevealHandled, revealRequest, selectedSummary?.id])

  const submit = async () => {
    if (selectedHotwordIssue) {
      setError(selectedHotwordIssue)
      return
    }
    if (!file) {
      input.current?.click()
      return
    }
    if(fileLimitError){setError(fileLimitError);return}
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const controller=new AbortController()
      uploadController.current=controller
      const data = new FormData()
      data.set('file', file)
      data.set('model', model)
      data.set('language', language)
      data.set('speaker_count', speakers)
      data.set('diarize', 'true')
      data.set('align', String(align))
      data.set('use_voiceprint_library', String(useVoiceprints))
      data.set('export_formats', 'json,srt,vtt,txt')
      data.set('compute_device', effectiveComputeDevice)
      data.set('accelerate_single_task', String(accelerateSingleTask))
      if (selectedHotwordIds.size) data.set('hotword_list_ids', [...selectedHotwordIds].join(','))
      const job = await api.submitAsr(data,{signal:controller.signal,onProgress:setUploadProgress})
      onJobSubmitted(job)
      setFile(undefined)
      setNotice(t('asr.notices.submitted'))
      if (input.current) input.current.value = ''
    } catch (cause) {
      if(isUploadCancelled(cause))setNotice(t('asr.notices.uploadCancelled'))
      else setError((cause as Error).message)
    } finally {
      uploadController.current=undefined
      setUploadProgress(undefined)
      setBusy(false)
    }
  }
  const play = async () => {
    const player = audio.current
    if (!player) return
    setMediaError('')
    stopAt.current = undefined
    try {
      if (player.paused) await player.play()
      else player.pause()
    } catch {
      setMediaError(t('asr.results.playbackError'))
    }
  }
  const playSegment = useCallback(async (segment: Segment) => {
    const player = audio.current
    if (!player) return
    setMediaError('')
    player.currentTime = segment.start
    setCurrentTime(segment.start)
    stopAt.current = segment.end
    try {
      await player.play()
    } catch {
      setMediaError(t('asr.results.playbackError'))
    }
  }, [])
  const seekWord = useCallback((start: number) => {
    const player = audio.current
    if (player) {
      player.currentTime = start
      setCurrentTime(start)
    }
  }, [])
  const seek = useCallback((ratio: number) => {
    const player = audio.current
    if (!player || !duration) return
    stopAt.current = undefined
    player.currentTime = Math.max(0, Math.min(duration, ratio * duration))
    setCurrentTime(player.currentTime)
  }, [duration])
  const chooseFile=(next?:File)=>{if(!next)return;setFile(next);setError('');setNotice('')}
  const chooseDropped = (event: React.DragEvent<HTMLButtonElement>) => {
    event.preventDefault()
    const next = event.dataTransfer.files[0]
    chooseFile(next)
  }
  const toggleSegment = useCallback(
    (segment: Segment) =>
      setSelectedSegments((current) => {
        const next = new Set(current)
        if (next.has(segment.id)) next.delete(segment.id)
        else next.add(segment.id)
        return next
      }),
    [],
  )
  const changeSpeakerFilter = (next: string) => {
    if (selectionSpeaker && next !== 'all' && next !== selectionSpeaker) setSelectedSegments(new Set())
    setSpeakerFilter(next)
  }
  const openRename = useCallback((segment: Segment) => {
    setRenameSpeaker({ id: segment.speaker, label: segment.speaker_label })
    setRenameValue(segment.speaker_label)
  }, [])
  const saveRename = async () => {
    if (!selected || !renameSpeaker || !renameValue.trim()) return
    setBusy(true)
    setError('')
    try {
      const next = await api.renameSpeaker(selected.id, renameSpeaker.id, renameValue.trim())
      onJobResultUpdated(selected.id, next)
      setRenameSpeaker(undefined)
      setNotice(t('asr.notices.renamed',{from:renameSpeaker.label,to:renameValue.trim()}))
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const openLibrary = () => {
    const speaker = selectionSpeaker ? speakerById.get(selectionSpeaker) : undefined
    const matchedId = speaker?.label_source === 'voiceprint' ? speaker.voiceprint_match?.person_id : undefined
    const match = voiceprints.find((person) => person.id === matchedId) || voiceprints.find((person) => nameKey(person.name) === nameKey(selectionLabel))
    setTargetPersonId(match?.id || '')
    setNewPersonName(match ? '' : /^Speaker[ _]\d+$/i.test(selectionLabel) ? '' : selectionLabel)
    setNewPersonNote('')
    setNewPersonHotword(true)
    setLibraryOpen(true)
  }
  const addToLibrary = async () => {
    if (!selected || !selectedSegments.size) return
    setBusy(true)
    setError('')
    try {
      let personId = targetPersonId
      let created = false
      if (!personId) {
        if (!newPersonName.trim()) throw new Error(t('asr.library.nameRequired'))
        const person = await api.addVoiceprintPerson(newPersonName.trim(), newPersonNote.trim() || null, newPersonHotword)
        personId = person.id
        created = true
      }
      await api.addAsrSamples(personId, selected.id, [...selectedSegments])
      await (created ? refreshPeopleAndHotwords() : refreshVoiceprints())
      setSelectedSegments(new Set())
      setLibraryOpen(false)
      setNotice(t('asr.notices.addedToVoiceprints'))
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const resetPreferences = () => {
    const next = { ...defaultAsrPreferences, computeDevice: defaultComputeDevice, hotwordListIds: [] }
    clearAsrPreferences()
    saveAsrPreferences(next)
    setPreferences(next)
    setNotice(t('asr.defaultsRestored'))
    setError('')
  }

  return (
    <div className="workbench hud-page">
      <aside className="control-panel" data-module="AUDIO_INPUT / UP_01">
        <div className="control-heading">
          <div>
            <h1>{t('asr.title')}</h1>
            <p className="subtitle">{t('asr.subtitle')}</p>
          </div>
          <button className="reset-settings" type="button" onClick={resetPreferences}>
            <RotateCcw size={14} />
            {t('asr.restoreDefaults')}
          </button>
        </div>
        <input ref={input} hidden type="file" accept="audio/*,video/*" disabled={busy} onChange={(event) => chooseFile(event.target.files?.[0])} />
        <button className="dropzone" disabled={busy} onClick={() => input.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={chooseDropped}>
          <UploadCloud size={44} />
          <b>{file?.name || t('asr.dropzone.title')}</b>
          <span>{file ? t('asr.dropzone.reselect') : t('asr.dropzone.formats')}</span>
        </button>
        <label>
          {t('asr.model')}
          <select aria-label={t('asr.model')} value={model} onChange={(event) => updatePreference('model', event.target.value)}>
            {models.map((item) => (
              <option key={item.id} value={item.id} disabled={!item.installed}>
                {item.name}
                {item.installed ? '' : t('asr.notInstalled')}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t('asr.language')}
          <select value={language} onChange={(event) => updatePreference('language', event.target.value)}>
            {asrLanguages.map((item) => (
              <option key={item} value={item}>
                {item === 'Auto' ? t('common.languages.Auto') : item}
              </option>
            ))}
          </select>
        </label>
        <fieldset className="hotword-picker">
          <legend>{t('asr.hotwords.label')}</legend>
          {hotwordLists.length ? (
            hotwordLists.map((item) => {
              const issue = hotwordSelectionIssue(item)
              const issueId = issue ? `hotword-option-issue-${item.id}` : undefined
              return (
                <label key={item.id} className={issue ? 'hotword-option blocked' : 'hotword-option'} title={issue || undefined}>
                  <input
                    type="checkbox"
                    checked={selectedHotwordIds.has(item.id)}
                    disabled={Boolean(issue)}
                    aria-describedby={issueId}
                    onChange={() => setPreferences((current) => {
                      const selected = new Set(current.hotwordListIds)
                      selected.has(item.id) ? selected.delete(item.id) : selected.add(item.id)
                      const next = { ...current, hotwordListIds: [...selected] }
                      saveAsrPreferences(next)
                      return next
                    })}
                  />
                  <span>
                    {item.name}
                    {item.kind === 'system' ? <em>{t('hotwords.system')}</em> : null}
                  </span>
                  <small className="hotword-option-count">{t('asr.hotwords.termCount',{count:item.term_count})}</small>
                  {issue ? (
                    <small id={issueId} className="hotword-option-issue">
                      {issue}
                    </small>
                  ) : null}
                </label>
              )
            })
          ) : (
            <small className="device-hint">{t('asr.hotwords.emptyHelp')}</small>
          )}
          {hotwordLists.length ? (
            <small className={`hotword-selection-summary${selectedHotwordIssue ? ' invalid' : ''}`}>
              {t('asr.hotwords.summary',{lists:selectedHotwordIds.size,maxLists:hotwordLimits?.max_selected_lists||8,terms:selectedHotwordStats.terms,maxTerms:hotwordLimits?.max_selected_terms||500,characters:selectedHotwordStats.promptChars,maxCharacters:hotwordLimits?.max_prompt_chars||8000})}
            </small>
          ) : null}
          {selectedHotwordIssue ? (
            <p id="asr-hotword-limit-error" className="hotword-limit-error" role="alert">
              {selectedHotwordIssue} {t('asr.hotwords.removeOne')}
            </p>
          ) : null}
        </fieldset>
        <label>
          {t('asr.speakerCount')}
          <select value={speakers} onChange={(event) => updatePreference('speakerCount', event.target.value)}>
            <option value="auto">{t('asr.auto')}</option>
            {Array.from({ length: maxSpeakers }, (_, index) => index + 1).map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
        <label>
          {t('asr.timestamps.label')}
          <select value={align ? 'word' : 'segment'} onChange={(event) => updatePreference('align', event.target.value === 'word')}>
            <option value="word">{t('asr.timestamps.word')}</option>
            <option value="segment">{t('asr.timestamps.segment')}</option>
          </select>
        </label>
        <label className="toggle-label">
          <input type="checkbox" checked={useVoiceprints} onChange={(event) => updatePreference('useVoiceprints', event.target.checked)} />
          <span>{t('asr.voiceprints.label')}</span>
          <small className="device-hint">{t('asr.voiceprints.help')}</small>
        </label>
        <label>
          {t('asr.computeDevice')}
          <select aria-label={t('asr.computeDeviceAria')} value={effectiveComputeDevice} onChange={(event) => updatePreference('computeDevice', event.target.value as ComputeDevice)}>
            <option value="gpu" disabled={gpuCapability?.available === false}>
              GPU · BF16
              {gpuCapability?.available === false ? t('asr.modelUnavailable') : t('asr.defaultMark')}
            </option>
            <option value="cpu">CPU · FP32</option>
          </select>
          {computeDevice === 'gpu' && effectiveComputeDevice === 'cpu' ? <small className="device-hint">{computeUnavailableReason(gpuCapability,t)}</small> : <small className="device-hint">{t('asr.computeHelp')}</small>}
        </label>
        <div className="acceleration-control">
          <label>
            <input type="checkbox" checked={accelerateSingleTask} onChange={(event) => updatePreference('accelerateSingleTask', event.target.checked)} />
            <span>{t('asr.acceleration')}</span>
          </label>
          <InfoTooltip id="asr-acceleration-help" text={t('asr.accelerationHelp')} />
        </div>
        <label>
          {t('asr.exportFormat')}<div className="select-like">JSON · SRT · VTT · TXT</div>
        </label>
        <div className="submission-actions">
          {error||fileLimitError ? (
            <p className="error" role="alert">
              {error||fileLimitError}
            </p>
          ) : null}
          {notice ? (
            <p className="notice" role="status">
              {notice}
            </p>
          ) : null}
          {uploadProgress?<SubmissionProgress label={t('asr.uploadLabel')} progress={uploadProgress} onCancel={()=>uploadController.current?.abort()}/>:null}
          <button className="primary" disabled={busy || !chosenModel.installed || Boolean(selectedHotwordIssue) || Boolean(fileLimitError)} aria-describedby={selectedHotwordIssue ? 'asr-hotword-limit-error' : undefined} onClick={submit}>
            <Play size={18} />
            {submitLabel}
          </button>
        </div>
        <section className="aside-jobs">
          <h2>{t('asr.taskList')}</h2>
          {visibleJobs.map((job) => (
            <JobMini key={job.id} job={job} isSelected={job.id === selectedSummary?.id} onOpen={(item) => item.state === 'succeeded' && onSelect(item)} />
          ))}
        </section>
      </aside>
      <section ref={resultPanel} className="result-panel" data-module="TRANSCRIPT_CORE / TRN_01">
        {selectedSummary && detail?.state === 'loading' ? (
          <div className="empty" role="status">
            <FileAudio size={52} />
            <h2>{t('asr.results.loading')}</h2>
            <p>{t('asr.results.loadingHelp')}</p>
          </div>
        ) : selectedSummary && detail?.state === 'error' ? (
          <div className="empty" role="alert">
            <FileAudio size={52} />
            <h2>{t('asr.results.loadFailed')}</h2>
            <p>{detail.error || t('asr.results.tryLater')}</p>
            <button onClick={() => loadJobDetail(selectedSummary, true)}>{t('common.actions.reload')}</button>
          </div>
        ) : selected && result ? (
          <>
            <div className="result-head">
              <span>
                <FileAudio size={20} />
                {selected.display_name}
              </span>
              <span>
                {String(result.model_name || selected.request.model || 'Qwen3-ASR')} · {formatTime(duration)} · {(result.compute_device || selected.request.compute_device || 'gpu').toString().toUpperCase()} {result.precision || ''}
              </span>
              <div className="artifact-links">
                {result.artifacts?.map((item) => (
                  <a key={item.name} className="button" href={artifactUrl(selected.id, item.name)} title={t('asr.results.downloadNamed',{name:item.name})}>
                    <Download size={15} />
                    {item.name.split('.').pop()?.toUpperCase()}
                  </a>
                ))}
              </div>
            </div>
            <audio
              ref={audio}
              className="sr-only"
              preload="metadata"
              src={selected.source_url || sourceUrl(selected.id)}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onEnded={() => setPlaying(false)}
              onError={() => setMediaError(t('asr.results.playbackError'))}
              onTimeUpdate={(event) => {
                const value = event.currentTarget.currentTime
                setCurrentTime(value)
                if (stopAt.current !== undefined && value >= stopAt.current - 0.03) {
                  event.currentTarget.pause()
                  stopAt.current = undefined
                }
              }}
            />
            <div className="wave-area">
              <Waveform peaks={result.waveform} currentTime={currentTime} duration={duration} onSeek={seek} />
              <div className="time-ruler">
                <span>00:00</span>
                <span>{formatTime(duration / 2, false)}</span>
                <span>{formatTime(duration, false)}</span>
              </div>
            </div>
            <div className="player-row">
              <button className="round" aria-label={playing ? t('asr.results.pause') : t('asr.results.play')} onClick={play}>
                {playing ? <Pause /> : <Play />}
              </button>
              <strong>{formatTime(currentTime)}</strong>
              <span>/ {formatTime(duration)}</span>
              <span className="grow" />
              <span>
                {result.language} · {result.timestamp_precision === 'word_or_character' ? t('asr.results.wordAlignment') : t('asr.results.segmentTimestamps')}
              </span>
            </div>
            {alignmentDowngraded ? (
              <p className="notice alignment-notice" role="note">
                {t('asr.results.alignmentDowngraded',{language:result.language})}
              </p>
            ) : null}
            {mediaError ? (
              <p className="media-error">
                {mediaError} <a href={sourceUrl(selected.id, true)}>{t('asr.results.downloadSource')}</a>
              </p>
            ) : null}
            <div className="transcript-tools">
              <Search size={18} />
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t('asr.results.search')} />
              <select aria-label={t('asr.results.filterSpeaker')} value={speakerFilter} onChange={(event) => changeSpeakerFilter(event.target.value)}>
                <option value="all">{t('asr.results.allSpeakers')}</option>
                {result.speakers?.map((speaker) => (
                  <option key={speaker.id} value={speaker.id}>
                    {speaker.label}
                  </option>
                ))}
              </select>
              <span>{t('asr.results.segmentSummary',{visible:Math.min(visibleSegmentCount,segments.length),matches:segments.length,total:result.segments?.length||0})}</span>
            </div>
            {selectedSegments.size ? (
              <div className="segment-selection" aria-label={t('asr.results.selectionActions')}>
                <b>
                  {t('asr.results.selectedSegments',{count:selectedSegments.size,speaker:selectionLabel})}
                </b>
                <button onClick={openLibrary}>
                  <UserPlus size={16} />
                  {t('asr.results.addToVoiceprints')}
                </button>
                <button className="icon-button" aria-label={t('asr.results.clearSelection')} onClick={() => setSelectedSegments(new Set())}>
                  <X />
                </button>
              </div>
            ) : null}
            <div ref={segmentList} className="segments" aria-busy={query!==deferredQuery}>
              {segments.length ? (
                <>{visibleSegments.map((segment) => {
                  const active = currentTime >= segment.start && currentTime < segment.end
                  return <SegmentRow key={segment.id} segment={segment} speaker={speakerById.get(segment.speaker)} active={active} currentTime={active ? currentTime : -1} onPlay={playSegment} onSeekWord={seekWord} selected={selectedSegments.has(segment.id)} selectionSpeaker={selectionSpeaker} onToggle={toggleSegment} onRename={openRename} />
                })}{visibleSegments.length<segments.length?<div ref={loadMoreSentinel} className="segment-load-more"><span>{t('asr.results.matchSummary',{visible:visibleSegments.length,total:segments.length})}</span><button type="button" className="button" onClick={loadMoreSegments}>{t('asr.results.loadMore')}</button></div>:null}</>
              ) : (
                <div className="empty search-empty">
                  <Search size={38} />
                  <p>{t('asr.results.noMatches')}</p>
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="empty">
            <FileAudio size={52} />
            <h2>{t('asr.results.empty')}</h2>
            <p>{t('asr.results.emptyHelp')}</p>
          </div>
        )}
      </section>
      {renameSpeaker ? (
        <Modal title={t('asr.rename.title')} closeLabel={t('asr.rename.close')} onClose={() => setRenameSpeaker(undefined)}>
          <p>{t('asr.rename.help',{name:renameSpeaker.label})}</p>
          <label>
            {t('asr.rename.newName')}
            <input value={renameValue} maxLength={80} onChange={(event) => setRenameValue(event.target.value)} />
          </label>
          <button className="primary" disabled={busy || !renameValue.trim()} onClick={saveRename}>
            {t('asr.rename.save')}
          </button>
        </Modal>
      ) : null}
      {libraryOpen ? (
        <Modal title={t('asr.library.title')} closeLabel={t('asr.library.close')} onClose={() => setLibraryOpen(false)}>
          <p>
            {t('asr.library.help',{count:selectedSegments.size,speaker:selectionLabel})}
          </p>
          {voiceprints.length ? (
            <label>
              {t('asr.library.person')}
              <select value={targetPersonId} onChange={(event) => setTargetPersonId(event.target.value)}>
                <option value="">{t('asr.library.newPerson')}</option>
                {voiceprints.map((person) => (
                  <option key={person.id} value={person.id}>
                    {person.name}
                    {person.note ? `(${person.note})` : ''} · {t('voiceprints.sampleCount',{count:person.sample_count})}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {!targetPersonId ? (
            <>
              <label>
                {t('asr.library.newPersonName')}
                <input value={newPersonName} maxLength={80} placeholder={t('asr.library.namePlaceholder')} onChange={(event) => setNewPersonName(event.target.value)} />
              </label>
              <label>
                {t('voiceprints.noteOptional')}
                <input value={newPersonNote} maxLength={20} placeholder={t('voiceprints.notePlaceholder')} onChange={(event) => setNewPersonNote(event.target.value)} />
              </label>
              <label className="toggle-label person-hotword-toggle">
                <input type="checkbox" checked={newPersonHotword} onChange={(event) => setNewPersonHotword(event.target.checked)} />
                <span>{t('voiceprints.addToHotwords')}</span>
                <small>{t('voiceprints.addToHotwordsHelp')}</small>
              </label>
            </>
          ) : (
            <p className="notice">
              {t('asr.library.matched')}
              {voiceprints.find((person) => person.id === targetPersonId)?.name}
            </p>
          )}
          <button className="primary" disabled={busy || (!targetPersonId && !newPersonName.trim())} onClick={addToLibrary}>
            {busy ? t('voiceprints.saving') : t('asr.library.confirm')}
          </button>
        </Modal>
      ) : null}
    </div>
  )
}
