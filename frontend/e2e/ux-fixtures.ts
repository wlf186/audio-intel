import {mockTimelineWaveform} from './timeline-fixture'
import {capabilitiesFor,type Page} from './fixtures'
import type {Job,VideoScene,VoiceprintPerson,VoiceprintGroup} from '../src/lib/types'

const now='2026-10-12T00:00:00Z'
function wave(){
 const buffer=Buffer.alloc(44+32000*120)
 buffer.write('RIFF');buffer.writeUInt32LE(buffer.length-8,4);buffer.write('WAVEfmt ',8);buffer.writeUInt32LE(16,16);buffer.writeUInt16LE(1,20);buffer.writeUInt16LE(1,22);buffer.writeUInt32LE(16000,24);buffer.writeUInt32LE(32000,28);buffer.writeUInt16LE(2,32);buffer.writeUInt16LE(16,34);buffer.write('data',36);buffer.writeUInt32LE(buffer.length-44,40)
 return buffer
}
export async function uxFixture(page:Page){
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error'&&!/409 \(Conflict\)|503 \(Service Unavailable\)/.test(message.text()))errors.push(message.text())})
 const people:VoiceprintPerson[]=Array.from({length:45},(_,i)=>({id:`person-${i}`,name:i===44?'曾华':`Alice ${String(i).padStart(2,'0')}`,sort_initial:i===44?'Z':'A',note:i===0?'研发部':undefined,group_id:i===44?'film':'work',include_in_hotword_library:true,sample_count:1,created_at:now,updated_at:now,samples:[{id:`sample-${i}`,person_id:`person-${i}`,name:'参考样本',state:'ready',language:'Chinese',duration:8,transcript:'这是参考语音。',words:[],tts_eligible:true,embedding_status:'ready',created_at:now,updated_at:now}]}))
 const groups:VoiceprintGroup[]=[{id:'voiceprint_ungrouped',name:'未分组',builtin:true},{id:'work',name:'工作组',builtin:false},{id:'film',name:'影视组',builtin:false},{id:'empty',name:'空分组',builtin:false}].map(group=>({...group,person_count:people.filter(person=>person.group_id===group.id).length,created_at:now,updated_at:now}))
 const scenes:VideoScene[]=Array.from({length:60},(_,i)=>({id:`scene-${i}`,start:i*2,end:(i+1)*2,frame_time:i*2+.5,image:`frame-${i}.jpg`,paragraphs:[{speaker:'s0',speaker_label:'Alice',start:i*2,end:(i+1)*2,text:`第 ${i+1} 段，在图文阅读和逐句明细之间切换，并保留正在阅读的位置。`,segment_ids:[i]}]}))
 const job:Job={id:'ux-video',kind:'asr',purpose:'video_transcription',state:'succeeded',stage:'completed',progress:1,display_name:'很长的中文文件名 · 产品季度评审会议与操作流程介绍.mp4',created_at:now,updated_at:now,request:{model:'qwen3-asr-0.6b',compute_device:'cpu'},result:{duration:120,language:'Chinese',speakers:[{id:'s0',label:'Alice'}],segments:scenes.map((scene,i)=>({id:i,start:scene.start,end:scene.end,speaker:'s0',speaker_label:'Alice',text:scene.paragraphs![0].text,words:[]})),waveform:[.2,.4,.3],artifacts:[],playback_url:'/api/v1/jobs/ux-video/playback',video:{contract_version:1,revision:1,duration:120,scenes,original_scenes:structuredClone(scenes)}}}
 const state={people,groups,job,exports:[] as unknown[],created:[] as Record<string,unknown>[],patches:[] as Record<string,unknown>[],imports:[] as Record<string,unknown>[],groupError:false,sceneHold:undefined as (()=>Promise<void>)|undefined,auth:{required:true,authenticated:true}}
 const caps=await capabilitiesFor(page);caps.events.sse=false
 await page.route('**/api/v1/auth/session',route=>route.fulfill({json:state.auth}))
 await page.route('**/api/v1/auth/logout',route=>{state.auth.authenticated=false;return route.fulfill({status:204})})
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:caps}))
 await page.route('**/api/v1/voiceprints/groups',route=>state.groupError?route.fulfill({status:503,json:{detail:'Groups unavailable'}}):route.fulfill({json:{items:state.groups.map(group=>({...group,person_count:state.people.filter(person=>person.group_id===group.id).length}))}}))
 await page.route('**/api/v1/voiceprints/people',route=>{
  if(route.request().method()==='POST'){
   const body=route.request().postDataJSON();state.created.push(body)
   const person:VoiceprintPerson={id:'created-person',...body,group_id:body.group_id||'voiceprint_ungrouped',sample_count:0,samples:[],created_at:now,updated_at:now};state.people.push(person);return route.fulfill({status:201,json:person})
  }
  return route.fulfill({json:{items:state.people}})
 })
 await page.route('**/api/v1/voiceprints/people/*/samples/from-asr',route=>{state.imports.push(route.request().postDataJSON());return route.fulfill({status:201,json:{items:[]}})})
 await page.route('**/api/v1/asr/hotword-lists',route=>route.fulfill({json:{items:[]}}))
 const {request:_,result:__,...summary}=job
 await page.route(url=>url.pathname==='/api/v1/jobs',route=>route.fulfill({json:{items:[summary],count:1,total:1,has_more:false}}))
 await page.route('**/api/v1/jobs/ux-video',route=>route.fulfill({json:job}))
 await page.route('**/api/v1/jobs/ux-video/video-scenes',async route=>{
  if(route.request().method()==='PATCH'){const body=route.request().postDataJSON();state.patches.push(body);job.result!.video!.revision++;if(body.action==='set_export_visibility')job.result!.video!.export_hidden_scene_ids=body.export_hidden_scene_ids;return route.fulfill({json:job.result})}
  await state.sceneHold?.()
  return route.fulfill({json:{revision:job.result!.video!.revision,scenes}})
 })
 await page.route('**/api/v1/jobs/ux-video/video-frames/*',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#edf2f3"/><text x="40" y="80" fill="#293641" font-size="28">Workspace review</text></svg>'}))
 await mockTimelineWaveform(page,'ux-video',120)
 const audio=wave()
 await page.route('**/api/v1/jobs/ux-video/playback',route=>{
  const range=route.request().headers()['range']?.match(/bytes=(\d+)-(\d*)/)
  const start=range?Number(range[1]):0,end=range?.[2]?Math.min(Number(range[2]),audio.length-1):audio.length-1
  return route.fulfill({status:range?206:200,contentType:'audio/wav',headers:{'Accept-Ranges':'bytes',...(range?{'Content-Range':`bytes ${start}-${end}/${audio.length}`}:{})},body:audio.subarray(start,end+1)})
 })
 await page.route('**/api/v1/jobs/ux-video/transcript-export',route=>{state.exports.push(route.request().postDataJSON());return route.fulfill({contentType:'application/pdf',headers:{'Content-Disposition':'attachment; filename="transcript.pdf"'},body:'%PDF-fixture'})})
 return{state,errors}
}
