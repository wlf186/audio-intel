export type NavigationGuard=(proceed:()=>void,destination:string)=>void
export function personFromHash(hash=location.hash):string|undefined{
 const match=/^#library\/voiceprints\/([^/]+)$/.exec(hash)
 if(!match)return
 try{return decodeURIComponent(match[1])}catch{return}
}
