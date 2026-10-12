export const workspaceSessionKey='audio-intel:workspace-session:v1'
export type AsrWorkspaceSession={view:'create'|'video'|'results';jobId?:string;videoReading:boolean}
export type PeopleSession={groupId:string;query:string;selectedId:string;scrollTop:number;alphabetScrollTop:number}
export type ReaderBookmark={anchorId:string;time:number;offset:number;scrollTop:number;visibleCount:number;query?:string;speakerFilter?:string}
type WorkspaceSession={asr?:AsrWorkspaceSession;people?:PeopleSession;readers?:Record<string,ReaderBookmark>}
let memory:WorkspaceSession={}
let loaded=false
let active=true
const finite=(value:unknown):value is number=>typeof value==='number'&&Number.isFinite(value)
const text=(value:unknown):value is string=>typeof value==='string'

function read(){
 if(!loaded){
  loaded=true
  try{const value=JSON.parse(sessionStorage.getItem(workspaceSessionKey)||'{}');if(value&&typeof value==='object'&&!Array.isArray(value))memory=value}catch{/* Navigation remains usable without storage. */}
 }
 return memory
}
function persist(){
 if(!active)return
 try{sessionStorage.setItem(workspaceSessionKey,JSON.stringify(memory))}catch{/* Keep the same state in memory. */}
}
export function activateWorkspaceSession(){active=true}
export function clearWorkspaceSession(){active=false;loaded=true;memory={};try{sessionStorage.removeItem(workspaceSessionKey)}catch{/* Best effort. */}}
export function clearPeopleSession(){delete read().people;persist()}
export function readAsrSession():AsrWorkspaceSession{
 const value=read().asr
 return{view:value&&['create','video','results'].includes(value.view)?value.view:'create',jobId:text(value?.jobId)?value.jobId:undefined,videoReading:value?.videoReading!==false}
}
export function saveAsrSession(value:Partial<AsrWorkspaceSession>){if(!active)return;read().asr={...readAsrSession(),...value};persist()}
export function readPeopleSession():PeopleSession{
 const value=read().people
 return{groupId:text(value?.groupId)?value.groupId:'',query:text(value?.query)?value.query:'',selectedId:text(value?.selectedId)?value.selectedId:'',scrollTop:finite(value?.scrollTop)?Math.max(0,value.scrollTop):0,alphabetScrollTop:finite(value?.alphabetScrollTop)?Math.max(0,value.alphabetScrollTop):0}
}
export function savePeopleSession(value:Partial<PeopleSession>){if(!active)return;read().people={...readPeopleSession(),...value};persist()}
export function readReaderBookmark(key:string):ReaderBookmark|undefined{
 const value=read().readers?.[key]
 if(!value||!text(value.anchorId)||![value.time,value.offset,value.scrollTop,value.visibleCount].every(finite)||value.visibleCount<1)return
 return{...value,visibleCount:Math.min(200000,Math.floor(value.visibleCount)),query:text(value.query)?value.query:undefined,speakerFilter:text(value.speakerFilter)?value.speakerFilter:undefined}
}
export function saveReaderBookmark(key:string,value:ReaderBookmark){
 if(!active||!key)return
 const readers={...read().readers};delete readers[key];readers[key]=value
 read().readers=Object.fromEntries(Object.entries(readers).slice(-200));persist()
}
export function removeJobSession(ids:string[]){
 const removed=new Set(ids)
 read().readers=Object.fromEntries(Object.entries(read().readers||{}).filter(([key])=>!removed.has(key.split(':')[0])))
 if(memory.asr?.jobId&&removed.has(memory.asr.jobId))delete memory.asr.jobId
 persist()
}
export function checkpointWorkspace(){window.dispatchEvent(new Event('audio-intel:checkpoint-workspace'))}
