import type {ComputeDevice} from './types'

export type AsrPreferences={model:string;language:string;speakerCount:string;align:boolean;useVoiceprints:boolean;computeDevice:ComputeDevice;accelerateSingleTask:boolean;hotwordListIds:string[]}
export type TtsPreferences={model:string;mode:'preset'|'inline_clone'|'voice_design';cloneSource:'upload'|'voiceprint';speaker:string;language:string;computeDevice:ComputeDevice;personId:string;sampleId:string;accelerateSingleTask:boolean}
export type TtsContent={text:string;instruct:string;refText:string;refLanguage:string;refJobId:string}

export const asrPreferencesKey='audio-intel:asr-preferences:v3'
export const ttsPreferencesKey='audio-intel:tts-preferences:v2'
export const ttsContentKey='audio-intel:tts-content:v2'
export const defaultAsrPreferences:AsrPreferences={model:'qwen3-asr-0.6b',language:'Auto',speakerCount:'auto',align:true,useVoiceprints:true,computeDevice:'gpu',accelerateSingleTask:true,hotwordListIds:[]}
export const defaultTtsPreferences:TtsPreferences={model:'qwen3-tts-0.6b',mode:'preset',cloneSource:'upload',speaker:'Vivian',language:'Auto',computeDevice:'gpu',personId:'',sampleId:'',accelerateSingleTask:true}
export const defaultTtsContent:TtsContent={text:'',instruct:'',refText:'',refLanguage:'Auto',refJobId:''}
export const publicAlignerLanguages=['Chinese','English','Cantonese','French','German','Italian','Japanese','Korean','Portuguese','Russian','Spanish']
export const publicAsrLanguages=['Auto',...publicAlignerLanguages]

const asrLanguages=new Set(publicAsrLanguages)
const ttsLanguages=new Set(['Auto','Chinese','English','Japanese','Korean','German','French','Russian','Portuguese','Spanish','Italian'])
const legacyAsrSessionKeys=['audio-intel:asr-device-v1','audio-intel:asr-voiceprints-v1','audio-intel:asr-acceleration-v1','audio-intel:asr-single-task-acceleration-v1']
const legacyAsrPreferencesKeys=['audio-intel:asr-preferences:v2','audio-intel:asr-preferences:v1']
const legacyTtsKeys=['audio-intel:tts-preferences:v1','audio-intel:tts-draft-v2','audio-intel:tts-draft-v1']
const legacyTtsContentKeys=['audio-intel:tts-content:v1','audio-intel:tts-draft-v2','audio-intel:tts-draft-v1']

function record(value:unknown):Record<string,unknown>{return value!==null&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:{}}
function parsed(storage:Storage,key:string):Record<string,unknown>|undefined{try{const raw=storage.getItem(key);return raw?record(JSON.parse(raw)):undefined}catch{return undefined}}
function text(value:unknown,fallback:string){return typeof value==='string'?value:fallback}
function bool(value:unknown,fallback:boolean){return typeof value==='boolean'?value:fallback}
function device(value:unknown,fallback:ComputeDevice):ComputeDevice{return value==='cpu'||value==='gpu'?value:fallback}
function stringList(value:unknown){if(!Array.isArray(value))return[];const seen=new Set<string>();return value.flatMap(item=>{if(typeof item!=='string')return[];const normalized=item.trim();if(!normalized||seen.has(normalized))return[];seen.add(normalized);return[normalized]})}
function write(storage:Storage,key:string,value:unknown){try{storage.setItem(key,JSON.stringify(value))}catch{/* Preferences remain usable in memory when browser storage is unavailable. */}}
function remove(storage:Storage,key:string){try{storage.removeItem(key)}catch{/* Ignore disabled browser storage. */}}

function normalizeAsr(value:Record<string,unknown>,maxSpeakers:number,defaultDevice:ComputeDevice):AsrPreferences{
 const speaker=text(value.speakerCount??value.speakers,defaultAsrPreferences.speakerCount)
 const speakerNumber=Number(speaker)
 return {
  model:text(value.model,defaultAsrPreferences.model),
  language:asrLanguages.has(text(value.language,''))?text(value.language,''):defaultAsrPreferences.language,
  speakerCount:speaker==='auto'||Number.isInteger(speakerNumber)&&speakerNumber>=1&&speakerNumber<=maxSpeakers?speaker:'auto',
  align:bool(value.align,defaultAsrPreferences.align),useVoiceprints:bool(value.useVoiceprints,defaultAsrPreferences.useVoiceprints),
  computeDevice:device(value.computeDevice,defaultDevice),accelerateSingleTask:bool(value.accelerateSingleTask,defaultAsrPreferences.accelerateSingleTask),
  hotwordListIds:stringList(value.hotwordListIds),
 }
}

