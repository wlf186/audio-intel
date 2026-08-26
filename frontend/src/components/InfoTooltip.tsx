import {Info} from 'lucide-react'

export function InfoTooltip({id,text}:{id:string;text:string}){
 return <span className="info-tooltip"><button type="button" aria-label="查看单任务加速说明" aria-describedby={id}><Info size={14}/></button><span id={id} role="tooltip">{text}</span></span>
}
