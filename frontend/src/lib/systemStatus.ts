import type {Health} from './types'

export type SystemPhase='checking'|'ready'|'error'

export function systemPhase(health:Health|undefined,systemError:string):SystemPhase{
 if(systemError||health&&health.status!=='ok')return 'error'
 return health?'ready':'checking'
}