function normalizeTts(value:Record<string,unknown>,defaultDevice:ComputeDevice):TtsPreferences{
 const model=value.model==='qwen3-tts-1.7b'?'qwen3-tts-1.7b':'qwen3-tts-0.6b'
 const mode=value.mode==='inline_clone'?'inline_clone':value.mode==='voice_design'&&model==='qwen3-tts-1.7b'?'voice_design':'preset'
 const cloneSource=value.cloneSource==='voiceprint'?'voiceprint':'upload'
 const language=text(value.language,'')
 return {model,mode,cloneSource,speaker:text(value.speaker,defaultTtsPreferences.speaker),language:ttsLanguages.has(language)?language:defaultTtsPreferences.language,
  computeDevice:device(value.computeDevice,defaultDevice),personId:text(value.personId,''),sampleId:text(value.sampleId,''),
  accelerateSingleTask:bool(value.accelerateSingleTask,defaultTtsPreferences.accelerateSingleTask)}
}

export function loadAsrPreferences(maxSpeakers:number,defaultDevice:ComputeDevice='gpu'):AsrPreferences{
 const stored=parsed(localStorage,asrPreferencesKey)
 if(stored)return normalizeAsr(stored,maxSpeakers,defaultDevice)
 const legacy:Record<string,unknown>={}
 try{
  const oldDevice=sessionStorage.getItem(legacyAsrSessionKeys[0]);if(oldDevice)legacy.computeDevice=oldDevice
  const oldVoiceprints=sessionStorage.getItem(legacyAsrSessionKeys[1]);if(oldVoiceprints!==null)legacy.useVoiceprints=oldVoiceprints!=='false'
  const oldAcceleration=sessionStorage.getItem(legacyAsrSessionKeys[2])??sessionStorage.getItem(legacyAsrSessionKeys[3]);if(oldAcceleration!==null)legacy.accelerateSingleTask=oldAcceleration==='true'
 }catch{/* Use defaults when session storage is unavailable. */}
 const previous=legacyAsrPreferencesKeys.map(key=>parsed(localStorage,key)).find(Boolean);const migrated=normalizeAsr({...previous,...legacy},maxSpeakers,defaultDevice);saveAsrPreferences(migrated);legacyAsrSessionKeys.forEach(key=>remove(sessionStorage,key));legacyAsrPreferencesKeys.forEach(key=>remove(localStorage,key));return migrated
}
export function saveAsrPreferences(value:AsrPreferences){write(localStorage,asrPreferencesKey,value)}
export function clearAsrPreferences(){remove(localStorage,asrPreferencesKey)}

export function loadTtsPreferences(defaultDevice:ComputeDevice='gpu'):TtsPreferences{
 const stored=parsed(localStorage,ttsPreferencesKey)
 if(stored)return normalizeTts(stored,defaultDevice)
 const legacy=parsed(localStorage,legacyTtsKeys[0])||legacyTtsKeys.slice(1).map(key=>parsed(sessionStorage,key)).find(Boolean)
 const migrated=normalizeTts(legacy||{},defaultDevice);saveTtsPreferences(migrated);remove(localStorage,legacyTtsKeys[0]);return migrated
}
export function saveTtsPreferences(value:TtsPreferences){write(localStorage,ttsPreferencesKey,value)}
export function clearTtsPreferences(){remove(localStorage,ttsPreferencesKey)}

export function loadTtsContent(defaultText=defaultTtsContent.text):TtsContent{
 const stored=parsed(sessionStorage,ttsContentKey)
 const legacy=stored?undefined:legacyTtsContentKeys.map(key=>parsed(sessionStorage,key)).find(Boolean)
 const source=stored||legacy||{}
 const content={
  text:text(source.text,defaultText),instruct:text(source.instruct,defaultTtsContent.instruct),refText:text(source.refText,defaultTtsContent.refText),
  refLanguage:text(source.refLanguage,defaultTtsContent.refLanguage),refJobId:text(source.refJobId,defaultTtsContent.refJobId),
 }
 write(sessionStorage,ttsContentKey,content);legacyTtsContentKeys.forEach(key=>remove(sessionStorage,key));return content
}
export function saveTtsContent(value:TtsContent){write(sessionStorage,ttsContentKey,value)}
