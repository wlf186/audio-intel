import type {ReactNode} from 'react'
import {InfoTooltip} from './InfoTooltip'
import './setting-toggle-row.css'

type SettingToggleRowProps={
 id:string
 label:string
 description:string
 checked:boolean
 onChange:(checked:boolean)=>void
 help:string
 helpLabel?:string
 children?:ReactNode
}

export function SettingToggleRow({id,label,description,checked,onChange,help,helpLabel,children}:SettingToggleRowProps){
 return <div className="setting-toggle-row acceleration-control">
  <div className="setting-toggle-main">
   <label className="setting-toggle-copy" htmlFor={id}>
    <span className="setting-toggle-title">{label}</span>
    <span className="setting-toggle-description" id={`${id}-description`}>{description}</span>
   </label>
   <InfoTooltip id={`${id}-help`} text={help} label={helpLabel}/>
   <label className="setting-toggle-hit" htmlFor={id}>
    <input id={id} type="checkbox" role="switch" aria-label={label} aria-describedby={`${id}-description`} checked={checked} onChange={event=>onChange(event.target.checked)}/>
    <span className="setting-toggle-track" aria-hidden="true"/>
   </label>
  </div>
  {children?<div className="setting-toggle-detail">{children}</div>:null}
 </div>
}
