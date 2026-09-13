import {expect,test,type Page} from '@playwright/test'
import {expandTtsSettings} from './tts-helpers'
import type {VoiceprintPerson} from '../src/lib/types'

const storageKey='audio-intel:tts-reference-ranges:v1'
function wav(){
 const data=Buffer.alloc(44+16000*2*45)
 data.write('RIFF');data.writeUInt32LE(data.length-8,4);data.write('WAVEfmt ',8);data.writeUInt32LE(16,16);data.writeUInt16LE(1,20);data.writeUInt16LE(1,22);data.writeUInt32LE(16000,24);data.writeUInt32LE(32000,28);data.writeUInt16LE(2,32);data.writeUInt16LE(16,34);data.write('data',36);data.writeUInt32LE(data.length-44,40)
 return data
}
async function setup(page:Page){
 const errors:string[]=[]
 const state={waveformReads:0,failWaveform:false,submissions:[] as Array<{path:string;body:string}>}
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error'&&!message.text().includes('status of 429'))errors.push(message.text())})
 const now=new Date().toISOString()
 const person:VoiceprintPerson={id:'person',name:'区间测试人员',sample_count:3,include_in_hotword_library:true,created_at:now,updated_at:now,samples:['a','b','tiny'].map(id=>({id,person_id:'person',name:`样本 ${id}`,state:'ready',language:'Chinese',duration:id==='tiny'?2:45,transcript:Array.from({length:45},(_,i)=>String(i)).join(' '),words:Array.from({length:45},(_,i)=>({text:String(i),start:i,end:i+1})),tts_eligible:true,embedding_status:'ready',created_at:now,updated_at:now,audio_url:`/api/v1/voiceprints/samples/${id}/audio`}))}
 await page.route('**/api/v1/capabilities',async route=>{const response=await route.fetch();const body=await response.json();body.events.sse=false;body.deployment.default_compute_device='cpu';return route.fulfill({response,json:body})})
 await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({json:{items:[person]}}))
 await page.route(/\/api\/v1\/jobs(?:\?.*)?$/,route=>route.fulfill({json:{items:[],count:0,total:0,has_more:false}}))
 await page.route('**/api/v1/voiceprints/samples/*/audio/waveform',route=>{
  state.waveformReads++
  return state.failWaveform?route.fulfill({status:429,headers:{'Retry-After':'1'},json:{detail:'Busy'}}):route.fulfill({json:{artifact_name:'reference.wav',duration:45,waveform:Array.from({length:240},(_,i)=>.1+(i%17)/22)}})
 })
 const audio=wav()
 await page.route('**/api/v1/voiceprints/samples/*/audio',route=>{
  const match=route.request().headers()['range']?.match(/bytes=(\d+)-(\d*)/)
  const start=match?Number(match[1]):0,end=match?.[2]?Math.min(Number(match[2]),audio.length-1):audio.length-1
  return route.fulfill({status:match?206:200,contentType:'audio/wav',headers:{'Accept-Ranges':'bytes',...(match?{'Content-Range':`bytes ${start}-${end}/${audio.length}`}:{})},body:audio.subarray(start,end+1)})
 })
 await page.route(/\/api\/v1\/tts\/(?:jobs|document-jobs)$/,route=>{
  state.submissions.push({path:new URL(route.request().url()).pathname,body:route.request().postData()||''})
  return route.fulfill({status:202,json:{id:`submitted-${state.submissions.length}`,kind:'tts',state:'queued',stage:'queued',progress:0,display_name:'区间任务',created_at:now,request:{}}})
 })
 await page.goto('/#tts')
 await expandTtsSettings(page)
 await page.getByRole('tab',{name:'声音克隆',exact:true}).click()
 await page.getByRole('tab',{name:'声纹库',exact:true}).click()
 await expect(page.getByLabel('TTS 声纹样本')).toHaveValue('a')
 return {state,errors}
}
async function selectRange(page:Page,start:string,end:string){
 await page.locator('.reference-range-card').getByRole('button',{name:/调整参考区间|编辑参考区间/}).click()
 const dialog=page.getByRole('dialog',{name:'调整声纹参考区间'})
 await expect(dialog.locator('.waveform-empty')).toHaveCount(0)
 // Set end first so a larger start does not prevent constructing a valid interval.
 await dialog.getByLabel('结束时间（秒）').fill(end)
 await dialog.getByLabel('开始时间（秒）').fill(start)
 return dialog
}

