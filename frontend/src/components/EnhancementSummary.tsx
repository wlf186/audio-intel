import {useTranslation} from 'react-i18next'
import type {AsrEnhancement} from '../lib/types'
import {ActionPopover} from './ActionPopover'

export function EnhancementSummary({value}:{value?:AsrEnhancement}){
 const {t}=useTranslation()
 if(!value)return null
 const controls=[value.speech_enhancement,value.background_denoise]
 const partial=value.status!=='fallback'&&(value.reason==='partial_fallback'||controls.some(control=>control?.status==='fallback'))
 const summary=t(partial?'asr.enhancement.summaryPartial':`asr.enhancement.summary_${value.status}`)
 const strengths=controls.map((control,index)=>typeof control?.strength==='number'?t(index?'asr.enhancement.noiseValue':'asr.enhancement.gainValue',{value:Math.round(control.strength*100)}):'').filter(Boolean)
 return <div className={`enhancement-summary${partial||value.status==='fallback'?' enhancement-partial':''}`} role="note"><span>{[summary,...strengths].join(' · ')}</span><ActionPopover label={t('asr.enhancement.processingDetails')} className="enhancement-details"><dl>{controls.map((control,index)=>control?<div key={index}><dt>{t(index?'asr.enhancement.denoiseName':'asr.enhancement.gainName')}</dt><dd>{t(control.reason==='strength_zero'?'asr.enhancement.zeroStatus':`asr.enhancement.status_${control.status}`)}{control.status==='fallback'?<small>{t(index===1&&control.reason==='model_unavailable'?'asr.enhancement.denoiseUnavailable':'asr.enhancement.unavailable')}</small>:null}</dd></div>:null)}{!controls.some(Boolean)?<div><dt>{summary}</dt><dd>{t(value.status==='fallback'?'asr.enhancement.fallback':value.status==='off'?'asr.enhancement.status_off':value.status==='applied'?'asr.enhancement.status_applied':'asr.enhancement.bypassed')}</dd></div>:null}</dl></ActionPopover></div>
}
