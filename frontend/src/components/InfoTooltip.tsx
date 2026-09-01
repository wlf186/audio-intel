import {Info} from 'lucide-react'
import {useTranslation} from 'react-i18next'

export function InfoTooltip({id,text}:{id:string;text:string}){
 const {t}=useTranslation()
 return <span className="info-tooltip"><button type="button" aria-label={t('common.accelerationHelp')} aria-describedby={id}><Info size={14}/></button><span id={id} role="tooltip">{text}</span></span>
}
