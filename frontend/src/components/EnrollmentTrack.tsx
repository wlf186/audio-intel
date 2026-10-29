import {useTranslation} from 'react-i18next'
export type EnrollmentAudioTrack='original'|'enhanced'
export function EnrollmentTrack({value,onChange,available,busy=false}:{value:EnrollmentAudioTrack;onChange:(value:EnrollmentAudioTrack)=>void;available:boolean;busy?:boolean}){
 const {t}=useTranslation()
 return <fieldset disabled={busy} style={{margin:'12px 0',padding:12,border:'1px solid var(--line)'}}>
  <legend>{t('enrollmentTrack.label')}</legend>
  <div style={{display:'flex',gap:24,flexWrap:'wrap'}}>{(['original','enhanced'] as const).map(track=><label key={track} style={{display:'flex',alignItems:'center',gap:8,minHeight:44,margin:0}}><input type="radio" name="enrollment-track" checked={value===track} disabled={track==='enhanced'&&!available} onChange={()=>onChange(track)} style={{width:18,height:18,minHeight:18,margin:0}}/>{t(track==='original'?'enrollmentTrack.original':'enrollmentTrack.enhanced')}</label>)}</div>
  {!available?<small>{t('enrollmentTrack.unavailable')}</small>:null}
 </fieldset>
}
