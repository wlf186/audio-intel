import {useId} from 'react'
import {useTranslation} from 'react-i18next'
import type {NarrationKind,NarrationOptions} from '../lib/types'
import {SettingToggleRow} from './SettingToggleRow'

type Props={
 options:NarrationOptions
 counts?:Record<string,number>
 disabled:boolean
 onChange:(value:NarrationOptions)=>void
}
const kinds:NarrationKind[]=['page_number','note_reference','note_body']

export function DocumentNarrationControls({options,counts,disabled,onChange}:Props){
 const {t}=useTranslation()
 const id=useId()
 function toggleKind(kind:NarrationKind,checked:boolean){
  const skip_kinds=checked?[...options.skip_kinds,kind]:options.skip_kinds.filter(value=>value!==kind)
  onChange({...options,skip_kinds})
 }
 return <div className="document-narration-controls">
  <SettingToggleRow id={id+'-skip'} label={t('document.narration.skip')} description={t('document.narration.help')}
   checked={options.skip_enabled} disabled={disabled} onChange={skip_enabled=>onChange({...options,skip_enabled})}/>
  <fieldset className="document-narration-kinds" disabled={disabled||!options.skip_enabled}>
   <legend>{t('document.narration.categories')}</legend>
   {kinds.map(kind=>{
    const count=counts?.[kind]
    const countId=id+'-'+kind+'-count'
    return <label key={kind}>
     <input type="checkbox" aria-label={t(`document.narration.${kind}`)} aria-describedby={count!=null?countId:undefined}
      checked={options.skip_kinds.includes(kind)} onChange={e=>toggleKind(kind,e.target.checked)}/>
     <span>{t(`document.narration.${kind}`)}</span>
     {count!=null?<small id={countId}>{t('document.narration.detected',{count})}</small>:null}
    </label>
   })}
  </fieldset>
  <SettingToggleRow id={id+'-pauses'} label={t('document.narration.pauses')} description={t('document.narration.pauseTimes')}
   checked={options.natural_pauses} disabled={disabled} onChange={natural_pauses=>onChange({...options,natural_pauses})}/>
 </div>
}
