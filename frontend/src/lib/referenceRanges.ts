import type {VoiceprintSample} from './types'

export type ReferenceRangeError='unknownDuration'|'invalid'|'noWords'|'overlap'
export type ReferenceRange={start:number;end:number}
export type ReferenceRangeCapability={voice_modes:string[];min_seconds:number;max_seconds:number;default_max_seconds:number}
export type ReferenceRanges=Record<string,ReferenceRange>
export const referenceRangesKey='audio-intel:tts-reference-ranges:v1'

export function loadReferenceRanges():ReferenceRanges{
 try{
  const value:unknown=JSON.parse(localStorage.getItem(referenceRangesKey)||'{}')
  if(!value||typeof value!=='object'||Array.isArray(value))return {}
  return Object.fromEntries(Object.entries(value).filter(([,range])=>range&&typeof range==='object'&&Number.isFinite(range.start)&&Number.isFinite(range.end)))
 }catch{return {}}
}
export function saveReferenceRanges(value:ReferenceRanges){try{localStorage.setItem(referenceRangesKey,JSON.stringify(value))}catch{/* Keep usable in memory when storage is unavailable. */}}
export function rangeError(range:ReferenceRange,duration:number|undefined,cap:ReferenceRangeCapability,requireDuration=true):ReferenceRangeError|undefined{
 if(requireDuration&&(!duration||!Number.isFinite(duration)))return 'unknownDuration'
 if(!Number.isFinite(range.start)||!Number.isFinite(range.end)||range.start<0||(duration!==undefined&&range.end>duration+1e-9)||range.end-range.start<cap.min_seconds-1e-9||range.end-range.start>cap.max_seconds+1e-9)return 'invalid'
}
export function previewReferenceRange(sample:VoiceprintSample,range:ReferenceRange,duration:number):{range:ReferenceRange;text?:string;pending?:boolean;error?:ReferenceRangeError}{
 let cursor=0,previousStart=-1,previousEnd=-1
 const text=sample.transcript||''
 const mapped:Array<{start:number;end:number;textStart:number;textEnd:number}>=[]
 for(const word of sample.words||[]){
  const token=word.text.trim()
  if(!token)continue
  const offset=text.indexOf(token,cursor)
  if(offset<0||!Number.isFinite(word.start)||!Number.isFinite(word.end)||word.start<0||word.end>duration||word.end<word.start||word.start<previousStart||word.end<previousEnd)return {range,pending:true}
  cursor=offset+token.length
  mapped.push({start:word.start,end:word.end,textStart:offset,textEnd:cursor})
  previousStart=word.start;previousEnd=word.end
 }
 if(!mapped.length)return {range,pending:true}
 const eligible=mapped.filter(word=>word.start>=range.start&&word.end<=range.end)
 if(!eligible.length)return {range,error:'noWords'}
 const first=eligible[0],last=eligible[eligible.length-1]
 if(mapped.some(word=>word.start<first.start&&word.end>first.start||word.start<last.end&&word.end>last.end))return {range,error:'overlap'}
 return {range:{start:first.start,end:last.end},text:text.slice(first.textStart,last.textEnd)}
}
export function referenceTime(value:number){return `${String(Math.floor(value/60)).padStart(2,'0')}:${(value%60).toFixed(3).padStart(6,'0')}`}