test.afterEach(async({page})=>{await page.unrouteAll({behavior:'wait'})})
for(const width of [1440,390]){
 test(`range editor, memory, playback and distinct resets at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:900})
  const {state,errors}=await setup(page)
  const card=page.locator('.reference-range-card'),sample=page.getByLabel('TTS 声纹样本')
  await expect(card).toContainText('最多参考 15 秒')
  expect(state.waveformReads).toBe(0)
  const adjust=card.getByRole('button',{name:'调整参考区间',exact:true})
  await expect(adjust).toHaveCSS('background-color','rgb(13, 21, 23)')
  await expect(adjust).toHaveCSS('color','rgb(233, 238, 233)')
  await adjust.hover();await expect(adjust).toHaveCSS('color','rgb(0, 231, 238)')
  await adjust.focus();await expect(adjust).toHaveCSS('outline-color','rgb(0, 231, 238)')
  const dialog=await selectRange(page,'10','40')
  const track=await dialog.locator('.reference-range-track').boundingBox()
  const handle=dialog.getByRole('slider',{name:'拖动参考区间起点'})
  const box=await handle.boundingBox()
  await page.mouse.move(box!.x+box!.width/2,box!.y+box!.height/2)
  await page.mouse.down();await page.mouse.move(track!.x+track!.width*12.1/45,box!.y+box!.height/2,{steps:5});await page.mouse.up()
  await expect(dialog.getByLabel('开始时间（秒）')).toHaveValue('13')
  await handle.focus();await page.keyboard.press('ArrowLeft')
  await expect(dialog.getByLabel('开始时间（秒）')).toHaveValue('12.9')
  await dialog.getByLabel('开始时间（秒）').fill('12')
  await dialog.getByLabel('结束时间（秒）').fill('15')
  await expect(dialog.getByRole('button',{name:'试听所选区间'})).toHaveCSS('background-color','rgb(13, 21, 23)')
  await expect(dialog.getByRole('button',{name:'取消',exact:true})).toHaveCSS('background-color','rgba(0, 0, 0, 0)')
  await dialog.getByRole('button',{name:'试听所选区间'}).click()
  await expect.poll(()=>dialog.locator('audio').evaluate((element:HTMLAudioElement)=>element.currentTime)).toBeGreaterThanOrEqual(12)
  await expect(dialog.getByRole('button',{name:'暂停试听'})).toBeVisible()
  await expect(dialog.getByRole('button',{name:'试听所选区间'})).toBeVisible({timeout:6000})
  expect(await dialog.locator('audio').evaluate((element:HTMLAudioElement)=>element.currentTime)).toBeCloseTo(15,1)
  await dialog.getByLabel('结束时间（秒）').fill('40')
  for(const slider of await dialog.getByRole('slider').all()){const b=await slider.boundingBox();expect(b!.width).toBeGreaterThanOrEqual(44);expect(b!.height).toBeGreaterThanOrEqual(44)}
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  expect(await dialog.evaluate(element=>element.scrollWidth-element.clientWidth)).toBeLessThanOrEqual(1)
  for(const control of await dialog.getByRole('button').all())expect((await control.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await page.screenshot({path:`/tmp/reference-range-editor-${width}.png`})
  await dialog.getByRole('button',{name:'应用区间'}).click()
  await expect(card).toContainText('00:12.000 – 00:40.000')
  await sample.selectOption('b');await expect(card).toContainText('最多参考 15 秒')
  const second=await selectRange(page,'3','18');await second.getByRole('button',{name:'应用区间'}).click()
  await sample.selectOption('a');await expect(card).toContainText('00:12.000 – 00:40.000')
  await page.reload();await expandTtsSettings(page);await expect(card).toContainText('00:12.000 – 00:40.000')
  const cancelled=await selectRange(page,'5','20');await cancelled.getByRole('button',{name:'取消',exact:true}).click();await expect(card).toContainText('00:12.000 – 00:40.000')
  await card.getByRole('button',{name:'改用自动截取（最多前 15 秒）'}).click()
  const stored=await page.evaluate(key=>JSON.parse(localStorage.getItem(key)||'{}'),storageKey)
  expect(stored.a).toBeUndefined();expect(stored.b).toEqual({start:3,end:18})
  const again=await selectRange(page,'12','40');await again.getByRole('button',{name:'应用区间'}).click()
  await page.getByRole('button',{name:'恢复默认配置',exact:true}).click()
  expect(await page.evaluate(key=>JSON.parse(localStorage.getItem(key)||'{}'),storageKey)).toEqual({b:{start:3,end:18}})
  await page.getByRole('tab',{name:'声音克隆',exact:true}).click();await page.getByRole('tab',{name:'声纹库',exact:true}).click()
  await sample.selectOption('b');await expect(card).toContainText('00:03.000 – 00:18.000')
  await sample.selectOption('tiny');await expect(card).toContainText('仅支持自动截取');await expect(card.getByRole('button',{name:'调整参考区间'})).toBeDisabled()
  const disabledAdjust=card.getByRole('button',{name:'调整参考区间'})
  await disabledAdjust.hover({force:true})
  await expect(disabledAdjust).toHaveCSS('color','rgb(233, 238, 233)')
  await expect(disabledAdjust).toHaveCSS('opacity','0.4')
  await page.locator('.language-switcher select:visible').first().selectOption('en-US')
  await page.locator('.voiceprint-sample-select').selectOption('b');await card.getByRole('button',{name:'Edit reference range'}).click()
  await expect(page.getByRole('dialog',{name:'Adjust voiceprint reference range'})).toBeVisible()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  await page.screenshot({path:`/tmp/reference-range-editor-en-${width}.png`})
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  expect(errors).toEqual([])
 })
}

test('waveform retry, invalid input and both UI submission forms',async({page})=>{
 const {state,errors}=await setup(page)
 state.failWaveform=true
 await page.getByRole('button',{name:'调整参考区间',exact:true}).click()
 const dialog=page.getByRole('dialog',{name:'调整声纹参考区间'})
 await expect(dialog.getByRole('button',{name:'重试波形'})).toBeVisible()
 state.failWaveform=false
 await dialog.getByRole('button',{name:'重试波形'}).click()
 await expect(dialog.locator('.waveform-empty')).toHaveCount(0)
 await dialog.getByLabel('结束时间（秒）').fill('31')
 await expect(dialog.getByRole('button',{name:'应用区间'})).toBeDisabled()
 const disabledApply=dialog.getByRole('button',{name:'应用区间'})
 await disabledApply.hover({force:true})
 await expect(disabledApply).toHaveCSS('background-color','rgba(0, 0, 0, 0)')
 await expect(disabledApply).toHaveCSS('box-shadow','none')
 const disabledListen=dialog.getByRole('button',{name:'试听所选区间'})
 await disabledListen.hover({force:true})
 await expect(disabledListen).toHaveCSS('color','rgb(233, 238, 233)')
 await dialog.getByLabel('开始时间（秒）').fill('10')
 await dialog.getByLabel('结束时间（秒）').fill('40')
 await dialog.getByRole('button',{name:'应用区间'}).click()
 await page.getByRole('button',{name:'生成语音',exact:true}).click()
 expect(state.submissions).toHaveLength(1)
 expect(state.submissions[0].body).toMatch(/name="reference_start_seconds"\r\n\r\n10/)
 expect(state.submissions[0].body).toMatch(/name="reference_end_seconds"\r\n\r\n40/)
 await page.getByRole('tab',{name:'文档合成',exact:true}).click()
 await page.locator('input[type=file][accept*=".epub"]').setInputFiles({name:'range.md',mimeType:'text/markdown',buffer:Buffer.from('# 区间测试\n\n这是用于测试参考区间的文档正文。')})
 const generate=page.getByRole('button',{name:'生成语音',exact:true})
 await expect(generate).toBeEnabled({timeout:15000});await generate.click()
 expect(state.submissions).toHaveLength(2)
 expect(state.submissions[1].path).toBe('/api/v1/tts/document-jobs')
 expect(state.submissions[1].body).toMatch(/name="reference_start_seconds"\r\n\r\n10/)
 expect(state.submissions[1].body).toMatch(/name="reference_end_seconds"\r\n\r\n40/)
 expect(errors).toEqual([])
})
