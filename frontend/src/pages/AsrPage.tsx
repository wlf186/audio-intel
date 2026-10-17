import { memo, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { Download, Layers, FileAudio, Pause, Pencil, Play, RotateCcw, Search, UploadCloud, UserPlus, X } from 'lucide-react'
import { api, HttpError, size, formatTime, isUploadCancelled, sourceUrl, uploadLimitMessage, type SubmissionProgress as UploadProgress } from '../lib/api'
import type { AsrStrengthCapability, AsrModelCapability, ComputeDevice, HotwordLibraryCapability, HotwordList, Job, JobDetailResource, JobResult, JobSummary, ResourceState, ResultRevealRequest, Segment, Speaker, VoiceprintGroup, VoiceprintPerson } from '../lib/types'
import './asr.css'
import {groupLabel} from '../components/VoiceprintGroups'
import {TranscriptExport} from '../components/TranscriptExport'
import {VideoTranscript} from '../components/VideoTranscript'
import {VideoSpeakerEnrollment} from '../components/VideoSpeakerEnrollment'
import {enrollmentPerson} from '../lib/voiceprintEnrollment'
import {VideoRegionPicker} from '../components/VideoRegionPicker'
import {submitVideo,loadVideoResume,discardVideoUpload,type VideoCapability,type VideoRegion} from '../lib/videoUpload'
import { ResourceStatePanel } from '../components/ResourceStatePanel'
import { Waveform } from '../components/Waveform'
import {SettingToggleRow} from '../components/SettingToggleRow'
import {EnhancementSettings} from '../components/EnhancementSettings'
import { EnhancedWaveform } from '../components/EnhancedWaveform'
import { JobMini } from '../components/JobMini'
import { Modal } from '../components/Modal'
import { InfoTooltip } from '../components/InfoTooltip'
import { WorkbenchDivider } from '../components/WorkbenchDivider'
import { clearAsrPreferences, combinedAudioEnhancement, defaultAsrPreferences, loadAsrPreferences, publicAlignerLanguages, publicAsrLanguages, saveAsrPreferences, type AsrPreferences } from '../lib/preferences'
import { visibleWorkspaceJobs } from '../lib/jobs'
import { computeUnavailableReason, voiceprintPersonLabel, speakerIdentity, hotwordListDisplayName } from '../lib/presentation'
import { SubmissionProgress } from '../components/SubmissionProgress'
import { useTranslation } from 'react-i18next'
import {checkpointWorkspace,readAsrSession,saveAsrSession,readReaderBookmark,saveReaderBookmark} from '../lib/workspaceSession'
import {useReaderBookmark} from '../hooks/useReaderBookmark'
import type { TFunction } from 'i18next'
const segmentBatchSize=40
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
  const identity = speakerIdentity(speaker, segment.speaker_label)
  return (
    <article className={`${active ? 'active ' : ''}${selected ? 'selected-segment' : ''}`} data-reader-id={segment.id} data-reader-time={segment.start}>
      <label className="segment-select">
        <input type="checkbox" checked={selected} disabled={disabled} aria-label={t('asr.segment.select', { number: segment.id + 1 })} onChange={() => onToggle(segment)} />
      </label>
      <div className="segment-time">
        <b>{formatTime(segment.start)}</b>
        <span>—</span>
        <b>{formatTime(segment.end)}</b>
      </div>
      <button className={`speaker s${Number(segment.speaker.split('_')[1]) % 4}`} aria-label={t('asr.segment.renameNamed', { name: segment.speaker_label })} title={t('asr.segment.renameHelp')} onClick={() => onRename(segment)}>
        <i />
        <span className="speaker-identity">
          <b>{identity.name}</b>
          {identity.note ? <small>（{identity.note}）</small> : null}
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
  jobsReady: boolean
  jobsError: string
  refreshJobs: () => Promise<void>
  refreshHotwords: () => Promise<void>
  onManageJobs: () => void
  jobs: JobSummary[]
  jobDetails: Record<string, JobDetailResource>
  loadJobDetail: (job: JobSummary, force?: boolean) => void
  onJobSubmitted: (job: Job) => void
  onJobResultUpdated: (jobId: string, result: JobResult) => void
  selectedJobId?: string
  restoreState: ResourceState
  restoreError: string
  restoreSelected: (force?:boolean)=>void
  onSelect: (job: JobSummary) => void
  gpuAvailable?: boolean
  defaultComputeDevice: ComputeDevice
  maxSpeakers: number
  maxUploadBytes?: number
  enhancementAvailable?: boolean
  denoiseAvailable?: boolean
  gainStrengthCapability?: AsrStrengthCapability
  noiseStrengthCapability?: AsrStrengthCapability
  videoCapability?: VideoCapability
  audioFormats?: string[]
  asrLanguages?: string[]
  alignerLanguages?: string[]
  asrModels: AsrModelCapability[]
  hotwordLists: HotwordList[]
  hotwordsState: ResourceState
  hotwordLimits?: HotwordLibraryCapability
  voiceprintGroups: VoiceprintGroup[]
  voiceprintGroupsState: ResourceState
  refreshVoiceprintGroups: () => Promise<void>
  voiceprints: VoiceprintPerson[]
  voiceprintsState: ResourceState
  refreshVoiceprints: () => Promise<void>
  refreshPeopleAndHotwords: () => Promise<void>
  revealRequest?: ResultRevealRequest
  onRevealHandled: (token: number) => void
}

export function AsrPage({ jobsReady, jobsError, refreshJobs, refreshHotwords, onManageJobs, jobs, jobDetails, loadJobDetail, onJobSubmitted, onJobResultUpdated, selectedJobId, restoreState, restoreError, restoreSelected, onSelect, gpuAvailable, defaultComputeDevice, maxSpeakers, maxUploadBytes, enhancementAvailable, denoiseAvailable, gainStrengthCapability, noiseStrengthCapability, videoCapability, audioFormats, asrLanguages = publicAsrLanguages, alignerLanguages = publicAlignerLanguages, asrModels, hotwordLists, hotwordsState, hotwordLimits, voiceprints, voiceprintsState, voiceprintGroups, voiceprintGroupsState, refreshVoiceprintGroups, refreshVoiceprints, refreshPeopleAndHotwords, revealRequest, onRevealHandled }: Props) {
  const { t, i18n } = useTranslation()
  const [view, setView] = useState<'create'|'video'|'results'>(()=>readAsrSession().view)
  const [compact, setCompact] = useState(()=>matchMedia('(max-width: 1023px)').matches)
  useEffect(()=>{const media=matchMedia('(max-width: 1023px)');const change=()=>setCompact(media.matches);media.addEventListener('change',change);return()=>media.removeEventListener('change',change)},[])
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [advancedOpen,setAdvancedOpen]=useState(false)
  const [hotwordsOpen, setHotwordsOpen] = useState(false)
  const [hotwordQuery, setHotwordQuery] = useState('')
  const [lastSubmitted, setLastSubmitted] = useState<JobSummary>()
  const [renameBusy, setRenameBusy] = useState(false)
  const [libraryBusy, setLibraryBusy] = useState(false)
  const [renameError, setRenameError] = useState('')
  const [resultNotice, setResultNotice] = useState('')
  const [resultError, setResultError] = useState('')
  const [audioFile, setAudioFile] = useState<File>()
  const [videoFile, setVideoFile] = useState<File>()
  const [videoRoi,setVideoRoi]=useState<VideoRegion>()
  const [resume,setResume]=useState(loadVideoResume)
  const [discardOpen,setDiscardOpen]=useState(false)
  const [videoReading,setVideoReading]=useState(()=>readAsrSession().videoReading)
  useEffect(()=>{saveAsrSession({view,videoReading})},[view,videoReading])
  const [videoEditing,setVideoEditing]=useState(false)
  const [videoWaveform,setVideoWaveform]=useState(false)
  const [videoToolbar,setVideoToolbar]=useState<HTMLDivElement|null>(null)
  useEffect(()=>setVideoWaveform(false),[selectedJobId])
  const [videoEnrollment,setVideoEnrollment]=useState<{jobId:string;speakerId:string;segmentIds:number[]}>()
  useEffect(()=>setVideoEnrollment(undefined),[selectedJobId])
  useEffect(()=>setVideoEditing(false),[selectedJobId])
  const isVideoTab=view==='video'
  const file=isVideoTab?videoFile:audioFile
  const setFile=(next:File|undefined)=>{if(isVideoTab){setVideoFile(next);if(!resume)setVideoRoi(undefined)}else setAudioFile(next)}
  const formats=isVideoTab?videoCapability?.formats:audioFormats
  const uploadLimit=isVideoTab?videoCapability?.max_upload_bytes:maxUploadBytes
  const [groupsOpen,setGroupsOpen]=useState(false)
  const [groupSelectionWarning,setGroupSelectionWarning]=useState(false)
  const strengthDefaults={speech:gainStrengthCapability?.default??1,noise:noiseStrengthCapability?.default??.5}
  const [enhancementSettingsOpen,setEnhancementSettingsOpen]=useState(false)
  const enhancementAvailabilityNote=enhancementAvailable===true&&denoiseAvailable===true?'':t(`asr.enhancement.${enhancementAvailable===true&&denoiseAvailable===false?'denoiseOnlyUnavailable':enhancementAvailable===false&&denoiseAvailable===true?'gainOnlyUnavailable':enhancementAvailable===false&&denoiseAvailable===false?'bothUnavailable':'availabilityUnknown'}`)
  const strengthLabel=(value:number)=>value===0?t('asr.enhancement.skip'):`${Math.round(value*100)}%`
  const [preferences, setPreferences] = useState<AsrPreferences>(() => loadAsrPreferences(maxSpeakers,defaultComputeDevice,strengthDefaults))
  useEffect(()=>{const reset=()=>setPreferences(current=>({...current,hotwordListIds:[]}));addEventListener('audio-intel:library-replaced',reset);return()=>removeEventListener('audio-intel:library-replaced',reset)},[])
  const customEnhancementStrengths=preferences.speechEnhancementStrength!==strengthDefaults.speech||preferences.backgroundDenoiseStrength!==strengthDefaults.noise
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [uploadProgress,setUploadProgress]=useState<UploadProgress>()
  const [mediaError, setMediaError] = useState('')
  const [mediaLoading, setMediaLoading] = useState(true)
  const [enhancedPlayback,setEnhancedPlayback]=useState(false)
  const pendingAudioSwitch=useRef<{time:number;playing:boolean}|undefined>(undefined)
  const [query, setQuery] = useState(()=>readReaderBookmark(`${selectedJobId}:segments`)?.query||'')
  const deferredQuery = useDeferredValue(query)
  const segmentFilterGeneration=useRef(0)
  const [speakerFilter, setSpeakerFilter] = useState(()=>readReaderBookmark(`${selectedJobId}:segments`)?.speakerFilter||'all')
  const [visibleSegmentCount, setVisibleSegmentCount] = useState(()=>readReaderBookmark(`${selectedJobId}:segments`)?.visibleCount||segmentBatchSize)
  const [readerJobId,setReaderJobId]=useState(selectedJobId)
  if(readerJobId!==selectedJobId){
    const bookmark=readReaderBookmark(`${selectedJobId}:segments`)
    setReaderJobId(selectedJobId);setQuery(bookmark?.query||'');setSpeakerFilter(bookmark?.speakerFilter||'all');setVisibleSegmentCount(bookmark?.visibleCount||segmentBatchSize)
  }
  const [selectedSegments, setSelectedSegments] = useState<Set<number>>(() => new Set())
  const [renameSpeaker, setRenameSpeaker] = useState<{
    id: string
    label: string
  }>()
  const [renameValue, setRenameValue] = useState('')
  const [renameNote, setRenameNote] = useState('')
  const [renamePersonId, setRenamePersonId] = useState('')
  const renamePersonValue = useMemo(() => voiceprintsState === 'ready' ? voiceprints.find(person =>
    person.id === renamePersonId && person.name === renameValue && (person.note || '') === renameNote,
  )?.id || '' : '', [voiceprints, voiceprintsState, renamePersonId, renameValue, renameNote])
  const [libraryOpen, setLibraryOpen] = useState(false)
  const libraryNameRef=useRef<HTMLInputElement>(null)
  const [libraryError,setLibraryError]=useState('')
  const [libraryAmbiguous,setLibraryAmbiguous]=useState(false)
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
  const selectWorkspace = (next: 'create'|'video'|'results') => {
    checkpointWorkspace()
    setView(next)
  }
  const loadMoreSentinel = useRef<HTMLDivElement>(null)
  const stopAt = useRef<number | undefined>(undefined)
  const uploadController=useRef<AbortController|undefined>(undefined)
  const asrJobs = useMemo(() => jobs.filter((job) => job.kind === 'asr'), [jobs])
  const selectedSummary = asrJobs.find((job) => job.id === selectedJobId)
  const visibleJobs = useMemo(() => visibleWorkspaceJobs(asrJobs, selectedSummary?.id), [asrJobs, selectedSummary?.id])
  const detail = selectedSummary ? jobDetails[selectedSummary.id] : undefined
  const selected = detail?.job
  const result = selected?.result
  const enhancedAudio=result?.audio_enhancement?.playback_url
  const originalDownloadUrl=result?.playback_url?`${result.playback_url}${result.playback_url.includes('?')?'&':'?'}download=true`:undefined
  const playbackUrl=enhancedPlayback&&enhancedAudio?enhancedAudio:result?.playback_url||selected?.source_url||(selected?sourceUrl(selected.id):undefined)
  useReaderBookmark(selectedSummary?`${selectedSummary.id}:segments`:'',segmentList,{active:view==='results'&&!videoReading||view==='results'&&!result?.video,ready:Boolean(result)&&query===deferredQuery,visibleCount:visibleSegmentCount,query:deferredQuery.trim().toLocaleLowerCase(),speakerFilter})
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
  const manualGroups=preferences.voiceprintGroupMode==='selected'
  const scopeGroups=voiceprintGroups.filter(group=>!manualGroups||preferences.voiceprintGroupIds.includes(group.id))
  const scopeLabel=voiceprintGroupsState==='ready'?t('workspaceUx.voiceprintScope',{groups:manualGroups?scopeGroups.map(group=>groupLabel(group,t('voiceprintGroups.ungrouped'))).join('、'):t('voiceprintGroups.allGroups'),count:scopeGroups.reduce((total,group)=>total+group.person_count,0)}):t(voiceprintGroupsState==='error'?'voiceprintGroups.failed':'voiceprintGroups.loading')
  const groupSelectionIssue=useVoiceprints&&manualGroups?(voiceprintGroupsState!=='ready'?t('voiceprintGroups.failed'):!preferences.voiceprintGroupIds.length?t('voiceprintGroups.chooseAtLeastOne'):''):''
  const selectedHotwordIds = useMemo(() => new Set(preferences.hotwordListIds), [preferences.hotwordListIds])
  const matchingHotwords = hotwordLists.filter(item=>hotwordListDisplayName(item,t).toLocaleLowerCase().includes(hotwordQuery.trim().toLocaleLowerCase()))
  const selectedHotwordStats = useMemo(() => hotwordStats(hotwordLists, selectedHotwordIds), [hotwordLists, selectedHotwordIds])
  const selectedHotwordIssue = hotwordLimitIssue(selectedHotwordIds.size, selectedHotwordStats, hotwordLimits, t('asr.hotwords.currentSelection'), t)
  const fileLimitError=file?uploadLimitMessage(file,uploadLimit,t,i18n.resolvedLanguage||'zh-CN'):''
  const submitLabel=uploadProgress?.phase==='verifying'?t('video.verifying'):uploadProgress?.phase==='creating'?t('asr.submit.creating'):uploadProgress?.phase==='uploading'&&uploadProgress.percent!==undefined?t('asr.submit.uploading',{percent:uploadProgress.percent}):uploadProgress?t('asr.submit.preparing'):busy?t('common.states.processing'):t('asr.submit.start')
  const hotwordSelectionIssue = (item: HotwordList) => {
    if (selectedHotwordIds.has(item.id)) return ''
    if (!item.term_count) return t('asr.hotwords.emptySystemList')
    const ids = new Set(selectedHotwordIds)
    ids.add(item.id)
    return hotwordLimitIssue(ids.size, hotwordStats(hotwordLists, ids), hotwordLimits, t('asr.hotwords.selectionWouldExceed',{name:hotwordListDisplayName(item,t)}), t)
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
    if (view === 'results' && selectedSummary?.state === 'succeeded') loadJobDetail(selectedSummary)
  }, [loadJobDetail, selectedSummary, view])
  useEffect(()=>{if(view==='results'&&jobsReady&&selectedJobId&&!selectedSummary)restoreSelected()},[view,jobsReady,selectedJobId,selectedSummary,restoreSelected])
  useEffect(() => {
    if (view === 'results' && jobsReady && !selectedJobId && asrJobs[0]) onSelect(asrJobs[0])
  }, [view, jobsReady, selectedJobId, asrJobs, onSelect])
  useEffect(() => { if (view !== 'results') audio.current?.pause() }, [view])
  useEffect(()=>()=>uploadController.current?.abort(),[])
  useEffect(()=>{
    if(!isVideoTab)return
    const saved=loadVideoResume();setResume(saved)
    if(!saved)return
    const p=saved.params
    setPreferences(current=>({...current,model:String(p.model||current.model),language:String(p.language||'Auto'),speakerCount:p.speaker_count?String(p.speaker_count):'auto',align:p.align!==false,useVoiceprints:p.use_voiceprint_library!==false,computeDevice:p.compute_device==='cpu'?'cpu':'gpu',accelerateSingleTask:p.accelerate_single_task!==false,audioEnhancement:combinedAudioEnhancement(p.speech_enhancement,p.background_denoise,p.audio_enhancement==='auto'?'auto':'off'),speechEnhancementStrength:typeof p.speech_enhancement_strength==='number'?p.speech_enhancement_strength:strengthDefaults.speech,backgroundDenoiseStrength:typeof p.background_denoise_strength==='number'?p.background_denoise_strength:strengthDefaults.noise,hotwordListIds:Array.isArray(p.hotword_list_ids)?p.hotword_list_ids as string[]:[],voiceprintGroupMode:Array.isArray(p.voiceprint_group_ids)?'selected':'all',voiceprintGroupIds:Array.isArray(p.voiceprint_group_ids)?p.voiceprint_group_ids as string[]:[]}))
    setVideoRoi(p.video_roi as VideoRegion|undefined)
  },[isVideoTab])
  useEffect(() => {
    const player = audio.current
    if (player) {
      player.pause()
      player.currentTime = 0
      player.load()
    }
    stopAt.current = undefined
    pendingAudioSwitch.current=undefined
    setEnhancedPlayback(false)
    setCurrentTime(0)
    setPlaying(false)
    setMediaError('')
    setMediaLoading(true)
    setSelectedSegments(new Set())
    setResultError('')
    setResultNotice('')
  }, [selectedSummary?.id])

  useEffect(() => {
    if (activeSegmentIndex < visibleSegmentCount) return
    setVisibleSegmentCount(Math.ceil((activeSegmentIndex + 1) / segmentBatchSize) * segmentBatchSize)
  }, [activeSegmentIndex, visibleSegmentCount])
  const loadMoreSegments = useCallback(() => setVisibleSegmentCount((current) => Math.min(segments.length, current + segmentBatchSize)), [segments.length])
  useEffect(() => {
    const root = segmentList.current
    const target = loadMoreSentinel.current
    if (view !== 'results' || query!==deferredQuery || !root || !target || visibleSegmentCount >= segments.length) return
    let active=true
    const generation=segmentFilterGeneration.current
    const observer = new IntersectionObserver((entries) => {if(active&&generation===segmentFilterGeneration.current&&entries.some((entry) => entry.isIntersecting))loadMoreSegments()}, {root,rootMargin:'0px 0px 360px'})
    observer.observe(target)
    return () => {active=false;observer.disconnect()}
  }, [loadMoreSegments, segments.length, visibleSegmentCount, view, videoReading,query,deferredQuery])
  useEffect(() => {
    if (!revealRequest) return
    setView('results')
    const frame = requestAnimationFrame(() => {
      document.querySelector<HTMLElement>('#main-content')?.scrollTo({top:0,left:0,behavior:'auto'})
      window.scrollTo({top:0,left:0,behavior:'auto'})
      resultPanel.current?.focus({preventScroll:true})
      onRevealHandled(revealRequest.token)
    })
    return () => cancelAnimationFrame(frame)
  }, [onRevealHandled, revealRequest])


  useEffect(()=>{
    if(voiceprintGroupsState!=='ready'||preferences.voiceprintGroupMode!=='selected')return
    const ids=preferences.voiceprintGroupIds.filter(id=>voiceprintGroups.some(group=>group.id===id))
    if(ids.length!==preferences.voiceprintGroupIds.length){setGroupSelectionWarning(true);setPreferences(current=>{const next={...current,voiceprintGroupIds:ids};saveAsrPreferences(next);return next})}
  },[voiceprintGroups,voiceprintGroupsState,preferences.voiceprintGroupMode,preferences.voiceprintGroupIds])

  const submit = async () => {
    if(groupSelectionIssue){setError(groupSelectionIssue);setGroupsOpen(true);return}
    if (selectedHotwordIds.size && hotwordsState !== 'ready') { setError(t('asr.workspace.hotwordsUnavailable')); return }
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
      if(manualGroups)data.set('voiceprint_group_ids',JSON.stringify(preferences.voiceprintGroupIds))
      data.set('export_formats', 'json,srt,vtt,txt')
      data.set('compute_device', effectiveComputeDevice)
      data.set('accelerate_single_task', String(accelerateSingleTask))
      data.set('audio_enhancement',preferences.audioEnhancement)
      const strengthParams=gainStrengthCapability&&noiseStrengthCapability?{speech_enhancement_strength:preferences.speechEnhancementStrength,background_denoise_strength:preferences.backgroundDenoiseStrength}:{}
      for(const [name,value] of Object.entries(strengthParams))data.set(name,String(value))
      if (selectedHotwordIds.size) data.set('hotword_list_ids', [...selectedHotwordIds].join(','))
      const job = isVideoTab?await submitVideo(file,{model,language,speaker_count:speakers==='auto'?null:Number(speakers),diarize:true,align,use_voiceprint_library:useVoiceprints,...(manualGroups?{voiceprint_group_ids:preferences.voiceprintGroupIds}:{}),compute_device:effectiveComputeDevice,accelerate_single_task:accelerateSingleTask,audio_enhancement:preferences.audioEnhancement,...strengthParams,hotword_list_ids:[...selectedHotwordIds],video_roi:videoRoi||null},{signal:controller.signal,onProgress:setUploadProgress}):await api.submitAsr(data,{signal:controller.signal,onProgress:setUploadProgress})
      onJobSubmitted(job)
      setLastSubmitted(job)
      onSelect(job)
      setFile(undefined)
      setNotice(t('asr.notices.submitted'))
      if (input.current) input.current.value = ''
    } catch (cause) {
      if(isUploadCancelled(cause))setNotice(t(isVideoTab?'video.paused':'asr.notices.uploadCancelled'))
      else setError((cause as Error).message)
    } finally {
      setResume(loadVideoResume())
      uploadController.current=undefined
      setUploadProgress(undefined)
      setBusy(false)
    }
  }
  const switchAudio = (enhanced:boolean) => {
    if(enhanced===enhancedPlayback||!audio.current||!enhancedAudio)return
    pendingAudioSwitch.current={time:audio.current.currentTime,playing:!audio.current.paused}
    audio.current.pause()
    setMediaError('');setMediaLoading(true);setEnhancedPlayback(enhanced)
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
  const chooseFile=(next?:File)=>{if(!next)return;const extension=next.name.split('.').pop()?.toLowerCase();if(formats&&!formats.includes(extension||'')){setError(t('video.unsupportedFormat'));return}setFile(next);setError('');setNotice('')}
  const chooseDropped = (event: React.DragEvent<HTMLElement>) => {
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
  const resetSegmentFilter=(nextQuery:string,nextSpeaker:string)=>{
    if(nextQuery.trim().toLocaleLowerCase()===query.trim().toLocaleLowerCase()&&nextSpeaker===speakerFilter)return
    checkpointWorkspace();segmentFilterGeneration.current++
    if(selectedSummary)saveReaderBookmark(`${selectedSummary.id}:segments`,{anchorId:'',time:0,offset:0,scrollTop:0,visibleCount:segmentBatchSize,query:nextQuery.trim().toLocaleLowerCase(),speakerFilter:nextSpeaker})
    setVisibleSegmentCount(segmentBatchSize)
  }
  const changeSpeakerFilter = (next: string) => {
    resetSegmentFilter(query,next)
    if (selectionSpeaker && next !== 'all' && next !== selectionSpeaker) setSelectedSegments(new Set())
    setSpeakerFilter(next)
  }
  const openRename = useCallback((segment: Segment) => {
    setRenameError('')
    setRenamePersonId('')
    setRenameSpeaker({ id: segment.speaker, label: segment.speaker_label })
    const identity = speakerIdentity(speakerById.get(segment.speaker), segment.speaker_label)
    setRenameValue(identity.name)
    setRenameNote(identity.note)
  }, [speakerById])
  const saveRename = async () => {
    if (!selected || !renameSpeaker || !renameValue.trim()) return
    setRenameBusy(true)
    setRenameError('')
    try {
      const next = await api.renameSpeaker(selected.id, renameSpeaker.id, renameValue.trim(), renameNote.trim() || null)
      setSpeakerFilter(current => {
        if (current === 'all' || next.speakers?.some(speaker => speaker.id === current)) return current
        const anchor = result?.segments?.find(segment => segment.speaker === current)
        return next.segments?.find(segment => segment.id === anchor?.id)?.speaker || 'all'
      })
      const anchor = result?.segments?.find(segment => segment.speaker === renameSpeaker.id)
      const updatedId = next.segments?.find(segment => segment.id === anchor?.id)?.speaker || renameSpeaker.id
      const updatedLabel = next.speakers?.find(speaker => speaker.id === updatedId)?.label || renameValue.trim()
      onJobResultUpdated(selected.id, next)
      setRenameSpeaker(undefined)
      setResultNotice(t('asr.notices.renamed',{from:renameSpeaker.label,to:updatedLabel}))
    } catch (cause) {
      setRenameError((cause as Error).message)
    } finally {
      setRenameBusy(false)
    }
  }
  const openLibrary = () => {
    const speaker = selectionSpeaker ? speakerById.get(selectionSpeaker) : undefined
    const {identity,match,ambiguous}=enrollmentPerson(speaker,selectionLabel,voiceprints)
    setLibraryError('')
    setLibraryAmbiguous(ambiguous)
    setTargetPersonId(match?.id || (ambiguous?'__choose__':''))
    setNewPersonName(/^Speaker[ _]\d+$/i.test(identity.name) ? '' : identity.name)
    setNewPersonNote(identity.note)
    setNewPersonHotword(true)
    setLibraryOpen(true)
  }
  const addToLibrary = async () => {
    if (!selected || !selectedSegments.size || targetPersonId==='__choose__') return
    setLibraryBusy(true)
    setLibraryError('')
    let creatingPerson=!targetPersonId
    let created=false
    try {
      let personId = targetPersonId
      if (!personId) {
        if (!newPersonName.trim()) throw new Error(t('asr.library.nameRequired'))
        const person = await api.addVoiceprintPerson(newPersonName.trim(), newPersonNote.trim() || null, newPersonHotword)
        personId = person.id
        setTargetPersonId(personId)
        created = true
        creatingPerson = false
      }
      await api.addAsrSamples(personId, selected.id, [...selectedSegments])
      setSelectedSegments(new Set())
      setLibraryOpen(false)
      setResultNotice(t('asr.notices.addedToVoiceprints'))
      if(!created)await refreshVoiceprints().catch(cause=>setResultError((cause as Error).message))
    } catch (cause) {
      setLibraryError(cause instanceof HttpError&&cause.status===409&&creatingPerson?t('voiceprintNames.personConflict'):(cause as Error).message)
      if(creatingPerson)libraryNameRef.current?.focus()
    } finally {
      if(created)await refreshPeopleAndHotwords().catch(cause=>setResultError((cause as Error).message))
      setLibraryBusy(false)
    }
  }
  const resetPreferences = () => {
    const next = { ...defaultAsrPreferences, speechEnhancementStrength:strengthDefaults.speech,backgroundDenoiseStrength:strengthDefaults.noise,computeDevice: defaultComputeDevice, hotwordListIds: [] }
    clearAsrPreferences()
    saveAsrPreferences(next)
    setPreferences(next)
    setNotice(t('asr.defaultsRestored'))
    setError('')
  }

  return (
    <div className={`workbench hud-page asr-workspace${view==='results'?' view-results':''}`}>
      <header className="asr-heading"><h1>{t('asr.title')}</h1></header>
      <div className="asr-tabs" role="tablist" aria-label={t('asr.title')} onKeyDown={event=>{
        if(busy)return
        const tabs=Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role=tab]'))
        const index=tabs.indexOf(document.activeElement as HTMLButtonElement)
        const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:event.key==='ArrowRight'?(index+1)%tabs.length:event.key==='ArrowLeft'?(index+tabs.length-1)%tabs.length:-1
        if(next<0)return
        event.preventDefault();tabs[next]?.focus();selectWorkspace((tabs[next]?.dataset.view||'create') as 'create'|'video'|'results')
      }}>
        {(['create',...(videoCapability?.supported?['video' as const]:[]),'results'] as const).map(item=><button key={item} data-view={item} disabled={busy} id={`asr-tab-${item}`} role="tab" aria-selected={view===item} aria-controls={item==='results'?'asr-results':'asr-create'} tabIndex={view===item?0:-1} onClick={()=>selectWorkspace(item)}>{t(`asr.workspace.${item}`)}</button>)}
      </div>
      <aside className="control-panel asr-create resizable-workbench" id="asr-create" role="tabpanel" aria-labelledby={isVideoTab?'asr-tab-video':'asr-tab-create'} hidden={view==='results'}>
      <div className={`asr-upload${file?' has-file':''}`} id="asr-input-pane">

        <input ref={input} hidden type="file" accept={formats?formats.map(item=>'.'+item).join(','):isVideoTab?'video/*':'audio/*'} disabled={busy} onChange={(event) => chooseFile(event.target.files?.[0])} />
        {!file?<button className="dropzone" disabled={busy} onClick={() => input.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={chooseDropped}>
          <UploadCloud size={44} />
          <b>{t(isVideoTab?'video.dropzone':'asr.dropzone.title')}</b>
          <span>{formats?.map(item=>item.toUpperCase()).join(' / ')||t('asr.dropzone.formats')}</span>
        </button>:<div className="asr-file-info" onDragOver={event=>event.preventDefault()} onDrop={chooseDropped}><span><b>{file.name}</b><small>{size(file.size,i18n.resolvedLanguage)}</small></span><div className="asr-file-actions"><button className="button" disabled={busy} onClick={()=>input.current?.click()}>{t('workspaceSplit.changeFile')}</button><button className="button" disabled={busy} onClick={()=>{setFile(undefined);if(input.current)input.current.value='';setError('')}}>{t('asr.workspace.removeFile')}</button></div></div>}
        <p>{t('video.uploadLimit',{limit:size(uploadLimit||0,i18n.resolvedLanguage)})}</p>
        <p>{t(isVideoTab?'video.uploadHelp':'asr.subtitle')}</p>
        {isVideoTab&&resume?<div className="video-resume"><p>{t('video.resumeNotice',{name:resume.name})}</p><button className="button" disabled={busy} onClick={()=>setDiscardOpen(true)}>{t('video.discard')}</button></div>:null}
        {isVideoTab&&file?<VideoRegionPicker file={file} value={videoRoi} onChange={setVideoRoi} disabled={busy||Boolean(resume)}/>:null}
      </div>
      <WorkbenchDivider workbench="asr"/>
      <section className={`asr-settings${settingsOpen?' expanded':''}`} id="asr-settings-pane">
        <div className="settings-heading"><button className="asr-settings-toggle" aria-expanded={!compact||settingsOpen} aria-controls="asr-settings-body" onClick={()=>{if(compact)setSettingsOpen(value=>!value)}}>{t('asr.workspace.settings')} · {effectiveComputeDevice.toUpperCase()}</button><button className="reset-settings" title={t('asr.restoreDefaults')} aria-label={t('asr.restoreDefaults')} onClick={resetPreferences}><RotateCcw size={16}/></button></div>
        {compact&&(!settingsOpen||!advancedOpen)&&computeDevice==='gpu'&&effectiveComputeDevice==='cpu'?<p className="notice">{computeUnavailableReason(gpuCapability,t)}</p>:null}
        {selectedHotwordIds.size>0&&hotwordsState!=='ready'?<ResourceStatePanel state={hotwordsState} loadingLabel={t('asr.workspace.loadingHotwords')} errorLabel={t('asr.workspace.hotwordsUnavailable')} retry={()=>void refreshHotwords().catch(()=>undefined)}/>:null}
        {selectedHotwordIssue?<p id="asr-hotword-submit-error" className="error" role="alert">{selectedHotwordIssue}</p>:null}
        <fieldset id="asr-settings-body" className="asr-settings-body" disabled={busy||(isVideoTab&&Boolean(resume))}>
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
        <section className="asr-processing-options" aria-label={t('asr.processingOptions')}>
          <h2>{t('asr.processingOptions')}</h2>
          <SettingToggleRow id="asr-acceleration" label={t('asr.acceleration')} description={t('asr.accelerationDescription')} checked={accelerateSingleTask} onChange={checked=>updatePreference('accelerateSingleTask',checked)} help={t('asr.accelerationHelp')}/>
          <SettingToggleRow id="asr-enhancement" label={t('asr.enhancement.label')} description={t('asr.enhancement.description')} checked={preferences.audioEnhancement==='auto'} onChange={checked=>updatePreference('audioEnhancement',checked?'auto':'off')} help={t('asr.enhancement.help')} helpLabel={t('asr.enhancement.helpLabel')}>
            {preferences.audioEnhancement==='off'?<span>{t('asr.enhancement.originalAudio')}</span>:gainStrengthCapability&&noiseStrengthCapability?<>
              <span>{t('asr.enhancement.savedStrengths',{gain:strengthLabel(preferences.speechEnhancementStrength),noise:strengthLabel(preferences.backgroundDenoiseStrength)})}</span>
              {customEnhancementStrengths?<span className="setting-toggle-custom">{t('asr.enhancement.custom')}</span>:null}
            </>:null}
            {gainStrengthCapability&&noiseStrengthCapability?<button type="button" className="button enhancement-settings-button" aria-label={t('asr.enhancement.settings')} disabled={preferences.audioEnhancement==='off'} onClick={()=>setEnhancementSettingsOpen(true)}>{t('asr.enhancement.adjust')}</button>:null}
            {preferences.audioEnhancement==='off'?gainStrengthCapability&&noiseStrengthCapability?<p className="setting-toggle-note">{t('asr.enhancement.enableToAdjust')}</p>:null:enhancementAvailabilityNote?<p className="setting-toggle-note" role="note">{enhancementAvailabilityNote}</p>:null}
          </SettingToggleRow>
        </section>
        <div className={`asr-advanced${advancedOpen?' expanded':''}`}><button type="button" className="asr-advanced-toggle" aria-expanded={!compact||advancedOpen} aria-controls="asr-advanced-body" onClick={()=>setAdvancedOpen(value=>!value)}>{t('asr.workspace.advanced')} · {chosenModel.name} · {effectiveComputeDevice.toUpperCase()}</button><div className="asr-advanced-body" id="asr-advanced-body">
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
          {t('asr.computeDevice')}
          <select aria-label={t('asr.computeDeviceAria')} aria-describedby={computeDevice==='gpu'&&effectiveComputeDevice==='cpu'?'asr-device-warning':undefined} title={t('asr.computeHelp')} value={effectiveComputeDevice} onChange={(event) => updatePreference('computeDevice', event.target.value as ComputeDevice)}>
            <option value="gpu" disabled={gpuCapability?.available === false}>
              GPU · BF16
              {gpuCapability?.available === false ? t('asr.modelUnavailable') : t('asr.defaultMark')}
            </option>
            <option value="cpu">CPU · FP32</option>
          </select>
        </label>
        {computeDevice === 'gpu' && effectiveComputeDevice === 'cpu' ? <small id="asr-device-warning" className="device-hint asr-device-warning">{computeUnavailableReason(gpuCapability,t)}</small> : null}
        <div className="asr-options">
        <div className="asr-voiceprint-toggle"><label className="toggle-label">
          <input type="checkbox" checked={useVoiceprints} onChange={(event) => updatePreference('useVoiceprints', event.target.checked)} />
          <span>{t('asr.voiceprints.label')}</span>
        </label><InfoTooltip id="asr-voiceprints-help" label={t('asr.voiceprints.label')} text={t('asr.voiceprints.help')}/><button className="button voiceprint-scope-button" aria-label={t('voiceprintGroups.scopeButton')} title={t('voiceprintGroups.scopeButton')} disabled={!useVoiceprints} onClick={()=>setGroupsOpen(true)}><Layers size={16}/>{t('workspaceUx.changeScope')}</button></div>
        {useVoiceprints?<small className="voiceprint-scope-summary">{scopeLabel}</small>:null}
        </div>
        </div></div>
        {groupSelectionIssue?<p className="error" role="alert">{groupSelectionIssue}</p>:null}
        <div className="asr-hotword-summary">
          <span>{t('asr.hotwords.label')}</span>
          <p>{hotwordsState==='ready'?t('asr.workspace.hotwordSummary',{lists:selectedHotwordIds.size,terms:selectedHotwordStats.terms}):t(hotwordsState==='loading'?'asr.workspace.loadingHotwords':'asr.workspace.hotwordsUnavailable')}</p>
          <button className="button" onClick={()=>setHotwordsOpen(true)}>{t('asr.workspace.chooseHotwords')}</button>
          {selectedHotwordIds.size?<button className="button" onClick={()=>updatePreference('hotwordListIds',[])}>{t('asr.workspace.clearHotwords')}</button>:null}
        </div>
        </fieldset>
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
          {lastSubmitted?<div className="asr-submitted"><span>{lastSubmitted.display_name}</span><button className="button" onClick={()=>{onSelect(lastSubmitted);setView('results')}}>{t('asr.workspace.viewSubmitted')}</button></div>:null}
          {uploadProgress?<SubmissionProgress label={t('asr.uploadLabel')} progress={uploadProgress} onCancel={()=>uploadController.current?.abort()} cancelLabel={isVideoTab?t('video.pause'):undefined}/>:null}
          <button className="primary" disabled={busy || !chosenModel.installed || Boolean(selectedHotwordIssue) || Boolean(groupSelectionIssue) || Boolean(fileLimitError) || (selectedHotwordIds.size>0&&hotwordsState!=='ready')} aria-describedby={selectedHotwordIssue ? 'asr-hotword-submit-error' : undefined} onClick={submit}>
            <Play size={18} />
            {submitLabel}
          </button>
        </div>
      </section>
      </aside>
      <section className="asr-results" id="asr-results" role="tabpanel" aria-labelledby="asr-tab-results" hidden={view!=='results'}>
        <aside className="asr-task-list">
          <h2>{t('asr.taskList')}</h2>
          <ResourceStatePanel state={jobsError?'error':jobsReady?'ready':'loading'} loadingLabel={t('asr.workspace.loadingJobs')} errorLabel={jobsError} retry={()=>void refreshJobs()}/>
          {jobsReady&&visibleJobs.length?<>
            <label className="asr-task-picker"><span className="sr-only">{t('asr.workspace.selectTask')}</span><select value={selectedSummary?.id||''} onChange={event=>{const job=visibleJobs.find(item=>item.id===event.target.value);if(job)onSelect(job)}}>
              {!selectedSummary?<option value="">{t('asr.workspace.selectTask')}</option>:null}
              {visibleJobs.map(job=><option key={job.id} value={job.id}>{job.display_name} · {t(`jobs.states.${job.state}`)}</option>)}
            </select></label>
            <div className="asr-recent-jobs">{visibleJobs.map(job=><JobMini key={job.id} job={job} isSelected={job.id===selectedSummary?.id} onOpen={onSelect}/>)}</div>
          </>:jobsReady&&!jobsError?<p>{t('asr.results.empty')}</p>:null}
          <button className="button asr-manage" onClick={onManageJobs}>{t('asr.workspace.manage')}</button>
        </aside>
      <section ref={resultPanel} tabIndex={-1} hidden={!jobsReady&&!selectedSummary} className={`result-panel${result?.video?' video-result':''}`} data-module="TRANSCRIPT_CORE / TRN_01">
        {resultNotice?<p className="notice" role="status">{resultNotice}</p>:null}
        {resultError?<div className="error" role="alert"><p>{resultError}</p><button className="button" onClick={()=>void refreshPeopleAndHotwords().then(()=>setResultError('')).catch(cause=>setResultError((cause as Error).message))}>{t('common.actions.retry')}</button></div>:null}
        {selectedSummary&&selectedSummary.state!=='succeeded'?<div className="asr-task-status" role="status">
          <JobMini job={selectedSummary} onOpen={onManageJobs}/>
          {selectedSummary.error_message?<p className="error">{selectedSummary.error_message}</p>:null}
          <p>{t('asr.workspace.pending')}</p>
        </div>:selectedJobId&&!selectedSummary&&jobsReady?(restoreState==='ready'?<p role="status">{t('asr.workspace.unavailable')}</p>:<ResourceStatePanel state={restoreState} loadingLabel={t('asr.results.loading')} errorLabel={restoreError} retry={()=>restoreSelected(true)}/>):!selectedSummary&&!jobsReady?<ResourceStatePanel state={jobsError?'error':'loading'} loadingLabel={t('asr.workspace.loadingJobs')} errorLabel={jobsError} retry={()=>void refreshJobs()}/>:
        selectedSummary && (!detail || (detail.state === 'loading'&&!detail.job)) ? (
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
              {detail?.state==='loading'?<small role="status">{t('asr.results.loading')}</small>:null}
              <div className="result-identity"><span>
                <FileAudio size={20} />
                {selected.display_name}
              </span>
              <span>
                {String(result.model_name || selected.request.model || 'Qwen3-ASR')} · {formatTime(duration)} · {(result.compute_device || selected.request.compute_device || 'gpu').toString().toUpperCase()} {result.precision || ''}
              </span></div>
              <TranscriptExport key={selected.id} job={selected}/>
            </div>
            <audio
              ref={audio}
              className="sr-only"
              preload={view==='results'?'metadata':'none'}
              src={playbackUrl}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onEnded={() => setPlaying(false)}
              onLoadStart={() => { setMediaLoading(true); setMediaError('') }}
              onLoadedMetadata={event => {
                const player=event.currentTarget
                if(player.currentSrc!==new URL(playbackUrl!,location.href).href)return
                setMediaLoading(false)
                const pending=pendingAudioSwitch.current
                if(pending){pendingAudioSwitch.current=undefined;player.currentTime=Math.min(pending.time,player.duration);setCurrentTime(player.currentTime);if(pending.playing)void player.play().catch(()=>setMediaError(t('asr.results.playbackError')))}
              }}
              onError={() => { pendingAudioSwitch.current=undefined;setMediaLoading(false); setMediaError(t('asr.results.playbackError')) }}
              onTimeUpdate={(event) => {
                if(pendingAudioSwitch.current)return
                const value = event.currentTarget.currentTime
                setCurrentTime(value)
                if (stopAt.current !== undefined && value >= stopAt.current - 0.03) {
                  event.currentTarget.pause()
                  stopAt.current = undefined
                }
              }}
            />
            <div className="asr-enhancement-row" role="group" aria-label={t('asr.enhancement.label')}>
              {enhancedAudio?<div className="asr-audio-listen">
                <span className="asr-audio-action-label">{t('asr.enhancement.listen')}</span>
                <div className="asr-audio-segmented" role="group" aria-label={t('asr.enhancement.listenTracks')}>
                  <button type="button" aria-pressed={!enhancedPlayback} disabled={Boolean(pendingAudioSwitch.current)} onClick={()=>switchAudio(false)}>{t('asr.enhancement.original')}</button>
                  <button type="button" aria-pressed={enhancedPlayback} disabled={Boolean(pendingAudioSwitch.current)} onClick={()=>switchAudio(true)}>{t('asr.enhancement.enhanced')}</button>
                </div>
              </div>:null}
              <div className="asr-audio-downloads" role="group" aria-label={t('asr.enhancement.downloadTracks')}>
                {originalDownloadUrl?<a className="button" href={originalDownloadUrl} download><Download size={16} aria-hidden="true"/>{t('asr.enhancement.downloadOriginal')}</a>:<button type="button" className="button" disabled aria-describedby="asr-original-unavailable"><Download size={16} aria-hidden="true"/>{t('asr.enhancement.downloadOriginal')}</button>}
                {enhancedAudio?<a className="button" href={enhancedAudio} download><Download size={16} aria-hidden="true"/>{t('asr.enhancement.download')}</a>:null}
              </div>
              {!originalDownloadUrl?<p id="asr-original-unavailable" className="asr-original-unavailable" role="note">{t('asr.enhancement.originalUnavailable')}</p>:null}
            </div>
            {!enhancedAudio&&result.audio_enhancement?.mode==='auto'?<p className="asr-enhancement-status" role="note">{t(result.audio_enhancement.status==='fallback'?'asr.enhancement.fallback':'asr.enhancement.bypassed')}</p>:null}
            {result.audio_enhancement?.speech_enhancement?<p className="asr-enhancement-status" role="note">{t('asr.enhancement.gainStatus',{status:t(result.audio_enhancement.speech_enhancement.reason==='strength_zero'?'asr.enhancement.zeroStatus':`asr.enhancement.status_${result.audio_enhancement.speech_enhancement.status}`)})}</p>:null}
            {result.audio_enhancement?.background_denoise?<p className="asr-enhancement-status" role="note">{t('asr.enhancement.denoiseStatus',{status:t(result.audio_enhancement.background_denoise.reason==='strength_zero'?'asr.enhancement.zeroStatus':`asr.enhancement.status_${result.audio_enhancement.background_denoise.status}`)})}</p>:null}
            {typeof result.audio_enhancement?.speech_enhancement?.strength==='number'&&typeof result.audio_enhancement?.background_denoise?.strength==='number'?<p className="asr-enhancement-status">{t('asr.enhancement.strengthSummary',{gain:Math.round(result.audio_enhancement.speech_enhancement.strength*100),noise:Math.round(result.audio_enhancement.background_denoise.strength*100)})}</p>:null}
            <div className="wave-area" hidden={Boolean(result.video)&&!videoWaveform}>
              {enhancedPlayback&&enhancedAudio?<EnhancedWaveform key={enhancedAudio} src={enhancedAudio} peaks={result.audio_enhancement?.waveform} currentTime={currentTime} duration={duration} onSeek={seek}/>:<Waveform peaks={result.waveform} currentTime={currentTime} duration={duration} onSeek={seek} />}
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
              {result.video?<><input className="video-playback-progress" type="range" aria-label={t('workspaceSplit.playbackPosition')} min={0} max={duration||0} step={.1} value={Math.min(currentTime,duration)} onChange={event=>{stopAt.current=undefined;seekWord(Number(event.target.value))}}/><button className="button waveform-toggle" aria-pressed={videoWaveform} onClick={()=>setVideoWaveform(value=>!value)}>{t('workspaceSplit.waveform')}</button></>:null}
              <span className="grow" />
              <span>
                {result.language} · {result.timestamp_precision === 'word_or_character' ? t('asr.results.wordAlignment') : t('asr.results.segmentTimestamps')}
              </span>
            </div>
            {mediaLoading && !mediaError ? <p className="media-loading" role="status">{t('asr.results.loadingAudio')}</p> : null}
            {alignmentDowngraded ? (
              <p className="notice alignment-notice" role="note">
                {t('asr.results.alignmentDowngraded',{language:result.language})}
              </p>
            ) : null}
            {mediaError ? (
              <p className="media-error" role="alert">
                {mediaError} <a href={sourceUrl(selected.id, true)}>{t('asr.results.downloadSource')}</a>
                {' '}<button type="button" className="button" onClick={() => {
                  audio.current?.load()
                  stopAt.current = undefined
                  setCurrentTime(0)
                  setPlaying(false)
                  setMediaError('')
                  setMediaLoading(true)
                }}>{t('asr.results.retryPlayback')}</button>
              </p>
            ) : null}
            {result.video?<div className="video-view-tabs" role="group" aria-label={t('video.readingMode')}><button aria-pressed={videoReading} onClick={()=>{checkpointWorkspace();setVideoReading(true)}}>{t('video.reading')}</button><button aria-pressed={!videoReading} onClick={()=>{checkpointWorkspace();setVideoReading(false)}}>{t('video.details')}</button><div className="video-toolbar" ref={setVideoToolbar}/></div>:null}
            {result.video&&videoReading?<VideoTranscript key={selected.id} job={selected} currentTime={currentTime} toolbar={videoToolbar} editing={videoEditing} onEditingChange={setVideoEditing} onUpdated={onJobResultUpdated} onSeek={seconds=>{stopAt.current=undefined;seekWord(seconds)}} onEnroll={(speakerId,segmentIds)=>{audio.current?.pause();setVideoEnrollment({jobId:selected.id,speakerId,segmentIds})}} onRename={id=>{const segment=result.segments?.find(item=>item.speaker===id);if(segment)openRename(segment)}}/>:<>
            <div className="transcript-tools">
              <Search size={18} />
              <input value={query} onChange={(event) => {resetSegmentFilter(event.target.value,speakerFilter);setQuery(event.target.value)}} placeholder={t('asr.results.search')} />
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
            </>}
          </>
        ) : (
          <div className="empty">
            <FileAudio size={52} />
            <h2>{t('asr.results.empty')}</h2>
            <p>{t('asr.results.emptyHelp')}</p>
          </div>
        )}
      </section>
      </section>
      {enhancementSettingsOpen&&gainStrengthCapability&&noiseStrengthCapability?<EnhancementSettings gain={gainStrengthCapability} noise={noiseStrengthCapability} gainValue={preferences.speechEnhancementStrength} noiseValue={preferences.backgroundDenoiseStrength} onClose={()=>setEnhancementSettingsOpen(false)} onChange={(gain,noise)=>setPreferences(current=>{const next={...current,speechEnhancementStrength:gain,backgroundDenoiseStrength:noise};saveAsrPreferences(next);return next})}/>:null}
      {discardOpen?<Modal title={t('video.discard')} closeLabel={t('common.actions.close')} onClose={()=>setDiscardOpen(false)}><p>{t('video.discardHelp')}</p><div className="modal-actions"><button className="button" onClick={()=>setDiscardOpen(false)}>{t('common.actions.cancel')}</button><button className="primary" onClick={()=>void discardVideoUpload().then(()=>{setResume(undefined);setDiscardOpen(false);setError('')}).catch(cause=>{setError(cause.message);setDiscardOpen(false)})}>{t('video.discard')}</button></div></Modal>:null}
      {hotwordsOpen?<Modal title={t('asr.hotwords.label')} closeLabel={t('common.actions.close')} onClose={()=>setHotwordsOpen(false)}>
        <ResourceStatePanel state={hotwordsState} loadingLabel={t('asr.workspace.loadingHotwords')} errorLabel={t('asr.workspace.hotwordsUnavailable')} retry={()=>void refreshHotwords().catch(()=>undefined)}/>
        {selectedHotwordIds.size?<button className="button" onClick={()=>updatePreference('hotwordListIds',[])}>{t('asr.workspace.clearHotwords')}</button>:null}
        {hotwordsState==='ready'?<><input aria-label={t('asr.workspace.searchHotwords')} placeholder={t('asr.workspace.searchHotwords')} value={hotwordQuery} onChange={event=>setHotwordQuery(event.target.value)}/>
        {hotwordLists.length>0&&matchingHotwords.length===0?<p role="status">{t('asr.workspace.noHotwordMatches')}</p>:null}
        <fieldset className="hotword-picker">
          <legend>{t('asr.hotwords.label')}</legend>
          {hotwordLists.length ? (
            matchingHotwords.map((item) => {
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
                    {hotwordListDisplayName(item,t)}
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

        </>:null}
        <button className="button" onClick={()=>setHotwordsOpen(false)}>{t('asr.workspace.close')}</button>
      </Modal>:null}
      {renameSpeaker ? (
        <Modal title={t('asr.rename.title')} closeLabel={t('asr.rename.close')} onClose={() => {if(!renameBusy)setRenameSpeaker(undefined)}}>
          {renameError?<p className="error" role="alert">{renameError}</p>:null}
          <p>{t('asr.rename.help',{name:renameSpeaker.label})}</p>
          <p>{t('asr.rename.mergeHelp')}</p>
          <ResourceStatePanel state={voiceprintsState} loadingLabel={t('voiceprints.loadingPeople')} errorLabel={t('voiceprints.loadFailed')} retry={() => void refreshVoiceprints().catch(() => {})} />
          <label>
            {t('asr.rename.libraryPerson')}
            <select value={renamePersonValue} disabled={renameBusy || voiceprintsState !== 'ready' || !voiceprints.length} onChange={event => {
              const person = voiceprints.find(item => item.id === event.target.value)
              setRenamePersonId(person?.id || '')
              if (person) {
                setRenameValue(person.name)
                setRenameNote(person.note || '')
              }
            }}>
              <option value="">{voiceprintsState === 'ready' ? t(voiceprints.length ? 'asr.rename.manual' : 'asr.rename.emptyPeople') : t(voiceprintsState === 'loading' ? 'voiceprints.loadingPeople' : 'voiceprints.loadFailed')}</option>
              {voiceprintsState === 'ready' ? voiceprints.map(person => <option key={person.id} value={person.id}>{voiceprintPersonLabel(person)}</option>) : null}
            </select>
            <small>{t('asr.rename.libraryHelp')}</small>
          </label>
          <label>
            {t('asr.rename.newName')}
            <input data-initial-focus value={renameValue} disabled={renameBusy} onChange={(event) => {
              setRenamePersonId('')
              setRenameValue(event.target.value)
            }} />
          </label>
          <label>
            {t('voiceprints.noteOptional')}
            <input value={renameNote} maxLength={20} disabled={renameBusy} placeholder={t('voiceprints.notePlaceholder')} onChange={(event) => {
              setRenamePersonId('')
              setRenameNote(event.target.value)
            }} />
            <small>{t('voiceprints.noteCount',{current:renameNote.trim().length})}</small>
          </label>
          <button className="primary" disabled={renameBusy || !renameValue.trim()} onClick={saveRename}>
            {t('asr.rename.save')}
          </button>
        </Modal>
      ) : null}
      {groupsOpen?<Modal title={t('voiceprintGroups.scopeTitle')} closeLabel={t('common.actions.cancel')} onClose={()=>setGroupsOpen(false)}>
        <p>{t('voiceprintGroups.scopeHelp')}</p>
        <ResourceStatePanel state={voiceprintGroupsState} loadingLabel={t('voiceprintGroups.loading')} errorLabel={t('voiceprintGroups.failed')} retry={()=>void refreshVoiceprintGroups().catch(()=>{})}/>
        {groupSelectionWarning?<p className="notice" role="status">{t('voiceprintGroups.staleSelection')}</p>:null}
        <label className="group-choice"><input type="checkbox" checked={!manualGroups} disabled={voiceprintGroupsState!=='ready'} onChange={event=>{setGroupSelectionWarning(false);setPreferences(current=>{const next={...current,voiceprintGroupMode:event.target.checked?'all' as const:'selected' as const,voiceprintGroupIds:event.target.checked?[]:voiceprintGroups.map(group=>group.id)};saveAsrPreferences(next);return next})}}/>{t('voiceprintGroups.allGroups')}</label>
        <div className="group-choice-list">{voiceprintGroupsState==='ready'?voiceprintGroups.map(group=><label className="group-choice" key={group.id}><input type="checkbox" checked={!manualGroups||preferences.voiceprintGroupIds.includes(group.id)} onChange={event=>{setGroupSelectionWarning(false);setPreferences(current=>{const selected=new Set(current.voiceprintGroupMode==='all'?voiceprintGroups.map(item=>item.id):current.voiceprintGroupIds);if(event.target.checked)selected.add(group.id);else selected.delete(group.id);const next={...current,voiceprintGroupMode:'selected' as const,voiceprintGroupIds:[...selected]};saveAsrPreferences(next);return next})}}/>{groupLabel(group,t('voiceprintGroups.ungrouped'))}<small>{group.person_count}</small></label>):null}</div>
        {groupSelectionIssue?<p className="error" role="alert">{groupSelectionIssue}</p>:null}
        <div className="modal-actions"><button className="primary" onClick={()=>setGroupsOpen(false)}>{t('common.actions.done')}</button></div>
      </Modal>:null}
      {videoEnrollment&&selected&&videoEnrollment.jobId===selected.id?<VideoSpeakerEnrollment key={`${selected.id}:${videoEnrollment.speakerId}`} job={selected} speakerId={videoEnrollment.speakerId} initialSegmentIds={videoEnrollment.segmentIds} people={voiceprints} peopleState={voiceprintsState} refreshPeople={refreshVoiceprints} refreshPeopleAndHotwords={refreshPeopleAndHotwords} onClose={()=>setVideoEnrollment(undefined)} onSuccess={count=>{setVideoEnrollment(undefined);setResultNotice(t('video.enrolledCount',{count}))}}/>:null}
      {libraryOpen ? (
        <Modal title={t('asr.library.title')} closeLabel={t('asr.library.close')} onClose={() => {if(!libraryBusy)setLibraryOpen(false)}}>
          <p>
            {t('asr.library.help',{count:selectedSegments.size,speaker:selectionLabel})}
          </p>
          {libraryError?<p className="error" role="alert">{libraryError}</p>:null}
          {libraryAmbiguous?<p>{t('voiceprintNames.ambiguousPerson')}</p>:null}
          {voiceprints.length ? (
            <label>
              {t('asr.library.person')}
              <select value={targetPersonId} onChange={(event) => setTargetPersonId(event.target.value)}>
                {libraryAmbiguous?<option value="__choose__" disabled>{t('voiceprintNames.choosePerson')}</option>:null}
                <option value="">{t('asr.library.newPerson')}</option>
                {voiceprints.map((person) => (
                  <option key={person.id} value={person.id}>
                    {voiceprintPersonLabel(person)} · {t('voiceprints.sampleCount',{count:person.sample_count})}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {!targetPersonId ? (
            <>
              <label>
                {t('asr.library.newPersonName')}
                <input ref={libraryNameRef} value={newPersonName} maxLength={80} placeholder={t('asr.library.namePlaceholder')} onChange={(event) => setNewPersonName(event.target.value)} />
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
            <p className="notice" hidden={targetPersonId==='__choose__'}>
              {t('asr.library.matched')}
              {voiceprints.filter(person=>person.id===targetPersonId).map(voiceprintPersonLabel).join('')}
            </p>
          )}
          <button className="primary" disabled={libraryBusy || targetPersonId==='__choose__' || (!targetPersonId && !newPersonName.trim())} onClick={addToLibrary}>
            {libraryBusy ? t('voiceprints.saving') : t('asr.library.confirm')}
          </button>
        </Modal>
      ) : null}
    </div>
  )
}
