import {expect,test,type Page} from '@playwright/test'

// Drain async route callbacks before Playwright closes the page.
test.afterEach(async({page})=>{await page.unrouteAll({behavior:'wait'})})
import type {VoiceprintPerson} from '../src/lib/types'

const now='2026-09-09T12:00:00Z'
async function workspace(page:Page){
 const errors:string[]=[]
 const expectedConflicts:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error'){
  if(message.text().includes('the server responded with a status of 409'))expectedConflicts.push(message.text())
  else errors.push(message.text())
 }})
 const people:VoiceprintPerson[]=[
  {id:'p1',name:'张三',note:'研发一部',include_in_hotword_library:true,sample_count:3,created_at:now,updated_at:now,samples:[
   {id:'long',person_id:'p1',name:'会议室录音',state:'ready',language:'Chinese',transcript:'这是会议室中录制的一段参考音频，用来验证声音克隆。',words:[],duration:28,tts_eligible:true,embedding_status:'ready',created_at:now,updated_at:now},
   {id:'short',person_id:'p1',name:'电话录音',state:'ready',language:'Chinese',transcript:'简短参考。',words:[],duration:5,tts_eligible:true,embedding_status:'ready',created_at:now,updated_at:now},
   {id:'pending',person_id:'p1',name:'正在分析的录音',state:'pending',language:'Chinese',words:[],tts_eligible:false,embedding_status:'pending',created_at:now,updated_at:now},
  ]},
  {id:'p2',name:'张三',note:'销售部',include_in_hotword_library:true,sample_count:1,created_at:now,updated_at:now,samples:[{id:'unknown',person_id:'p2',name:'旧版参考',state:'ready',language:'Chinese',transcript:'历史样本',words:[],tts_eligible:true,embedding_status:'ready',created_at:now,updated_at:now}]},
 ]
 await page.route('**/api/v1/auth/session',route=>route.fulfill({json:{required:false,authenticated:true}}))
 await page.route(/\/api\/v1\/jobs(?:\?.*)?$/,route=>route.fulfill({json:{items:[],count:0,total:0,limit:100,offset:0,has_more:false}}))
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',offline:true,bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{},models:[],storage:{}}}))
 await page.route('**/api/v1/asr/hotword-lists',route=>route.fulfill({json:{items:[],count:0}}))
 await page.route('**/api/v1/tts/voices',route=>route.fulfill({json:{items:[],preset_speakers:['Vivian']}}))
 await page.route('**/api/v1/capabilities',async route=>{
  const response=await route.fetch()
  const value=await response.json()
  value.events={sse:false}
  value.deployment={...value.deployment,default_compute_device:'cpu'}
  for(const model of [...value.asr.models,...value.tts.model_capabilities])model.installed=true
  await route.fulfill({response,json:value})
 })
 await page.route('**/api/v1/voiceprints/people',async route=>{
  if(route.request().method()==='POST'){
   const input=route.request().postDataJSON() as {name:string;note:string|null;include_in_hotword_library:boolean}
   if(people.some(person=>person.name===input.name&&(person.note||null)===(input.note||null)))return route.fulfill({status:409,json:{code:'voiceprint_person_conflict',detail:'Duplicate name and note'}})
   const person={...input,note:input.note||undefined,id:'created',sample_count:0,samples:[],created_at:now,updated_at:now}
   people.push(person)
   return route.fulfill({status:201,json:person})
  }
  return route.fulfill({json:{items:people}})
 })
 await page.route(/\/api\/v1\/voiceprints\/people\/p1\/samples\/[^/]+$/,route=>{
  const id=new URL(route.request().url()).pathname.split('/').at(-1)
  const sample=people[0].samples.find(item=>item.id===id)!
  const input=route.request().postDataJSON() as {name:string}
  if(people[0].samples.some(item=>item.id!==id&&item.name===input.name))return route.fulfill({status:409,json:{code:'voiceprint_sample_name_conflict',detail:'Duplicate sample name'}})
  sample.name=input.name
  return route.fulfill({json:sample})
 })
 return {people,errors,expectedConflicts}
}

