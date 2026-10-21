import {useTranslation} from 'react-i18next'
import type {ResourceState,VoiceprintGroup} from '../lib/types'
import {groupLabel} from './VoiceprintGroups'
import {ResourceStatePanel} from './ResourceStatePanel'

export function VoiceprintGroupSelect({groups,state,value,onChange,refresh,disabled=false}:{groups:VoiceprintGroup[];state:ResourceState;value:string;onChange:(id:string)=>void;refresh:()=>Promise<void>;disabled?:boolean}){
 const {t}=useTranslation()
 const valid=groups.some(group=>group.id===value)
 return <div className="enrollment-group">
  <ResourceStatePanel state={state} loadingLabel={t('voiceprintGroups.loading')} errorLabel={t('voiceprintGroups.failed')} retry={()=>void refresh().catch(()=>undefined)}/>
  <label>{t('voiceprintGroups.filter')}<select value={state==='ready'&&valid?value:''} disabled={disabled||state!=='ready'} onChange={event=>onChange(event.target.value)}>
   {state!=='ready'||!valid?<option value="" disabled>{t(state==='loading'?'voiceprintGroups.loading':state==='error'?'voiceprintGroups.failed':'voiceprintGroups.unavailable')}</option>:null}
   {state==='ready'?groups.map(group=><option key={group.id} value={group.id}>{groupLabel(group,t('voiceprintGroups.ungrouped'))}</option>):null}
  </select></label>
  {state==='ready'&&!valid?<p className="error" role="alert">{t('voiceprintGroups.unavailable')}</p>:null}
 </div>
}
