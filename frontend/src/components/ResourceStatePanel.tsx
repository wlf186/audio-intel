import {LoaderCircle,RefreshCw} from 'lucide-react'
import type {ResourceState} from '../lib/types'

export function ResourceStatePanel({state,loadingLabel,errorLabel,retry}:{state:ResourceState;loadingLabel:string;errorLabel:string;retry:()=>void}){
 if(state==='ready')return null
 return <div className={`resource-state ${state}`} role={state==='error'?'alert':'status'}>{state==='loading'?<LoaderCircle className="spin"/>:<RefreshCw/>}<p>{state==='loading'?loadingLabel:errorLabel}</p>{state==='error'?<button className="button" onClick={retry}>重试</button>:null}</div>
}