for(const width of [1440,390]){
 test(`voiceprint name conflicts stay in dialogs and renamed samples reach cloning at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:width===390?844:900})
  const {people,errors,expectedConflicts}=await workspace(page)
  await page.goto('/#voiceprints')
  await expect(page).toHaveTitle(/Sandevistan/)
  await page.getByRole('button',{name:'新建人员',exact:true}).click()
  const personDialog=page.getByRole('dialog',{name:'新建声纹人员'})
  await personDialog.getByLabel('名字（必填）').fill('张三')
  await personDialog.getByLabel('备注（选填）').fill('研发一部')
  await personDialog.getByRole('button',{name:'保存人员'}).click()
  await expect(personDialog.getByRole('alert')).toContainText('姓名和备注相同')
  await expect(personDialog.getByLabel('名字（必填）')).toHaveValue('张三')
  await expect(personDialog.getByLabel('名字（必填）')).toBeFocused()
  await expect(page.locator('.voiceprint-page > .error')).toHaveCount(0)
  await page.screenshot({path:`/tmp/voiceprints-after-conflict-${width}.png`})
  await personDialog.getByLabel('备注（选填）').fill('产品部')
  await personDialog.getByRole('button',{name:'保存人员'}).click()
  await expect(personDialog).toHaveCount(0)
  await expect(page.locator('.samples-panel .person-note')).toHaveText('产品部')
  await page.locator('.people-panel button').filter({hasText:'研发一部'}).click()
  const trigger=page.getByRole('button',{name:'重命名 会议室录音',exact:true})
  await trigger.click()
  const rename=page.getByRole('dialog',{name:'重命名样本'})
  await rename.getByLabel('样本名称').fill('电话录音')
  await rename.getByRole('button',{name:'重命名样本',exact:true}).click()
  await expect(rename.getByRole('alert')).toContainText('已有同名样本')
  await expect(rename.getByLabel('样本名称')).toBeFocused()
  await rename.getByLabel('样本名称').fill('安静会议室录音')
  await rename.getByRole('button',{name:'重命名样本',exact:true}).click()
  await expect(rename).toHaveCount(0)
  await expect(page.getByRole('button',{name:'重命名 安静会议室录音',exact:true})).toBeVisible()
  await page.getByRole('button',{name:'重命名 正在分析的录音',exact:true}).click()
  await rename.getByLabel('样本名称').fill('待分析会议录音')
  await rename.getByRole('button',{name:'重命名样本',exact:true}).click()
  await expect(rename).toHaveCount(0)
  expect(people[0].samples[2].name).toBe('待分析会议录音')
  await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:'语音合成'}).click()
  await page.getByRole('tab',{name:'声音克隆'}).click()
  await page.getByRole('tab',{name:'声纹库',exact:true}).click()
  const person=page.getByLabel('声纹人员',{exact:true})
  const sample=page.getByLabel('TTS 声纹样本')
  await expect(person.locator('option')).toContainText(['张三（研发一部）','张三（销售部）','张三（产品部）'])
  await expect(sample).toHaveValue('long')
  await expect(sample.locator('option:checked')).toContainText('安静会议室录音 · 原长 28 秒 · 使用开头≤15秒')
  await expect(page.locator('.sample-summary')).toContainText('最后一个完整字词边界')
  await expect(sample.locator('option')).toHaveCount(2)
  await page.locator('.sample-summary').scrollIntoViewIfNeeded()
  await page.screenshot({path:`/tmp/voiceprints-after-clone-${width}.png`})
  await sample.selectOption('short')
  await expect(sample.locator('option:checked')).toContainText('5 秒 · 完整使用')
  await person.selectOption('p2')
  await expect(sample.locator('option:checked')).toContainText('时长未知')
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  for(const control of [person,sample]){
   const box=await control.boundingBox()
   expect(box!.height).toBeGreaterThanOrEqual(44)
   expect(box!.x).toBeGreaterThanOrEqual(0)
   expect(box!.x+box!.width).toBeLessThanOrEqual(width)
  }
  expect(expectedConflicts).toHaveLength(2)
  expect(errors).toEqual([])
 })
}

test('ASR enrollment requires a choice for ambiguous names and keeps creation errors inside its dialog',async({page})=>{
 const {errors}=await workspace(page)
 const result={text:'测试发言。',language:'Chinese',duration:2,speakers:[{id:'Speaker_0',label:'张三',label_source:'manual'}],segments:[{id:0,start:0,end:2,speaker:'Speaker_0',speaker_label:'张三',text:'测试发言。',words:[]}],artifacts:[]}
 const job={id:'asr-names',kind:'asr',state:'succeeded',stage:'completed',progress:1,display_name:'会议',created_at:now,updated_at:now,request:{},result}
 await page.route(/\/api\/v1\/jobs(?:\?.*)?$/,route=>route.fulfill({json:{items:[job],count:1,total:1,limit:100,offset:0,has_more:false}}))
 await page.route('**/api/v1/jobs/asr-names',route=>route.fulfill({json:job}))
 const audio=Buffer.alloc(44+32000)
 audio.write('RIFF');audio.writeUInt32LE(audio.length-8,4);audio.write('WAVEfmt ',8);audio.writeUInt32LE(16,16);audio.writeUInt16LE(1,20);audio.writeUInt16LE(1,22);audio.writeUInt32LE(16000,24);audio.writeUInt32LE(32000,28);audio.writeUInt16LE(2,32);audio.writeUInt16LE(16,34);audio.write('data',36);audio.writeUInt32LE(32000,40)
 await page.route('**/api/v1/jobs/asr-names/source*',route=>route.fulfill({contentType:'audio/wav',body:audio}))
 let target=''
 await page.route('**/api/v1/voiceprints/people/*/samples/from-asr',route=>{target=new URL(route.request().url()).pathname;return route.fulfill({status:201,json:{items:[]}})})
 await page.goto('/#asr')
 await page.getByLabel('选择片段 1').check()
 await page.getByRole('button',{name:'加入声纹库',exact:true}).click()
 const dialog=page.getByRole('dialog',{name:'加入声纹库'})
 await expect(dialog.getByLabel('指定人员')).toHaveValue('__choose__')
 await expect(dialog.getByRole('button',{name:'确认加入'})).toBeDisabled()
 await dialog.getByLabel('指定人员').selectOption('')
 await dialog.getByLabel('新人员名字').fill('张三')
 await dialog.getByLabel('备注（选填）').fill('研发一部')
 await dialog.getByRole('button',{name:'确认加入'}).click()
 await expect(dialog.getByRole('alert')).toContainText('姓名和备注相同')
 await dialog.getByLabel('指定人员').selectOption('p2')
 await dialog.getByRole('button',{name:'确认加入'}).click()
 await expect(dialog).toHaveCount(0)
 expect(target).toContain('/people/p2/')
 expect(errors).toEqual([])
})

test('completed clone shows saved names and actual reference duration without reading current library metadata',async({page})=>{
 const {errors}=await workspace(page)
 const job={id:'tts-names',kind:'tts',state:'succeeded',stage:'completed',progress:1,display_name:'声音克隆',created_at:now,updated_at:now,
  request:{voice_mode:'voiceprint',voiceprint_person_name:'历史姓名',voiceprint_person_note:'历史备注',voiceprint_sample_name:'历史样本名'},
  result:{speaker:'历史姓名',duration:2,reference_duration_original:28,reference_duration_used:14.72,reference_truncated:true,artifacts:[]}}
 await page.route(/\/api\/v1\/jobs(?:\?.*)?$/,route=>route.fulfill({json:{items:[job],count:1,total:1,limit:100,offset:0,has_more:false}}))
 await page.route('**/api/v1/jobs/tts-names',route=>route.fulfill({json:job}))
 await page.goto('/#tts')
 await expect(page.locator('.audio-card')).toContainText('历史姓名（历史备注）')
 await expect(page.locator('.audio-card')).toContainText('参考样本：历史样本名')
 await expect(page.locator('.audio-card')).toContainText('原长 28 秒，实际使用 14.72 秒')
 await page.setViewportSize({width:390,height:844})
 await page.locator('.audio-card').scrollIntoViewIfNeeded()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await page.screenshot({path:'/tmp/voiceprints-after-result-mobile.png'})
 expect(errors).toEqual([])
})

test('long person and sample names remain fully readable without mobile overflow',async({page})=>{
 const {people,errors}=await workspace(page)
 people[0].name='长姓名'.repeat(25)
 people[0].note='研发'.repeat(10)
 people[0].samples[0].name='会议录音'.repeat(20)
 await page.setViewportSize({width:390,height:844})
 await page.goto('/#tts')
 await page.getByRole('tab',{name:'声音克隆'}).click()
 await page.getByRole('tab',{name:'声纹库',exact:true}).click()
 await expect(page.locator('.sample-summary')).toContainText(people[0].samples[0].name)
 await expect(page.locator('.sample-summary')).toContainText(people[0].name)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:'声纹库',exact:true}).click()
 const rename=page.getByRole('button',{name:`重命名 ${people[0].samples[0].name}`,exact:true})
 await rename.scrollIntoViewIfNeeded()
 await expect(rename).toBeVisible()
 const box=await rename.boundingBox()
 expect(box!.height).toBeGreaterThanOrEqual(44)
 expect(box!.width).toBeGreaterThanOrEqual(44)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await page.screenshot({path:'/tmp/voiceprints-after-long-names-mobile.png'})
 expect(errors).toEqual([])
})

test('clone library loading and failure stay distinct from an empty library and can retry',async({page})=>{
 const {people,errors}=await workspace(page)
 let release:()=>void=()=>{}
 const pending=new Promise<void>(resolve=>{release=resolve})
 let count=0
 await page.route('**/api/v1/voiceprints/people',async route=>{
  count++
  if(count===1){await pending;return route.fulfill({status:503,json:{detail:'Temporary library outage'}})}
  return route.fulfill({json:{items:people}})
 })
 await page.goto('/#tts')
 await page.getByRole('tab',{name:'声音克隆'}).click()
 await page.getByRole('tab',{name:'声纹库',exact:true}).click()
 await expect(page.locator('.clone-person-picker .resource-state.loading')).toBeVisible()
 await expect(page.getByLabel('TTS 声纹样本')).toBeDisabled()
 await expect(page.getByLabel('TTS 声纹样本').locator('option')).not.toContainText('没有可用于 TTS 的样本')
 release()
 const panel=page.locator('.clone-person-picker .resource-state.error')
 await expect(panel).toBeVisible()
 await panel.getByRole('button',{name:'重试'}).click()
 await expect(page.getByLabel('TTS 声纹样本')).toBeEnabled()
 await expect(page.getByLabel('TTS 声纹样本')).toHaveValue('long')
 expect(errors).toHaveLength(1)
 expect(errors[0]).toContain('the server responded with a status of 503')
})
