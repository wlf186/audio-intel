import {useTranslation} from 'react-i18next'
import type {ResourceState,VoiceprintGroup} from '../lib/types'
import {groupLabel} from './VoiceprintGroups'
import {ResourceStatePanel} from './ResourceStatePanel'

export function VoiceprintScope({groups,state,mode,ids,onChange,refresh}:{groups:VoiceprintGroup[];state:ResourceState;mode:'all'|'selected';ids:string[];onChange:(mode:'all'|'selected',ids:string[])=>void;refresh:()=>Promise<void>}){
 const {t}=useTranslation()
 const selected=groups.filter(group=>ids.includes(group.id))
 const all=groups.length>0&&selected.length===groups.length
 const partial=selected.length>0&&!all
 return <>
  <ResourceStatePanel state={state} loadingLabel={t('voiceprintGroups.loading')} errorLabel={t('voiceprintGroups.failed')} retry={()=>void refresh().catch(()=>undefined)}/>
  <div role="radiogroup" aria-label={t('voiceprintGroups.scopeMode')}>
   <label className="group-choice"><input type="radio" name="voiceprint-scope" checked={mode==='all'} disabled={state!=='ready'} onChange={()=>onChange('all',[])}/>{t('voiceprintGroups.allGroups')}</label>
   <label className="group-choice"><input type="radio" name="voiceprint-scope" checked={mode==='selected'} disabled={state!=='ready'} onChange={()=>onChange('selected',[])}/>{t('voiceprintGroups.specifiedGroups')}</label>
  </div>
  {mode==='all'?<p>{t('voiceprintGroups.allGroupsHelp')}</p>:<>
   <p>{t('voiceprintGroups.specifiedGroupsHelp')}</p>
   {state==='ready'?<div role="group" aria-label={t('voiceprintGroups.specifiedGroups')}>
    <label className="group-choice"><input type="checkbox" ref={node=>{if(node)node.indeterminate=partial}} checked={all} onChange={()=>onChange('selected',all?[]:groups.map(group=>group.id))}/>{t('voiceprintGroups.selectAllGroups')}</label>
    <div className="group-choice-list">{groups.map(group=><label className="group-choice" key={group.id}><input type="checkbox" checked={ids.includes(group.id)} onChange={event=>onChange('selected',event.target.checked?[...ids,group.id]:ids.filter(id=>id!==group.id))}/>{groupLabel(group,t('voiceprintGroups.ungrouped'))}<small>{group.person_count}</small></label>)}</div>
   </div>:null}
  </>}
 </>
}
