import {test,expect,capabilitiesFor,type Page} from './fixtures'
import {showAsrResults} from './asr-helpers'
import type {Job} from '../src/lib/types'
import path from 'node:path'
import os from 'node:os'

function wave(amplitude:number){
 const b=Buffer.alloc(44+16000*5*2)
 b.write('RIFF');b.writeUInt32LE(b.length-8,4);b.write('WAVEfmt ',8);b.writeUInt32LE(16,16);b.writeUInt16LE(1,20);b.writeUInt16LE(1,22);b.writeUInt32LE(16000,24);b.writeUInt32LE(32000,28);b.writeUInt16LE(2,32);b.writeUInt16LE(16,34);b.write('data',36);b.writeUInt32LE(b.length-44,40)
 for(let i=44;i<b.length;i+=2)b.writeInt16LE(Math.round(Math.sin(i/15)*amplitude*32767),i)
 return b
}
async function fixture(page:Page,withPeaks=true,partial=false){
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())})
 const now=new Date().toISOString()
 const job:Job={id:'enhancement-fixture',kind:'asr',state:'succeeded',stage:'completed',progress:1,display_name:'公开增强测试.wav',created_at:now,request:{compute_device:'cpu',audio_enhancement:'auto'},result:{duration:5,language:'Chinese',timestamp_precision:'segment',playback_url:'/api/v1/jobs/enhancement-fixture/playback',waveform:Array(240).fill(.08),segments:[{id:0,start:0,end:5,speaker:'Speaker_0',speaker_label:'测试人声',text:'公开的回放测试文字。'}],speakers:[{id:'Speaker_0',label:'测试人声'}],audio_enhancement:{mode:'auto',status:'applied',reason:'quiet_speech',playback_url:'/api/v1/jobs/enhancement-fixture/artifacts/enhanced.wav',waveform:Array(240).fill(.7)},artifacts:[]}}
 const state={submission:''}
 if(partial)Object.assign(job.result!.audio_enhancement!,{processing_version:5,reason:'partial_fallback',
  speech_enhancement:{enabled:true,status:'applied',reason:'quiet_speech'},
  background_denoise:{enabled:true,status:'fallback',reason:'model_unavailable'}})
 if(!withPeaks)delete job.result!.audio_enhancement!.waveform
 await page.route('**/api/v1/**',async route=>{
  const url=new URL(route.request().url());const pathname=url.pathname
  if(pathname==='/api/v1/capabilities'){const body=await capabilitiesFor(page);body.events={...body.events,sse:false};return route.fulfill({json:body})}
  if(pathname==='/api/v1/jobs'){const {request:_,result:__,...summary}=job;return route.fulfill({json:{items:[summary],count:1,total:1,has_more:false}})}
  if(pathname===`/api/v1/jobs/${job.id}`)return route.fulfill({json:job})
  if(pathname.endsWith('/playback')||pathname.endsWith('/enhanced.wav')){
   const b=wave(pathname.endsWith('/enhanced.wav')?.5:.05)
   const range=route.request().headers().range?.match(/bytes=(\d+)-(\d*)/)
   const start=range?Number(range[1]):0;const end=Math.min(b.length-1,range?.[2]?Number(range[2]):b.length-1)
   return route.fulfill({status:range?206:200,contentType:'audio/wav',headers:{'Accept-Ranges':'bytes',...(range?{'Content-Range':`bytes ${start}-${end}/${b.length}`}:{})},body:b.subarray(start,end+1)})
  }
  if(pathname==='/api/v1/asr/jobs'&&route.request().method()==='POST'){
   state.submission=route.request().postData()||''
   return route.fulfill({status:202,json:{...job,id:'submitted-enhancement',state:'queued',progress:0,stage:'queued',result:undefined}})
  }
  return route.continue()
 })
 await page.goto('/#asr')
 return {errors,state}
}
test('enhanced waveform loads, shows a failure and retries without fabricated peaks',async({page,responseGate})=>{
 const {errors}=await fixture(page,false)
 const gate=responseGate()
 let attempt=0
 await page.route('**/artifacts/enhanced.wav/waveform',async route=>{
  attempt++
  if(attempt===1){await gate.hold();return route.fulfill({status:200,contentType:'application/json',body:'invalid-json'})}
  return route.fulfill({json:{waveform:Array(240).fill(.7),duration:5}})
 })
 await showAsrResults(page)
 await page.getByRole('group',{name:'人声增强',exact:true}).getByRole('button',{name:'增强音',exact:true}).click()
 await expect(page.locator('.waveform-resource [role=status]')).toBeVisible()
 await expect(page.locator('.wave-area canvas')).toHaveCount(0)
 gate.release()
 await expect(page.locator('.waveform-resource [role=alert]')).toBeVisible()
 await page.locator('.waveform-resource').getByRole('button').click()
 await expect(page.locator('.wave-area canvas')).toBeVisible()
 expect(attempt).toBe(2)
 expect(errors).toEqual([])
})
for(const width of [1440,390]){
 test(`ASR enhanced playback preserves position and playback at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:900})
  const {errors}=await fixture(page)
  await showAsrResults(page)
  const player=page.locator('.result-panel audio')
  await expect.poll(()=>player.evaluate((e:HTMLAudioElement)=>e.readyState)).toBeGreaterThanOrEqual(1)
  await player.evaluate((e:HTMLAudioElement)=>{e.pause();e.currentTime=2})
  await expect.poll(()=>player.evaluate((e:HTMLAudioElement)=>e.currentTime)).toBe(2)
  const before=await page.locator('.wave-area canvas').evaluate((e:HTMLCanvasElement)=>e.toDataURL())
  const group=page.getByRole('group',{name:'人声增强',exact:true})
  await group.getByRole('button',{name:'增强音',exact:true}).click()
  await expect(group.getByRole('button',{name:'增强音',exact:true})).toHaveAttribute('aria-pressed','true')
  await expect.poll(()=>player.evaluate((e:HTMLAudioElement)=>e.currentSrc)).toContain('/artifacts/enhanced.wav')
  await expect.poll(()=>player.evaluate((e:HTMLAudioElement)=>e.currentTime)).toBeCloseTo(2,2)
  await expect.poll(()=>page.locator('.wave-area canvas').evaluate((e:HTMLCanvasElement)=>e.toDataURL())).not.toBe(before)
  await page.getByRole('button',{name:'播放',exact:true}).click()
  await expect.poll(()=>player.evaluate((e:HTMLAudioElement)=>e.paused)).toBe(false)
  await group.getByRole('button',{name:'原音',exact:true}).click()
  await expect.poll(()=>player.evaluate((e:HTMLAudioElement)=>e.currentSrc)).toContain('/playback')
  await expect.poll(()=>player.evaluate((e:HTMLAudioElement)=>e.paused)).toBe(false)
  await expect.poll(()=>player.evaluate((e:HTMLAudioElement)=>e.currentTime)).toBeGreaterThanOrEqual(2)
  await player.evaluate((e:HTMLAudioElement)=>e.pause())
  for(const control of await group.locator('button,a').all()){
   const box=await control.boundingBox();expect(box!.height).toBeGreaterThanOrEqual(44);expect(box!.x).toBeGreaterThanOrEqual(0);expect(box!.x+box!.width).toBeLessThanOrEqual(width)
  }
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  await page.screenshot({path:path.join(os.tmpdir(),`asr-enhancement-${width}.png`)})
  await page.locator('.language-switcher select:visible').first().selectOption('en-US')
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  expect(errors).toEqual([])
 })
 test(`ASR enhancement opt-out persists and is submitted at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:900})
  const {errors,state}=await fixture(page)
  if(width===390){await page.locator('.asr-settings-toggle').click();await page.locator('.asr-advanced-toggle').click()}
  const select=page.getByLabel('人声增强',{exact:true})
  await expect(select).toBeChecked()
  await select.uncheck()
  await page.reload()
  if(width===390){await page.locator('.asr-settings-toggle').click();await page.locator('.asr-advanced-toggle').click()}
  await expect(select).not.toBeChecked()
  await expect(page.getByLabel('背景降噪',{exact:true})).toBeChecked()
  for(const control of [select,page.getByLabel('背景降噪',{exact:true})]){
   const box=await control.locator('..').boundingBox();expect(box!.height).toBeGreaterThanOrEqual(44);expect(box!.x+box!.width).toBeLessThanOrEqual(width)
  }
  await page.locator('input[type=file]').setInputFiles({name:'public.wav',mimeType:'audio/wav',buffer:wave(.05)})
  const accepted=page.waitForResponse(r=>r.url().endsWith('/api/v1/asr/jobs')&&r.request().method()==='POST')
  await page.getByRole('button',{name:'开始转写',exact:true}).click()
  expect((await accepted).status()).toBe(202)
  expect(state.submission).toMatch(/name="speech_enhancement"\r\n\r\nfalse/)
  expect(state.submission).toMatch(/name="background_denoise"\r\n\r\ntrue/)
  expect(state.submission).not.toContain('name="audio_enhancement"')
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  expect(errors).toEqual([])
 })
}

