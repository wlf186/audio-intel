import { memo, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { Download, FileAudio, Pause, Pencil, Play, RotateCcw, Search, UploadCloud, UserPlus, X } from 'lucide-react'
import { api, artifactUrl, formatTime, sourceUrl } from '../lib/api'
import type { AsrModelCapability, ComputeDevice, HotwordLibraryCapability, HotwordList, Job, JobDetailResource, JobResult, JobSummary, ResultRevealRequest, Segment, Speaker, VoiceprintPerson } from '../lib/types'
import { Waveform } from '../components/Waveform'
import { JobMini } from '../components/JobMini'
import { Modal } from '../components/Modal'
import { InfoTooltip } from '../components/InfoTooltip'
import { clearAsrPreferences, defaultAsrPreferences, loadAsrPreferences, publicAlignerLanguages, publicAsrLanguages, saveAsrPreferences, type AsrPreferences } from '../lib/preferences'
import { visibleWorkspaceJobs } from '../lib/jobs'
import { computeUnavailableReason } from '../lib/presentation'
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
function hotwordLimitIssue(listCount: number, stats: { terms: number; promptChars: number }, limits: HotwordLibraryCapability | undefined, prefix: string) {
  const violations: string[] = []
  const maxLists = limits?.max_selected_lists || 8
  const maxTerms = limits?.max_selected_terms || 500
  const maxPromptChars = limits?.max_prompt_chars || 8000
  if (listCount > maxLists) violations.push(`${listCount} 个词表（上限 ${maxLists} 个）`)
  if (stats.terms > maxTerms) violations.push(`${stats.terms} 个唯一热词（上限 ${maxTerms} 个）`)
  if (stats.promptChars > maxPromptChars) violations.push(`${stats.promptChars} 个提示字符（上限 ${maxPromptChars} 个）`)
  return violations.length ? `${prefix}：${violations.join('；')}。` : ''
}

const SegmentRow = memo(function SegmentRow({ segment, speaker, active, currentTime, onPlay, onSeekWord, selected, selectionSpeaker, onToggle, onRename }: { segment: Segment; speaker?: Speaker; active: boolean; currentTime: number; onPlay: (segment: Segment) => void; onSeekWord: (start: number) => void; selected: boolean; selectionSpeaker?: string; onToggle: (segment: Segment) => void; onRename: (segment: Segment) => void }) {
  const [expanded, setExpanded] = useState(false)
  const disabled = Boolean(selectionSpeaker && selectionSpeaker !== segment.speaker)
  const match = speaker?.label_source === 'voiceprint' ? speaker.voiceprint_match : undefined
  return (
    <article className={`${active ? 'active ' : ''}${selected ? 'selected-segment' : ''}`}>
      <div className="segment-select">
        <input type="checkbox" checked={selected} disabled={disabled} aria-label={`选择片段 ${segment.id + 1}`} onChange={() => onToggle(segment)} />
      </div>
      <div className="segment-time">
        <b>{formatTime(segment.start)}</b>
        <span>—</span>
        <b>{formatTime(segment.end)}</b>
      </div>
      <button className={`speaker s${Number(segment.speaker.split('_')[1]) % 4}`} aria-label={`重命名说话人 ${segment.speaker_label}`} title="重命名当前任务中的说话人" onClick={() => onRename(segment)}>
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
            {expanded ? '收起字词时间戳' : `查看 ${segment.words.length} 个字词时间戳`}
          </button>
        ) : null}
      </div>
      <button className="icon-button" aria-label={`播放片段 ${segment.id + 1}`} onClick={() => onPlay(segment)}>
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
  maxSpeakers: number
  asrLanguages?: string[]
  alignerLanguages?: string[]
  asrModels: AsrModelCapability[]
  hotwordLists: HotwordList[]
  hotwordLimits?: HotwordLibraryCapability
  voiceprints: VoiceprintPerson[]
  refreshVoiceprints: () => Promise<void>
  refreshPeopleAndHotwords: () => Promise<void>
  revealRequest?: ResultRevealRequest
  onRevealHandled: (token: number) => void
}

export function AsrPage({ jobs, jobDetails, loadJobDetail, onJobSubmitted, onJobResultUpdated, selectedJobId, onSelect, gpuAvailable, maxSpeakers, asrLanguages = publicAsrLanguages, alignerLanguages = publicAlignerLanguages, asrModels, hotwordLists, hotwordLimits, voiceprints, refreshVoiceprints, refreshPeopleAndHotwords, revealRequest, onRevealHandled }: Props) {
  const [file, setFile] = useState<File>()
  const [preferences, setPreferences] = useState<AsrPreferences>(() => loadAsrPreferences(maxSpeakers))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
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
  const [selectedHotwordIds, setSelectedHotwordIds] = useState<Set<string>>(() => new Set())
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
  const selectedHotwordStats = useMemo(() => hotwordStats(hotwordLists, selectedHotwordIds), [hotwordLists, selectedHotwordIds])
  const selectedHotwordIssue = hotwordLimitIssue(selectedHotwordIds.size, selectedHotwordStats, hotwordLimits, '当前热词选择已超出单次任务限制')
  const hotwordSelectionIssue = (item: HotwordList) => {
    if (selectedHotwordIds.has(item.id)) return ''
    if (!item.term_count) return '该系统词表当前没有已启用的人名，暂不可选择。'
    const ids = new Set(selectedHotwordIds)
    ids.add(item.id)
    return hotwordLimitIssue(ids.size, hotwordStats(hotwordLists, ids), hotwordLimits, `选择“${item.name}”后将超出单次任务限制`)
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
    setBusy(true)
    setError('')
    setNotice('')
    try {
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
      const job = await api.submitAsr(data)
      onJobSubmitted(job)
      setFile(undefined)
      setSelectedHotwordIds(new Set())
      setNotice('任务已提交，可在任务记录查看进度。')
      if (input.current) input.current.value = ''
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
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
      setMediaError('当前浏览器无法播放该音频编码，请下载原文件后播放。')
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
      setMediaError('当前浏览器无法播放该音频编码，请下载原文件后播放。')
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
  const chooseDropped = (event: React.DragEvent<HTMLButtonElement>) => {
    event.preventDefault()
    const next = event.dataTransfer.files[0]
    if (next) setFile(next)
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
      setNotice(`已将 ${renameSpeaker.label} 重命名为 ${renameValue.trim()}`)
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
        if (!newPersonName.trim()) throw new Error('当前说话人名称未匹配声纹库，请输入新人员名称。')
        const person = await api.addVoiceprintPerson(newPersonName.trim(), newPersonNote.trim() || null, newPersonHotword)
        personId = person.id
        created = true
      }
      await api.addAsrSamples(personId, selected.id, [...selectedSegments])
      await (created ? refreshPeopleAndHotwords() : refreshVoiceprints())
      setSelectedSegments(new Set())
      setLibraryOpen(false)
      setNotice('选中段落已加入声纹库。')
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const resetPreferences = () => {
    const next = { ...defaultAsrPreferences }
    clearAsrPreferences()
    saveAsrPreferences(next)
    setPreferences(next)
    setNotice('已恢复 ASR 默认配置。')
    setError('')
  }

  return (
    <div className="workbench hud-page">
      <aside className="control-panel" data-module="AUDIO_INPUT / UP_01">
        <div className="control-heading">
          <div>
            <h1>音频转写</h1>
            <p className="subtitle">上传音频，获得说话人、逐字时间戳与字幕文件</p>
          </div>
          <button className="reset-settings" type="button" onClick={resetPreferences}>
            <RotateCcw size={14} />
            恢复默认配置
          </button>
        </div>
        <input ref={input} hidden type="file" accept="audio/*,video/*" onChange={(event) => setFile(event.target.files?.[0])} />
        <button className="dropzone" onClick={() => input.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={chooseDropped}>
          <UploadCloud size={44} />
          <b>{file?.name || '拖放音频到这里'}</b>
          <span>{file ? '点击可重新选择' : '支持常见音视频格式'}</span>
        </button>
        <label>
          识别模型
          <select aria-label="ASR 模型" value={model} onChange={(event) => updatePreference('model', event.target.value)}>
            {models.map((item) => (
              <option key={item.id} value={item.id} disabled={!item.installed}>
                {item.name}
                {item.installed ? '' : '（未安装）'}
              </option>
            ))}
          </select>
        </label>
        <label>
          识别语言
          <select value={language} onChange={(event) => updatePreference('language', event.target.value)}>
            {asrLanguages.map((item) => (
              <option key={item} value={item}>
                {item === 'Auto' ? '自动检测' : item}
              </option>
            ))}
          </select>
        </label>
        <fieldset className="hotword-picker">
          <legend>热词表（可选）</legend>
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
                    onChange={() =>
                      setSelectedHotwordIds((current) => {
                        const next = new Set(current)
                        next.has(item.id) ? next.delete(item.id) : next.add(item.id)
                        return next
                      })
                    }
                  />
                  <span>
                    {item.name}
                    {item.kind === 'system' ? <em>系统</em> : null}
                  </span>
                  <small className="hotword-option-count">{item.term_count} 词</small>
                  {issue ? (
                    <small id={issueId} className="hotword-option-issue">
                      {issue}
                    </small>
                  ) : null}
                </label>
              )
            })
          ) : (
            <small className="device-hint">未配置热词表；可前往“热词库”创建。留空表示不启用热词。</small>
          )}
          {hotwordLists.length ? (
            <small className={`hotword-selection-summary${selectedHotwordIssue ? ' invalid' : ''}`}>
              已选 {selectedHotwordIds.size} / {hotwordLimits?.max_selected_lists || 8} 个表 · {selectedHotwordStats.terms} / {hotwordLimits?.max_selected_terms || 500} 个唯一词 · {selectedHotwordStats.promptChars} / {hotwordLimits?.max_prompt_chars || 8000} 字符
            </small>
          ) : null}
          {selectedHotwordIssue ? (
            <p id="asr-hotword-limit-error" className="hotword-limit-error" role="alert">
              {selectedHotwordIssue} 请取消至少一个词表后再提交。
            </p>
          ) : null}
        </fieldset>
        <label>
          说话人数
          <select value={speakers} onChange={(event) => updatePreference('speakerCount', event.target.value)}>
            <option value="auto">自动</option>
            {Array.from({ length: maxSpeakers }, (_, index) => index + 1).map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
        <label>
          时间戳
          <select value={align ? 'word' : 'segment'} onChange={(event) => updatePreference('align', event.target.value === 'word')}>
            <option value="word">句级 + 字词级</option>
            <option value="segment">仅句级</option>
          </select>
        </label>
        <label className="toggle-label">
          <input type="checkbox" checked={useVoiceprints} onChange={(event) => updatePreference('useVoiceprints', event.target.checked)} />
          <span>允许使用声纹库识别人员</span>
          <small className="device-hint">只为匹配到的 Speaker 自动命名，不改变分段结果。</small>
        </label>
        <label>
          计算设备
          <select aria-label="ASR 计算设备" value={effectiveComputeDevice} onChange={(event) => updatePreference('computeDevice', event.target.value as ComputeDevice)}>
            <option value="gpu" disabled={gpuCapability?.available === false}>
              GPU · BF16
              {gpuCapability?.available === false ? '（该模型不可用）' : '（默认）'}
            </option>
            <option value="cpu">CPU · FP32</option>
          </select>
          {computeDevice === 'gpu' && effectiveComputeDevice === 'cpu' ? <small className="device-hint">{computeUnavailableReason(gpuCapability)}</small> : <small className="device-hint">ASR 与时间对齐同步切换；VAD 和说话人分离始终使用 CPU。</small>}
        </label>
        <div className="acceleration-control">
          <label>
            <input type="checkbox" checked={accelerateSingleTask} onChange={(event) => updatePreference('accelerateSingleTask', event.target.checked)} />
            <span>单任务加速</span>
          </label>
          <InfoTooltip id="asr-acceleration-help" text="按 CPU 核心与可用内存或 GPU 显存自动提高任务内部批次。长音频收益更明显；不改变模型、精度、分块或说话人算法，内存不足时会自动回退。" />
        </div>
        <label>
          导出格式<div className="select-like">JSON · SRT · VTT · TXT</div>
        </label>
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
          <button className="primary" disabled={busy || !chosenModel.installed || Boolean(selectedHotwordIssue)} aria-describedby={selectedHotwordIssue ? 'asr-hotword-limit-error' : undefined} onClick={submit}>
            <Play size={18} />
            {busy ? '正在处理…' : '开始转写'}
          </button>
        </div>
        <section className="aside-jobs">
          <h2>任务列表</h2>
          {visibleJobs.map((job) => (
            <JobMini key={job.id} job={job} isSelected={job.id === selectedSummary?.id} onOpen={(item) => item.state === 'succeeded' && onSelect(item)} />
          ))}
        </section>
      </aside>
      <section ref={resultPanel} className="result-panel" data-module="TRANSCRIPT_CORE / TRN_01">
        {selectedSummary && detail?.state === 'loading' ? (
          <div className="empty" role="status">
            <FileAudio size={52} />
            <h2>正在加载转写结果</h2>
            <p>任务摘要已就绪，正在按需读取完整结果。</p>
          </div>
        ) : selectedSummary && detail?.state === 'error' ? (
          <div className="empty" role="alert">
            <FileAudio size={52} />
            <h2>转写结果加载失败</h2>
            <p>{detail.error || '请稍后重试。'}</p>
            <button onClick={() => loadJobDetail(selectedSummary, true)}>重新加载</button>
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
                  <a key={item.name} className="button" href={artifactUrl(selected.id, item.name)} title={`下载 ${item.name}`}>
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
              onError={() => setMediaError('当前浏览器无法播放该音频编码，请下载原文件后播放。')}
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
              <button className="round" aria-label={playing ? '暂停' : '播放'} onClick={play}>
                {playing ? <Pause /> : <Play />}
              </button>
              <strong>{formatTime(currentTime)}</strong>
              <span>/ {formatTime(duration)}</span>
              <span className="grow" />
              <span>
                {result.language} · {result.timestamp_precision === 'word_or_character' ? '字词级对齐' : '句级时间戳'}
              </span>
            </div>
            {alignmentDowngraded ? (
              <p className="notice alignment-notice" role="note">
                自动检测为 {result.language}
                ；该语种暂不支持字词级对齐，本次已返回句段级时间戳。
              </p>
            ) : null}
            {mediaError ? (
              <p className="media-error">
                {mediaError} <a href={sourceUrl(selected.id, true)}>下载原文件</a>
              </p>
            ) : null}
            <div className="transcript-tools">
              <Search size={18} />
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索转写内容" />
              <select aria-label="按说话人过滤" value={speakerFilter} onChange={(event) => changeSpeakerFilter(event.target.value)}>
                <option value="all">全部说话人</option>
                {result.speakers?.map((speaker) => (
                  <option key={speaker.id} value={speaker.id}>
                    {speaker.label}
                  </option>
                ))}
              </select>
              <span>已展示 {Math.min(visibleSegmentCount,segments.length)} / 匹配 {segments.length} / 总计 {result.segments?.length || 0}</span>
            </div>
            {selectedSegments.size ? (
              <div className="segment-selection" aria-label="段落选择操作">
                <b>
                  {selectedSegments.size} 个 {selectionLabel} 段落已选择
                </b>
                <button onClick={openLibrary}>
                  <UserPlus size={16} />
                  加入声纹库
                </button>
                <button className="icon-button" aria-label="清除段落选择" onClick={() => setSelectedSegments(new Set())}>
                  <X />
                </button>
              </div>
            ) : null}
            <div ref={segmentList} className="segments" aria-busy={query!==deferredQuery}>
              {segments.length ? (
                <>{visibleSegments.map((segment) => {
                  const active = currentTime >= segment.start && currentTime < segment.end
                  return <SegmentRow key={segment.id} segment={segment} speaker={speakerById.get(segment.speaker)} active={active} currentTime={active ? currentTime : -1} onPlay={playSegment} onSeekWord={seekWord} selected={selectedSegments.has(segment.id)} selectionSpeaker={selectionSpeaker} onToggle={toggleSegment} onRename={openRename} />
                })}{visibleSegments.length<segments.length?<div ref={loadMoreSentinel} className="segment-load-more"><span>已展示 {visibleSegments.length} / {segments.length} 个匹配片段</span><button type="button" className="button" onClick={loadMoreSegments}>加载更多</button></div>:null}</>
              ) : (
                <div className="empty search-empty">
                  <Search size={38} />
                  <p>没有匹配的转写片段</p>
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="empty">
            <FileAudio size={52} />
            <h2>还没有转写结果</h2>
            <p>从左侧上传音频并启动任务；处理完成后会在这里展示。</p>
          </div>
        )}
      </section>
      {renameSpeaker ? (
        <Modal title="重命名说话人" closeLabel="关闭重命名" onClose={() => setRenameSpeaker(undefined)}>
          <p>{renameSpeaker.label} 的所有段落和导出文件都会同步更新。</p>
          <label>
            新名称
            <input value={renameValue} maxLength={80} onChange={(event) => setRenameValue(event.target.value)} />
          </label>
          <button className="primary" disabled={busy || !renameValue.trim()} onClick={saveRename}>
            保存名称
          </button>
        </Modal>
      ) : null}
      {libraryOpen ? (
        <Modal title="加入声纹库" closeLabel="关闭加入声纹库" onClose={() => setLibraryOpen(false)}>
          <p>
            将 {selectedSegments.size} 个 {selectionLabel} 段落分别保存为独立样本。
          </p>
          {voiceprints.length ? (
            <label>
              指定人员
              <select value={targetPersonId} onChange={(event) => setTargetPersonId(event.target.value)}>
                <option value="">新建人员</option>
                {voiceprints.map((person) => (
                  <option key={person.id} value={person.id}>
                    {person.name}
                    {person.note ? '（' + person.note + '）' : ''} · {person.sample_count} 个样本
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {!targetPersonId ? (
            <>
              <label>
                新人员名字
                <input value={newPersonName} maxLength={80} placeholder="当前名称未匹配，请创建人员" onChange={(event) => setNewPersonName(event.target.value)} />
              </label>
              <label>
                备注（选填）
                <input value={newPersonNote} maxLength={20} placeholder="外号、手机号或公司名称" onChange={(event) => setNewPersonNote(event.target.value)} />
              </label>
              <label className="toggle-label person-hotword-toggle">
                <input type="checkbox" checked={newPersonHotword} onChange={(event) => setNewPersonHotword(event.target.checked)} />
                <span>加入热词库</span>
                <small>自动同步名字到“声纹库人名”。</small>
              </label>
            </>
          ) : (
            <p className="notice">
              已匹配：
              {voiceprints.find((person) => person.id === targetPersonId)?.name}
            </p>
          )}
          <button className="primary" disabled={busy || (!targetPersonId && !newPersonName.trim())} onClick={addToLibrary}>
            {busy ? '正在保存…' : '确认加入'}
          </button>
        </Modal>
      ) : null}
    </div>
  )
}
