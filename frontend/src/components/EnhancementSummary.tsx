import {Info} from 'lucide-react'
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
 return <div className={`enhancement-summary${partial||value.status==='fallback'?' enhancement-partial':''}`} role="note"><ActionPopover label={summary} icon={<Info size={15} aria-hidden="true"/>} className="enhancement-details"><h3>{t('asr.enhancement.processingDetails')}</h3>{strengths.length?<p>{t('asr.enhancement.configuredStrengths')} · {strengths.join(' · ')}</p>:null}{value.status==='bypassed'?<p>{t('asr.enhancement.bypassed')}</p>:null}<dl>{controls.map((control,index)=>control?<div key={index}><dt>{t(index?'asr.enhancement.denoiseName':'asr.enhancement.gainName')}</dt><dd>{t(control.reason==='strength_zero'?'asr.enhancement.zeroStatus':`asr.enhancement.status_${control.status}`)}{control.status==='fallback'?<small>{t(index===1&&control.reason==='model_unavailable'?'asr.enhancement.denoiseUnavailable':'asr.enhancement.unavailable')}</small>:null}</dd></div>:null)}{!controls.some(Boolean)?<div><dt>{summary}</dt><dd>{t(value.status==='fallback'?'asr.enhancement.fallback':value.status==='off'?'asr.enhancement.status_off':value.status==='applied'?'asr.enhancement.status_applied':'asr.enhancement.bypassed')}</dd></div>:null}</dl></ActionPopover></div>
}