for(const [gain,noise] of [[false,false],[true,false],[false,true],[true,true]]){
 test(`independent audio controls submit gain=${gain} noise=${noise}`,async({page})=>{
  const {errors,state}=await fixture(page)
  await page.getByLabel('人声增强',{exact:true}).setChecked(gain)
  await page.getByLabel('背景降噪',{exact:true}).setChecked(noise)
  await page.locator('input[type=file]').setInputFiles({name:'public.wav',mimeType:'audio/wav',buffer:wave(.05)})
  const accepted=page.waitForResponse(r=>r.url().endsWith('/api/v1/asr/jobs')&&r.request().method()==='POST')
  await page.getByRole('button',{name:'开始转写',exact:true}).click()
  expect((await accepted).status()).toBe(202)
  expect(state.submission).toContain(`name="speech_enhancement"\r\n\r\n${gain}`)
  expect(state.submission).toContain(`name="background_denoise"\r\n\r\n${noise}`)
  expect(state.submission).not.toContain('name="audio_enhancement"')
  expect(errors).toEqual([])
 })
}
test('legacy disabled preference migrates to both controls off',async({page})=>{
 await page.addInitScript(()=>{
  if(!localStorage.getItem('migration-fixture')){
   localStorage.setItem('audio-intel:asr-preferences:v4',JSON.stringify({audioEnhancement:'off'}))
   localStorage.setItem('migration-fixture','1')
  }
 })
 const {errors}=await fixture(page)
 await expect(page.getByLabel('人声增强',{exact:true})).not.toBeChecked()
 await expect(page.getByLabel('背景降噪',{exact:true})).not.toBeChecked()
 expect(await page.evaluate(()=>localStorage.getItem('audio-intel:asr-preferences:v4'))).toBeNull()
 expect(errors).toEqual([])
})
test('partial processing shows each outcome while retaining enhanced playback',async({page})=>{
 const {errors}=await fixture(page,true,true)
 await showAsrResults(page)
 await expect(page.getByText('人声增强：已应用',{exact:true})).toBeVisible()
 await expect(page.getByText('背景降噪：未完成，已回退',{exact:true})).toBeVisible()
 await expect(page.getByRole('button',{name:'增强音',exact:true})).toBeVisible()
 expect(errors).toEqual([])
})
