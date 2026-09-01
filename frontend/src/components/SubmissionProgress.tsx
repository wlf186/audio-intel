import {XCircle} from 'lucide-react'
import {size,type SubmissionProgress as Progress} from '../lib/api'
import {useTranslation} from 'react-i18next'
import {resolvedLocale} from '../i18n'

type Props={label:string;progress:Progress;onCancel?:()=>void}

export function SubmissionProgress({label,progress,onCancel}:Props){
 const {t}=useTranslation()
 const locale=resolvedLocale()
 const uploading=progress.phase==='uploading'
 const title=progress.phase==='preparing'?t('submission.preparing',{label}):uploading?t('submission.uploading',{label}):t('submission.creating',{label})
 const transferred=progress.totalBytes?`${size(progress.loadedBytes,locale)} / ${size(progress.totalBytes,locale)}`:progress.loadedBytes?t('submission.sent',{size:size(progress.loadedBytes,locale)}):''
 const value=uploading?progress.percent:undefined
 return <div className={`submission-progress ${progress.phase}`} role="region" aria-label={t('submission.status',{label})}>
  <div><b aria-live="polite">{title}</b>{value!==undefined?<strong>{value}%</strong>:null}</div>
  <progress max={100} value={value} aria-label={t('submission.progress',{label})} aria-valuetext={progress.phase==='creating'?t('submission.serverCreating'):value!==undefined?`${value}%`:t('submission.preparingGeneric')}/>
  <small>{progress.phase==='creating'?t('submission.keepOpen'):transferred||t('submission.connecting')}</small>
  {onCancel&&progress.phase!=='creating'?<button type="button" className="cancel-upload" onClick={onCancel}><XCircle size={16}/>{t('submission.cancel')}</button>:null}
 </div>
}
