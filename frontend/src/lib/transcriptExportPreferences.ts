import type {TranscriptExportFormat} from './types'
export type TranscriptTextVersion='original'|'optimized'|'comparison'
export type TranscriptExportPreferences={redact:boolean;rules:string;textVersion?:TranscriptTextVersion;formatByVersion?:Partial<Record<TranscriptTextVersion,TranscriptExportFormat>>}
export type TranscriptReplacement={source:string;target:string}
export const transcriptExportPreferencesKey='audio-intel:transcript-export:v1'
export const defaultTranscriptExportPreferences:TranscriptExportPreferences={redact:true,rules:''}
const versions:TranscriptTextVersion[]=['original','optimized','comparison']
const formats:TranscriptExportFormat[]=['html','docx','txt','srt','vtt','json']
export function exportFormats(version:TranscriptTextVersion,available:TranscriptExportFormat[]){return available.filter(value=>version==='original'||value==='html'||value==='docx'||version==='optimized'&&value==='txt')}
export function preferredExportFormat(preferences:TranscriptExportPreferences,version:TranscriptTextVersion,available:TranscriptExportFormat[]):TranscriptExportFormat{
 const allowed=exportFormats(version,available),saved=preferences.formatByVersion?.[version]
 return saved&&allowed.includes(saved)?saved:allowed.includes('html')?'html':allowed[0]||'html'
}
export function loadTranscriptExportPreferences():TranscriptExportPreferences{
 try{
  const value=JSON.parse(localStorage.getItem(transcriptExportPreferencesKey)||'null')
  const formatByVersion:Partial<Record<TranscriptTextVersion,TranscriptExportFormat>>={}
  for(const version of versions){
   const saved=value?.formatByVersion?.[version]
   if(saved==='pdf')formatByVersion[version]='html'
   else if(formats.includes(saved))formatByVersion[version]=saved
  }
  return{redact:typeof value?.redact==='boolean'?value.redact:true,rules:typeof value?.rules==='string'?value.rules:'',textVersion:versions.includes(value?.textVersion)?value.textVersion:undefined,formatByVersion}
 }catch{return{...defaultTranscriptExportPreferences}}
}
export function saveTranscriptExportPreferences(value:TranscriptExportPreferences){try{localStorage.setItem(transcriptExportPreferencesKey,JSON.stringify(value))}catch{/* Keep controls usable without browser storage. */}}
export function parseTranscriptRules(raw:string):{rules:TranscriptReplacement[];errorLine?:number;error?:'syntax'|'duplicate'|'limit'|'length'}{
 const rules:TranscriptReplacement[]=[]
 const seen=new Set<string>()
 for(const [index,line] of raw.split(/\r?\n/).entries()){
  if(!line.trim())continue
  const separator=line.indexOf('=')
  const source=separator<0?'':line.slice(0,separator).trim()
  const target=separator<0?'':line.slice(separator+1).trim()
  if(separator<0||!source)return{rules:[],errorLine:index+1,error:'syntax'}
  if(seen.has(source))return{rules:[],errorLine:index+1,error:'duplicate'}
  if(Array.from(source).length>8000||Array.from(target).length>8000)return{rules:[],errorLine:index+1,error:'length'}
  if(rules.length===100)return{rules:[],errorLine:index+1,error:'limit'}
  seen.add(source);rules.push({source,target})
 }
 return{rules}
}
