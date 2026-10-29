export const workspaceSessionKey='audio-intel:workspace-session:v1'
export type ReadingMode='original'|'optimized'|'comparison'
export type AsrWorkspaceSession={view:'create'|'video'|'results';jobId?:string;videoReading:boolean}
export type PeopleSession={groupId:string;query:string;selectedId:string;scrollTop:number;alphabetScrollTop:number}
export type ReaderBookmark={sourceId?:number;anchorId:string;time:number;offset:number;scrollTop:number;visibleCount:number;query?:string;speakerFilter?:string}
type WorkspaceSession={optimizationChanges?:Record<string,boolean>;readingFilters?:Record<string,{query:string;speaker:string}>;optimizationViews?:Record<string,boolean|ReadingMode>;asr?:AsrWorkspaceSession;people?:PeopleSession;readers?:Record<string,ReaderBookmark>}
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
 read().readingFilters=Object.fromEntries(Object.entries(read().readingFilters||{}).filter(([id])=>!removed.has(id)))
 read().optimizationChanges=Object.fromEntries(Object.entries(read().optimizationChanges||{}).filter(([id])=>!removed.has(id)))
 read().optimizationViews=Object.fromEntries(Object.entries(read().optimizationViews||{}).filter(([id])=>!removed.has(id)))
 read().readers=Object.fromEntries(Object.entries(read().readers||{}).filter(([key])=>!removed.has(key.split(':')[0])))
 if(memory.asr?.jobId&&removed.has(memory.asr.jobId))delete memory.asr.jobId
 persist()
}
export function checkpointWorkspace(){window.dispatchEvent(new Event('audio-intel:checkpoint-workspace'))}

export function readOptimizationView(jobId:string):ReadingMode{const value=read().optimizationViews?.[jobId];return value===true?'optimized':value==='optimized'||value==='comparison'?value:'original'}
export function saveOptimizationView(jobId:string,value:ReadingMode){if(!active)return;const values={...read().optimizationViews};delete values[jobId];values[jobId]=value;read().optimizationViews=Object.fromEntries(Object.entries(values).slice(-200));persist()}

export function readReadingFilter(jobId:string|undefined){
 const saved=jobId?read().readingFilters?.[jobId]:undefined,legacy=readReaderBookmark(`${jobId}:segments`)
 return{query:text(saved?.query)?saved.query:legacy?.query||'',speaker:text(saved?.speaker)?saved.speaker:legacy?.speakerFilter||'all'}
}
export function saveReadingFilter(jobId:string,query:string,speaker:string){
 if(!active)return
 const values={...read().readingFilters};delete values[jobId];values[jobId]={query,speaker}
 read().readingFilters=Object.fromEntries(Object.entries(values).slice(-200));persist()
}

export function readOptimizationChanges(jobId:string){return read().optimizationChanges?.[jobId]===true}
export function saveOptimizationChanges(jobId:string,value:boolean){if(!active)return;const values={...read().optimizationChanges};delete values[jobId];values[jobId]=value;read().optimizationChanges=Object.fromEntries(Object.entries(values).slice(-200));persist()}
