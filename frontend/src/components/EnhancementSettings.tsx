import {useState} from 'react'
import {useTranslation} from 'react-i18next'
import type {AsrStrengthCapability} from '../lib/types'
import {Modal} from './Modal'

type Props={gain:AsrStrengthCapability;noise:AsrStrengthCapability;gainValue:number;noiseValue:number;onChange:(gain:number,noise:number)=>void;onClose:()=>void}

export function EnhancementSettings({gain,noise,gainValue,noiseValue,onChange,onClose}:Props){
 const {t}=useTranslation()
 const [values,setValues]=useState([String(Math.round(gainValue*100)),String(Math.round(noiseValue*100))])
 const capabilities=[gain,noise]
 const valid=values.every((value,index)=>value.trim()!==''&&Number.isFinite(Number(value))&&Number(value)>=capabilities[index].min*100&&Number(value)<=capabilities[index].max*100)
 const update=(index:number,value:string)=>{
  const next=values.map((old,i)=>i===index?value:old)
  setValues(next)
  if(next.every((text,i)=>text.trim()!==''&&Number.isFinite(Number(text))&&Number(text)>=capabilities[i].min*100&&Number(text)<=capabilities[i].max*100))onChange(Number(next[0])/100,Number(next[1])/100)
 }
 return <Modal className="enhancement-settings" title={t('asr.enhancement.settings')} closeLabel={t('common.actions.close')} onClose={onClose}>
  <p>{t('asr.enhancement.settingsHelp')}</p>
  {capabilities.map((capability,index)=>{
   const label=t(index===0?'asr.enhancement.gainStrength':'asr.enhancement.noiseStrength')
   return <div className="enhancement-strength-field" key={index}>
    <label htmlFor={`enhancement-strength-${index}`}>{label}</label>
    <small>{t('asr.enhancement.defaultStrength',{value:Math.round(capability.default*100)})}</small>
    <div className="enhancement-strength-inputs">
     <input id={`enhancement-strength-${index}`} type="range" aria-label={label} min={capability.min*100} max={capability.max*100} step={capability.step*100} value={index===0?gainValue*100:noiseValue*100} onChange={event=>update(index,event.target.value)}/>
     <label><input type="number" aria-label={t('asr.enhancement.strengthValue',{name:label})} min={capability.min*100} max={capability.max*100} step={capability.step*100} value={values[index]} onChange={event=>update(index,event.target.value)}/> %</label>
    </div>
   </div>
  })}
  {!valid?<p role="alert">{t('asr.enhancement.invalidStrength')}</p>:null}
  <p className="device-hint" role="note">{t('asr.enhancement.strongerHelp')}</p>
  <div className="modal-actions"><button className="button" onClick={()=>{setValues([String(gain.default*100),String(noise.default*100)]);onChange(gain.default,noise.default)}}>{t('asr.enhancement.resetStrength')}</button><button className="primary" disabled={!valid} onClick={onClose}>{t('asr.enhancement.done')}</button></div>
 </Modal>
}
