import {useTranslation} from 'react-i18next'
import type {SceneDetectionSettings,SceneDetectionCapability} from '../lib/sceneDetection'

export function SceneDetectionFields({values,capability,disabled,onChange}:{
 values:SceneDetectionSettings;capability:SceneDetectionCapability;disabled:boolean;onChange:(value:SceneDetectionSettings)=>void
}){
 const {t}=useTranslation()
 return <div className="scene-detection-controls">{(['min_change_percent','min_stable_seconds'] as const).map(key=><label key={key}>
    <span>{t(`sceneDetection.${key}`)}</span>
    <input type="number" min={capability[key].min} max={capability[key].max} step={capability[key].step} value={Number.isNaN(values[key])?'':values[key]} disabled={disabled} onChange={event=>onChange({...values,[key]:event.target.value===''?NaN:Number(event.target.value)})}/>
    <small>{t(`sceneDetection.${key}Help`)}</small>
   </label>)}</div>
}

export function SceneChoice<T extends string>({label,caption,value,choices,onChange,disabled=false}:{
 label:string;caption?:string;value:T;choices:readonly (readonly [T,string])[];onChange:(value:T)=>void;disabled?:boolean
}){
 const buttons=<div className="scene-choice" role="group" aria-label={label}>
  {choices.map(([key,text])=><button type="button" key={key} aria-pressed={value===key} disabled={disabled} onClick={()=>onChange(key)}>{text}</button>)}
 </div>
 return caption?<div className="scene-choice-field"><small>{caption}</small>{buttons}</div>:buttons
}

export function SceneModeChoice({value,onChange,disabled}:{value:string;onChange:(mode:'auto'|'custom')=>void;disabled?:boolean}){
 const {t}=useTranslation()
 return <SceneChoice caption={t('sceneDetection.mode')} label={t('sceneDetection.mode')} value={value==='auto'?'auto':'custom'} disabled={disabled} onChange={onChange}
  choices={[['auto',t('sceneDetection.auto')],['custom',t('sceneDetection.custom')]]}/>
}

export function SceneDetailChoice({value,onChange,disabled}:{value:import('../lib/sceneDetection').SceneDetail;onChange:(v:import('../lib/sceneDetection').SceneDetail)=>void;disabled?:boolean}){
 const {t}=useTranslation()
 return <SceneChoice caption={t('sceneDetection.detail')} label={t('sceneDetection.detail')} value={value} disabled={disabled} onChange={onChange}
  choices={(['concise','balanced','detailed'] as const).map(level=>[level,t(`sceneDetection.${level}`)] as const)}/>
}
