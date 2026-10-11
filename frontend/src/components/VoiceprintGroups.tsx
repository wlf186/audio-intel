import {useState} from 'react'
import {Pencil,Plus,Trash2} from 'lucide-react'
import {useTranslation} from 'react-i18next'
import {api} from '../lib/api'
import type {ResourceState,VoiceprintGroup} from '../lib/types'
import {Modal} from './Modal'
import {ConfirmDialog} from './ConfirmDialog'
import {ResourceStatePanel} from './ResourceStatePanel'

export const ungroupedId='voiceprint_ungrouped'
export function groupLabel(group:VoiceprintGroup,ungrouped:string){return group.builtin?ungrouped:group.name}

export function VoiceprintGroups({groups,state,value,onChange,refresh,onChanged,disabled}:{groups:VoiceprintGroup[];state:ResourceState;value:string;onChange:(id:string)=>void;refresh:()=>Promise<void>;onChanged:()=>Promise<void>;disabled:boolean}){
 const {t}=useTranslation()
 const [editor,setEditor]=useState<'create'|'rename'>()
 const [name,setName]=useState('')
 const [confirm,setConfirm]=useState<VoiceprintGroup>()
 const [busy,setBusy]=useState(false)
 const [error,setError]=useState('')
 const current=groups.find(group=>group.id===value)
 async function save(){
  setBusy(true);setError('')
  try{if(editor==='rename'&&current)await api.renameVoiceprintGroup(current.id,name.trim());else await api.addVoiceprintGroup(name.trim());setEditor(undefined);await onChanged()}
  catch(cause){setError((cause as Error).message)}finally{setBusy(false)}
 }
 async function remove(){
  if(!confirm)return
  setBusy(true);setError('')
  try{await api.removeVoiceprintGroup(confirm.id);setConfirm(undefined);onChange('');await onChanged()}
  catch(cause){setError((cause as Error).message)}finally{setBusy(false)}
 }
 return <div className="voiceprint-group-controls">
  <ResourceStatePanel state={state} loadingLabel={t('voiceprintGroups.loading')} errorLabel={t('voiceprintGroups.failed')} retry={()=>void refresh().catch(()=>{})}/>
  <label>{t('voiceprintGroups.filter')}<select aria-label={t('voiceprintGroups.filter')} value={value} disabled={disabled||busy||state!=='ready'} onChange={event=>onChange(event.target.value)}><option value="">{t('voiceprintGroups.allPeople')}</option>{state==='ready'?groups.map(group=><option key={group.id} value={group.id}>{groupLabel(group,t('voiceprintGroups.ungrouped'))} ({group.person_count})</option>):null}</select></label>
  <div className="group-edit-actions">
   <button className="button" disabled={disabled||busy||state!=='ready'} onClick={()=>{setEditor('create');setName('');setError('')}}><Plus size={16}/>{t('voiceprintGroups.create')}</button>
   <button className="icon-button" aria-label={t('voiceprintGroups.rename')} disabled={disabled||busy||!current||current.builtin} onClick={()=>{setEditor('rename');setName(current?.name||'');setError('')}}><Pencil size={16}/></button>
   <button className="icon-button danger" aria-label={t('voiceprintGroups.delete')} disabled={disabled||busy||!current||current.builtin} onClick={()=>{setConfirm(current);setError('')}}><Trash2 size={16}/></button>
  </div>
  {error&&!editor?<p className="error" role="alert">{error}</p>:null}
  {editor?<Modal title={t(editor==='create'?'voiceprintGroups.create':'voiceprintGroups.rename')} closeLabel={t('common.actions.cancel')} onClose={()=>{if(!busy)setEditor(undefined)}}>
   {error?<p className="error" role="alert">{error}</p>:null}<label>{t('voiceprintGroups.name')}<input data-initial-focus value={name} maxLength={80} disabled={busy} onChange={event=>setName(event.target.value)}/></label>
   <div className="modal-actions"><button className="button" disabled={busy} onClick={()=>setEditor(undefined)}>{t('common.actions.cancel')}</button><button className="primary" disabled={busy||!name.trim()} onClick={()=>void save()}>{t('common.actions.save')}</button></div>
  </Modal>:null}
  {confirm?<ConfirmDialog title={t('voiceprintGroups.delete')} description={t('voiceprintGroups.deleteDescription',{name:confirm.name})} confirmLabel={t('common.actions.delete')} busy={busy} danger onClose={()=>{if(!busy)setConfirm(undefined)}} onConfirm={()=>void remove()}/>:null}
 </div>
}
