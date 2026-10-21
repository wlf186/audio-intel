import {useEffect,useMemo,useRef,useState} from 'react'
import {useTranslation} from 'react-i18next'
import {api,formatTime,HttpError} from '../lib/api'
import type {Job,ResourceState,Segment,VoiceprintGroup,VoiceprintPerson} from '../lib/types'
import {ungroupedId} from './VoiceprintGroups'
import {VoiceprintGroupSelect} from './VoiceprintGroupSelect'
import {enrollmentPerson} from '../lib/voiceprintEnrollment'
import {voiceprintPersonLabel} from '../lib/presentation'
import {Modal} from './Modal'
import {ResourceStatePanel} from './ResourceStatePanel'

type Props={job:Job;speakerId:string;initialSegmentIds?:number[];people:VoiceprintPerson[];peopleState:ResourceState;groups:VoiceprintGroup[];groupsState:ResourceState;refreshGroups:()=>Promise<void>;refreshPeople:()=>Promise<void>;refreshPeopleAndHotwords:()=>Promise<void>;onClose:()=>void;onSuccess:(count:number)=>void}
export function VideoSpeakerEnrollment({job,speakerId,initialSegmentIds=[],people,peopleState,groups,groupsState,refreshGroups,refreshPeople,refreshPeopleAndHotwords,onClose,onSuccess}:Props){
 const {t}=useTranslation()
 const [step,setStep]=useState<'select'|'person'>('select')
 const [selected,setSelected]=useState<Set<number>>(()=>new Set(initialSegmentIds))
 const [visible,setVisible]=useState(()=>{const values=(job.result?.segments||[]).filter(segment=>segment.speaker===speakerId);const last=Math.max(...initialSegmentIds.map(id=>values.findIndex(segment=>segment.id===id)),0);return Math.max(40,Math.ceil((last+1)/40)*40)})
 const [personId,setPersonId]=useState('')
 const [name,setName]=useState('')
 const [note,setNote]=useState('')
 const [hotword,setHotword]=useState(true)
 const [groupId,setGroupId]=useState(ungroupedId)
 const [ambiguous,setAmbiguous]=useState(false)
 const [busy,setBusy]=useState(false)
 const [error,setError]=useState('')
 const [playing,setPlaying]=useState<number>()
 const player=useRef<HTMLAudioElement>(null)
 const end=useRef<number|undefined>(undefined)
 const active=useRef(true)
 const submitting=useRef(false)
 const speaker=job.result?.speakers?.find(item=>item.id===speakerId)
 const segments=useMemo(()=>[...new Map((job.result?.segments||[]).filter(item=>item.speaker===speakerId).map(item=>[item.id,item])).values()].sort((a,b)=>a.start-b.start||a.id-b.id),[job.result?.segments,speakerId])
 const imported=useMemo(()=>new Set(people.flatMap(person=>person.samples||[]).filter(sample=>sample.source_job_id===job.id).map(sample=>sample.source_segment_id)),[people,job.id])
 const selectedIds=segments.filter(segment=>selected.has(segment.id)&&!imported.has(segment.id)).map(segment=>segment.id)
 const stop=()=>{player.current?.pause();end.current=undefined;setPlaying(undefined)}
 useEffect(()=>{document.querySelector<HTMLElement>('.video-enrollment-row:has(input:checked)')?.scrollIntoView({block:'nearest'})},[])
 useEffect(()=>{active.current=true;const audio=player.current;return()=>{active.current=false;audio?.pause()}},[])
 function close(){if(submitting.current)return;stop();onClose()}
 async function preview(segment:Segment){
  const audio=player.current;if(!audio)return
  if(playing===segment.id){stop();return}
  setError('');end.current=segment.end;audio.currentTime=segment.start;setPlaying(segment.id)
  try{await audio.play()}catch{if(active.current){stop();setError(t('asr.results.playbackError'))}}
 }
 function next(){
  stop();setError('')
  const {identity,match,ambiguous:needsChoice}=enrollmentPerson(speaker,speaker?.label||speakerId,people)
  setPersonId(match?.id||(needsChoice?'__choose__':''));setAmbiguous(needsChoice)
  setName(/^Speaker[ _]\d+$/i.test(identity.name)?'':identity.name);setNote(identity.note);setStep('person')
 }
 async function submit(){
  if(submitting.current||peopleState!=='ready'||!selectedIds.length||personId==='__choose__'||(!personId&&(groupsState!=='ready'||!groups.some(group=>group.id===groupId))))return
  submitting.current=true;setBusy(true);setError('')
  let created=false,creating=!personId
  try{
   let target=personId
   if(!target){const person=await api.addVoiceprintPerson(name.trim(),note.trim()||null,hotword,groupId);target=person.id;created=true;creating=false;if(active.current)setPersonId(target)}
   await api.addAsrSamples(target,job.id,selectedIds)
   // A completed import is not a failed import if refreshing the library fails.
   await (created?refreshPeopleAndHotwords():refreshPeople()).catch(()=>undefined)
   if(active.current)onSuccess(selectedIds.length)
  }catch(cause){
   if(active.current)setError(cause instanceof HttpError&&cause.status===409&&creating?t('voiceprintNames.personConflict'):(cause as Error).message)
   if(creating&&cause instanceof HttpError&&cause.status===422)await refreshGroups().catch(()=>undefined)
   if(created)await refreshPeopleAndHotwords().catch(()=>undefined)
   else if(cause instanceof HttpError&&cause.status===409&&!creating)await refreshPeople().catch(()=>undefined)
  }finally{ submitting.current=false;if(active.current)setBusy(false) }
 }
 return <Modal className="video-enrollment" title={t('asr.library.title')} closeLabel={t('asr.library.close')} onClose={close}>
  <audio ref={player} src={job.result?.playback_url||`/api/v1/jobs/${job.id}/playback`} preload="none" onTimeUpdate={()=>{if(end.current!==undefined&&player.current&&player.current.currentTime>=end.current)stop()}} onEnded={stop} onError={()=>{stop();setError(t('asr.results.playbackError'))}}/>
  <p className="enrollment-step" role="status">{t('workspaceUx.enrollmentStep',{step:step==='select'?1:2})}</p>
  <p>{t('video.enrollHelp',{speaker:speaker?.label||speakerId})}</p>
  <ResourceStatePanel state={peopleState} loadingLabel={t('voiceprints.loadingPeople')} errorLabel={t('voiceprints.loadFailed')} retry={()=>void refreshPeople().catch(()=>undefined)}/>
  {error?<p className="error" role="alert">{error}</p>:null}
  {step==='select'?<>
   <div className="video-enrollment-list modal-scroll-body">
    {segments.slice(0,visible).map(segment=><div className="video-enrollment-row" key={segment.id}>
     <label><input type="checkbox" aria-label={t('asr.segment.select',{number:segment.id+1})} checked={selectedIds.includes(segment.id)} disabled={peopleState!=='ready'||imported.has(segment.id)} onChange={()=>setSelected(current=>{const next=new Set(current);if(next.has(segment.id))next.delete(segment.id);else next.add(segment.id);return next})}/><span><b>{formatTime(segment.start)} – {formatTime(segment.end)}</b><span>{segment.text}</span>{peopleState==='ready'&&imported.has(segment.id)?<small>{t('video.alreadyImported')}</small>:null}</span></label>
     <button className="button" onClick={()=>void preview(segment)} aria-label={t(playing===segment.id?'video.stopSample':'video.previewSample',{number:segment.id+1})}>{t(playing===segment.id?'asr.results.pause':'asr.results.play')}</button>
    </div>)}
    {!segments.length?<p>{t('video.noSpeakerSegments')}</p>:null}
    {visible<segments.length?<button className="button" onClick={()=>setVisible(value=>value+40)}>{t('asr.results.loadMore')}</button>:null}
   </div>
   <div className="modal-actions"><span>{t('asr.results.selectedSegments',{count:selectedIds.length,speaker:speaker?.label||speakerId})}</span><button className="primary" disabled={peopleState!=='ready'||!selectedIds.length} onClick={next}>{t('video.choosePerson')}</button></div>
  </>:<><div className="modal-scroll-body">
   <p>{t('asr.library.help',{count:selectedIds.length,speaker:speaker?.label||speakerId})}</p>
   {ambiguous?<p>{t('voiceprintNames.ambiguousPerson')}</p>:null}
   <label>{t('asr.library.person')}<select disabled={busy||peopleState!=='ready'} value={personId} onChange={event=>setPersonId(event.target.value)}><option value="">{t('asr.library.newPerson')}</option>{ambiguous?<option value="__choose__" disabled>{t('voiceprintNames.choosePerson')}</option>:null}{peopleState==='ready'?people.map(person=><option value={person.id} key={person.id}>{voiceprintPersonLabel(person)}</option>):null}</select></label>
   {!personId?<>
    <label>{t('asr.library.newPersonName')}<input disabled={busy} value={name} maxLength={80} onChange={event=>setName(event.target.value)}/></label>
    <label>{t('voiceprints.noteOptional')}<input disabled={busy} value={note} maxLength={20} onChange={event=>setNote(event.target.value)}/></label>
    <label className="toggle-label person-hotword-toggle"><input type="checkbox" disabled={busy} checked={hotword} onChange={event=>setHotword(event.target.checked)}/><span>{t('voiceprints.addToHotwords')}</span><small>{t('voiceprints.addToHotwordsHelp')}</small></label>
    <VoiceprintGroupSelect groups={groups} state={groupsState} value={groupId} onChange={setGroupId} refresh={refreshGroups} disabled={busy}/>
   </>:null}
   {!selectedIds.length?<p role="alert">{t('video.selectionUnavailable')}</p>:null}
   </div>
   <div className="modal-actions"><button className="button" disabled={busy} onClick={()=>{setError('');setStep('select')}}>{t('video.backToSamples')}</button><button className="primary" disabled={busy||peopleState!=='ready'||!selectedIds.length||personId==='__choose__'||(!personId&&(!name.trim()||groupsState!=='ready'||!groups.some(group=>group.id===groupId)))||(Boolean(personId)&&!people.some(person=>person.id===personId))} onClick={()=>void submit()}>{t(busy?'voiceprints.saving':'asr.library.confirm')}</button></div>
  </>}
 </Modal>
}
