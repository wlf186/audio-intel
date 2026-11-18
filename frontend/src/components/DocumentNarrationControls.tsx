import {useTranslation} from 'react-i18next'
import type {NarrationKind,NarrationOptions} from '../lib/types'

export function DocumentNarrationControls({options,disabled,onChange}:{options:NarrationOptions;disabled:boolean;onChange:(value:NarrationOptions)=>void}){
 const {t}=useTranslation()
 return <div className="document-narration-controls">
  <label><input type="checkbox" checked={options.skip_enabled} disabled={disabled} onChange={e=>onChange({...options,skip_enabled:e.target.checked})}/>{t('document.narration.skip')}</label>
  <div className="document-narration-kinds">{(['page_number','note_reference','note_body'] as NarrationKind[]).map(kind=><label key={kind}><input type="checkbox" checked={options.skip_kinds.includes(kind)} disabled={disabled||!options.skip_enabled} onChange={e=>onChange({...options,skip_kinds:e.target.checked?[...options.skip_kinds,kind]:options.skip_kinds.filter(k=>k!==kind)})}/>{t(`document.narration.${kind}`)}</label>)}</div>
  <label><input type="checkbox" checked={options.natural_pauses} disabled={disabled} onChange={e=>onChange({...options,natural_pauses:e.target.checked})}/>{t('document.narration.pauses')}</label>
  <p>{t('document.narration.help')}</p>
 </div>
}
