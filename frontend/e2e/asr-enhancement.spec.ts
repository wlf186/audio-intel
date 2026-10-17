import {test,expect,capabilitiesFor,type Page} from './fixtures'
import {showAsrResults} from './asr-helpers'
import type {Job} from '../src/lib/types'
import path from 'node:path'
import os from 'node:os'
import {createServer} from 'node:http'
import type {AddressInfo} from 'node:net'

function wave(amplitude:number){
 const b=Buffer.alloc(44+16000*5*2)
 b.write('RIFF');b.writeUInt32LE(b.length-8,4);b.write('WAVEfmt ',8);b.writeUInt32LE(16,16);b.writeUInt16LE(1,20);b.writeUInt16LE(1,22);b.writeUInt32LE(16000,24);b.writeUInt32LE(32000,28);b.writeUInt16LE(2,32);b.writeUInt16LE(16,34);b.write('data',36);b.writeUInt32LE(b.length-44,40)
 for(let i=44;i<b.length;i+=2)b.writeInt16LE(Math.round(Math.sin(i/15)*amplitude*32767),i)
 return b
}
async function audioDownloadServer(){
 const server=createServer((request,response)=>{
  const url=new URL(request.url||'/', 'http://127.0.0.1')
  const enhanced=url.pathname.endsWith('/enhanced.wav')
  if(!enhanced&&!url.pathname.endsWith('/playback')){response.writeHead(404);response.end();return}
  const data=wave(enhanced?.5:.05)
  const range=request.headers.range?.match(/bytes=(\d+)-(\d*)/)
  const start=range?Number(range[1]):0;const end=Math.min(data.length-1,range?.[2]?Number(range[2]):data.length-1)
  if(start>end){response.writeHead(416,{'Content-Range':`bytes */${data.length}`});response.end();return}
  const name=enhanced?'enhanced.wav':'公开增强测试-original.wav'
  response.writeHead(range?206:200,{'Content-Type':'audio/wav','Accept-Ranges':'bytes','Content-Length':end-start+1,
   ...(enhanced||url.searchParams.get('download')==='true'?{'Content-Disposition':`attachment; filename*=UTF-8''${encodeURIComponent(name)}`}:{}) ,
   ...(range?{'Content-Range':`bytes ${start}-${end}/${data.length}`}:{})})
  response.end(data.subarray(start,end+1))
 })
 await new Promise<void>((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve)})
 return {origin:`http://127.0.0.1:${(server.address() as AddressInfo).port}`,close:()=>new Promise<void>((resolve,reject)=>server.close(error=>error?reject(error):resolve()))}
}

async function fixture(page:Page,withPeaks=true,partial=false,strengths=false,tracks:{original?:boolean;status?:'off'|'bypassed'|'fallback';origin?:string}={}){
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())})
 const now=new Date().toISOString()
 const job:Job={id:'enhancement-fixture',kind:'asr',state:'succeeded',stage:'completed',progress:1,display_name:'公开增强测试.wav',created_at:now,request:{compute_device:'cpu',audio_enhancement:'auto'},result:{duration:5,language:'Chinese',timestamp_precision:'segment',playback_url:'/api/v1/jobs/enhancement-fixture/playback',waveform:Array(240).fill(.08),segments:[{id:0,start:0,end:5,speaker:'Speaker_0',speaker_label:'测试人声',text:'公开的回放测试文字。'}],speakers:[{id:'Speaker_0',label:'测试人声'}],audio_enhancement:{mode:'auto',status:'applied',reason:'quiet_speech',playback_url:'/api/v1/jobs/enhancement-fixture/artifacts/enhanced.wav',waveform:Array(240).fill(.7)},artifacts:[]}}
 if(tracks.original===false)delete job.result!.playback_url
 if(tracks.status){job.result!.audio_enhancement={mode:tracks.status==='off'?'off':'auto',status:tracks.status,reason:tracks.status==='off'?'disabled':tracks.status==='fallback'?'resource_unavailable':'clean'}}
 if(tracks.origin){
  job.result!.playback_url=tracks.origin+job.result!.playback_url
  job.result!.audio_enhancement!.playback_url=tracks.origin+job.result!.audio_enhancement!.playback_url
 }
 const state={submission:''}
 if(partial)Object.assign(job.result!.audio_enhancement!,{processing_version:5,reason:'partial_fallback',
  speech_enhancement:{enabled:true,status:'applied',reason:'quiet_speech'},
  background_denoise:{enabled:true,status:'fallback',reason:'model_unavailable'}})
 if(strengths)Object.assign(job.result!.audio_enhancement!,{processing_version:6,
  speech_enhancement:{enabled:true,status:'applied',reason:'quiet_speech',strength:1.5},
  background_denoise:{enabled:true,status:'bypassed',reason:'strength_zero',strength:0}})
 if(!withPeaks)delete job.result!.audio_enhancement!.waveform
 await page.route('**/api/v1/**',async route=>{
  const url=new URL(route.request().url());const pathname=url.pathname
  if(pathname==='/api/v1/capabilities'){const body=await capabilitiesFor(page);body.events={...body.events,sse:false};return route.fulfill({json:body})}
  if(pathname==='/api/v1/jobs'){const {request:_,result:__,...summary}=job;return route.fulfill({json:{items:[summary],count:1,total:1,has_more:false}})}
  if(pathname===`/api/v1/jobs/${job.id}`)return route.fulfill({json:job})
  if(pathname.endsWith('/playback')||pathname.endsWith('/enhanced.wav')||pathname.endsWith('/source')){
   const b=wave(pathname.endsWith('/enhanced.wav')?.5:.05)
   const range=route.request().headers().range?.match(/bytes=(\d+)-(\d*)/)
   const start=range?Number(range[1]):0;const end=Math.min(b.length-1,range?.[2]?Number(range[2]):b.length-1)
   return route.fulfill({status:range?206:200,contentType:'audio/wav',headers:{'Accept-Ranges':'bytes',...(url.searchParams.get('download')==='true'?{'Content-Disposition':"attachment; filename*=UTF-8''%E5%85%AC%E5%BC%80%E5%A2%9E%E5%BC%BA%E6%B5%8B%E8%AF%95-original.wav"}:pathname.endsWith('/enhanced.wav')?{'Content-Disposition':'attachment; filename=enhanced.wav'}:{}),...(range?{'Content-Range':`bytes ${start}-${end}/${b.length}`}:{})},body:b.subarray(start,end+1)})
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
 await page.getByRole('group',{name:'音频增强',exact:true}).getByRole('button',{name:'增强音',exact:true}).click()
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
  const group=page.getByRole('group',{name:'音频增强',exact:true})
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
  const select=page.getByLabel('音频增强',{exact:true})
  await expect(select).toBeChecked()
  await select.uncheck()
  await page.reload()
  if(width===390){await page.locator('.asr-settings-toggle').click();await page.locator('.asr-advanced-toggle').click()}
  await expect(select).not.toBeChecked()
  await expect(page.getByRole('checkbox',{name:'背景降噪',exact:true})).toHaveCount(0)
  await select.scrollIntoViewIfNeeded()
  await expect(select).toBeVisible()
  const box=await select.locator('..').boundingBox();expect(box!.height).toBeGreaterThanOrEqual(44);expect(box!.x+box!.width).toBeLessThanOrEqual(width)
  await page.screenshot({path:path.join(os.tmpdir(),`asr-combined-control-${width}.png`)})
  await page.locator('input[type=file]').setInputFiles({name:'public.wav',mimeType:'audio/wav',buffer:wave(.05)})
  const accepted=page.waitForResponse(r=>r.url().endsWith('/api/v1/asr/jobs')&&r.request().method()==='POST')
  await page.getByRole('button',{name:'开始转写',exact:true}).click()
  expect((await accepted).status()).toBe(202)
  expect(state.submission).toMatch(/name="audio_enhancement"\r\n\r\noff/)
  expect(state.submission).not.toContain('name="speech_enhancement"')
  expect(state.submission).not.toContain('name="background_denoise"')
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  expect(errors).toEqual([])
 })
}

for(const enabled of [false,true]){
 test(`combined audio control submits enabled=${enabled}`,async({page})=>{
  const {errors,state}=await fixture(page)
  await page.getByLabel('音频增强',{exact:true}).setChecked(enabled)
  await page.locator('input[type=file]').setInputFiles({name:'public.wav',mimeType:'audio/wav',buffer:wave(.05)})
  const accepted=page.waitForResponse(r=>r.url().endsWith('/api/v1/asr/jobs')&&r.request().method()==='POST')
  await page.getByRole('button',{name:'开始转写',exact:true}).click()
  expect((await accepted).status()).toBe(202)
  expect(state.submission).toContain(`name="audio_enhancement"\r\n\r\n${enabled?'auto':'off'}`)
  expect(state.submission).not.toContain('name="speech_enhancement"')
  expect(state.submission).not.toContain('name="background_denoise"')
  expect(errors).toEqual([])
 })
}
test('legacy disabled preference migrates to the combined control off',async({page})=>{
 await page.addInitScript(()=>{
  if(!localStorage.getItem('migration-fixture')){
   localStorage.setItem('audio-intel:asr-preferences:v4',JSON.stringify({audioEnhancement:'off'}))
   localStorage.setItem('migration-fixture','1')
  }
 })
 const {errors}=await fixture(page)
 await expect(page.getByLabel('音频增强',{exact:true})).not.toBeChecked()
 expect(await page.evaluate(()=>localStorage.getItem('audio-intel:asr-preferences:v4'))).toBeNull()
 expect(errors).toEqual([])
})
for(const [gain,noise] of [[false,false],[true,false],[false,true],[true,true]]){
 test(`v5 preferences merge gain=${gain} noise=${noise} and persist a single control`,async({page})=>{
  await page.addInitScript(({gain,noise})=>{
   if(!localStorage.getItem('migration-fixture')){
    localStorage.setItem('audio-intel:asr-preferences:v5',JSON.stringify({speechEnhancement:gain,backgroundDenoise:noise,language:'English',computeDevice:'cpu'}))
    localStorage.setItem('migration-fixture','1')
   }
  },{gain,noise})
  const {errors}=await fixture(page)
  const control=page.getByLabel('音频增强',{exact:true})
  await expect(control).toBeChecked({checked:gain||noise})
  expect(await page.evaluate(()=>localStorage.getItem('audio-intel:asr-preferences:v5'))).toBeNull()
  const stored=await page.evaluate(()=>JSON.parse(localStorage.getItem('audio-intel:asr-preferences:v7')!))
  expect(stored.audioEnhancement).toBe(gain||noise?'auto':'off')
  expect(stored.language).toBe('English');expect(stored.computeDevice).toBe('cpu')
  expect(stored).not.toHaveProperty('speechEnhancement');expect(stored).not.toHaveProperty('backgroundDenoise')
  await control.setChecked(!(gain||noise))
  await page.reload()
  await expect(control).toBeChecked({checked:!(gain||noise)})
  expect(errors).toEqual([])
 })
}
test('partial processing shows each outcome while retaining enhanced playback',async({page})=>{
 const {errors}=await fixture(page,true,true)
 await showAsrResults(page)
 await expect(page.getByText('人声增强：已应用',{exact:true})).toBeVisible()
 await expect(page.getByText('背景降噪：未完成，已回退',{exact:true})).toBeVisible()
 await expect(page.getByRole('button',{name:'增强音',exact:true})).toBeVisible()
 expect(errors).toEqual([])
})

for(const width of [1440,390]){
 test(`advanced strengths persist, reset independently and submit at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:900})
  const {errors,state}=await fixture(page)
  if(width===390){await page.locator('.asr-settings-toggle').click();await page.locator('.asr-advanced-toggle').click()}
  const open=()=>page.getByRole('button',{name:'增强高级设置',exact:true}).click()
  await page.getByRole('combobox',{name:'识别语言',exact:true}).selectOption('English')
  await open()
  const modal=page.getByRole('dialog',{name:'增强高级设置'})
  const gain=modal.getByRole('spinbutton',{name:'人声增益力度数值'})
  const noise=modal.getByRole('spinbutton',{name:'降噪混合比例数值'})
  await expect(gain).toHaveValue('100');await expect(noise).toHaveValue('50')
  await gain.fill('150');await noise.fill('75')
  const stored=()=>page.evaluate(()=>JSON.parse(localStorage.getItem('audio-intel:asr-preferences:v7')!))
  await expect.poll(async()=>(await stored()).speechEnhancementStrength).toBe(1.5)
  await expect.poll(async()=>(await stored()).backgroundDenoiseStrength).toBe(.75)
  await gain.fill('201');await expect(modal.getByRole('alert')).toBeVisible()
  await expect(modal.getByRole('button',{name:'完成',exact:true})).toBeDisabled()
  expect((await stored()).speechEnhancementStrength).toBe(1.5)
  await modal.getByRole('button',{name:'恢复增强默认值',exact:true}).click()
  await expect(gain).toHaveValue('100');await expect(noise).toHaveValue('50')
  expect((await stored()).language).toBe('English')
  await modal.getByRole('slider',{name:'人声增益力度',exact:true}).focus()
  await page.keyboard.press('ArrowRight');await expect(gain).toHaveValue('101')
  await gain.fill('150');await noise.fill('0')
  for(const control of await modal.locator('button,input').all()){
   if(!await control.isVisible())continue
   const box=await control.boundingBox();expect(box!.height).toBeGreaterThanOrEqual(44);expect(box!.x).toBeGreaterThanOrEqual(0);expect(box!.x+box!.width).toBeLessThanOrEqual(width)
  }
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  await page.screenshot({path:path.resolve(`../tmp/enhancement-strength/ui-${width}.png`)})
  await modal.getByRole('button',{name:'完成',exact:true}).click()
  await page.getByLabel('音频增强',{exact:true}).uncheck()
  await expect(page.getByRole('button',{name:'增强高级设置',exact:true})).toBeDisabled()
  await page.reload()
  if(width===390){await page.locator('.asr-settings-toggle').click();await page.locator('.asr-advanced-toggle').click()}
  await page.getByLabel('音频增强',{exact:true}).check()
  await open();await expect(gain).toHaveValue('150');await expect(noise).toHaveValue('0')
  await modal.getByRole('button',{name:'完成',exact:true}).click()
  await page.locator('input[type=file]').setInputFiles({name:'public-strength.wav',mimeType:'audio/wav',buffer:wave(.05)})
  const accepted=page.waitForResponse(r=>r.url().endsWith('/api/v1/asr/jobs')&&r.request().method()==='POST')
  await page.getByRole('button',{name:'开始转写',exact:true}).click()
  expect((await accepted).status()).toBe(202)
  expect(state.submission).toContain('name="speech_enhancement_strength"\r\n\r\n1.5')
  expect(state.submission).toContain('name="background_denoise_strength"\r\n\r\n0')
  expect(errors).toEqual([])
 })
}

test('v6 combined preferences migrate with server strength defaults',async({page})=>{
 await page.addInitScript(()=>localStorage.setItem('audio-intel:asr-preferences:v6',JSON.stringify({audioEnhancement:'off',language:'English'})))
 const {errors}=await fixture(page)
 await expect(page.getByLabel('音频增强',{exact:true})).not.toBeChecked()
 const stored=await page.evaluate(()=>JSON.parse(localStorage.getItem('audio-intel:asr-preferences:v7')!))
 expect(stored).toMatchObject({audioEnhancement:'off',speechEnhancementStrength:1,backgroundDenoiseStrength:.5,language:'English'})
 expect(await page.evaluate(()=>localStorage.getItem('audio-intel:asr-preferences:v6'))).toBeNull()
 expect(errors).toEqual([])
})

test('older capabilities omit advanced settings and numeric submissions',async({page})=>{
 const {errors,state}=await fixture(page)
 await page.route('**/api/v1/capabilities',async route=>{
  const body=await capabilitiesFor(page)
  delete body.asr.audio_enhancement!.speech_enhancement!.strength
  delete body.asr.audio_enhancement!.background_denoise!.strength
  body.events={...body.events,sse:false}
  return route.fulfill({json:body})
 })
 await page.reload()
 await expect(page.getByRole('button',{name:'增强高级设置',exact:true})).toHaveCount(0)
 await page.locator('input[type=file]').setInputFiles({name:'public-old-server.wav',mimeType:'audio/wav',buffer:wave(.05)})
 const accepted=page.waitForResponse(r=>r.url().endsWith('/api/v1/asr/jobs')&&r.request().method()==='POST')
 await page.getByRole('button',{name:'开始转写',exact:true}).click();await accepted
 expect(state.submission).not.toContain('name="speech_enhancement_strength"')
 expect(state.submission).not.toContain('name="background_denoise_strength"')
 expect(errors).toEqual([])
})


test('policy 6 results show used strengths and explicit zero bypass',async({page})=>{
 const {errors}=await fixture(page,true,false,true)
 await showAsrResults(page)
 await expect(page.getByText('背景降噪：强度为 0，已跳过',{exact:true})).toBeVisible()
 await expect(page.getByText('本次强度：人声 150% · 降噪 0%',{exact:true})).toBeVisible()
 expect(errors).toEqual([])
})


for(const width of [1440,390])test(`original and enhanced WAV downloads preserve listening at ${width}px`,async({page})=>{
 await page.setViewportSize({width,height:900})
 const server=await audioDownloadServer()
 try{
 const {errors}=await fixture(page,true,false,false,{origin:server.origin})
 await showAsrResults(page)
 const group=page.getByRole('group',{name:'音频增强',exact:true})
 const tracks=group.getByRole('group',{name:'试听音轨',exact:true})
 const downloads=group.getByRole('group',{name:'下载音频',exact:true})
 await expect(tracks.getByRole('button',{name:'原音',exact:true})).toHaveAttribute('aria-pressed','true')
 await tracks.getByRole('button',{name:'增强音',exact:true}).click()
 const player=page.locator('.result-panel audio')
 await expect.poll(()=>player.evaluate((e:HTMLAudioElement)=>e.currentSrc)).toContain('/enhanced.wav')
 await expect.poll(()=>player.evaluate((e:HTMLAudioElement)=>e.readyState)).toBeGreaterThanOrEqual(1)
 await player.evaluate((e:HTMLAudioElement)=>{e.pause();e.currentTime=1})
 const originalEvent=page.waitForEvent('download')
 await downloads.getByRole('link',{name:'下载原音',exact:true}).click()
 const original=await originalEvent
 expect(original.suggestedFilename()).toBe('公开增强测试-original.wav')
 expect(await original.failure()).toBeNull()
 const originalStream=await original.createReadStream();if(!originalStream)throw new Error('Original WAV stream unavailable')
 const originalChunks:Buffer[]=[];for await(const chunk of originalStream)originalChunks.push(Buffer.from(chunk))
 expect(Buffer.concat(originalChunks)).toEqual(wave(.05))
 expect(await player.evaluate((e:HTMLAudioElement)=>e.currentTime)).toBeCloseTo(1,2)
 expect(await player.evaluate((e:HTMLAudioElement)=>e.paused)).toBe(true)
 await page.getByRole('button',{name:'播放',exact:true}).click()
 const enhancedEvent=page.waitForEvent('download')
 await downloads.getByRole('link',{name:'下载增强音',exact:true}).click()
 const enhanced=await enhancedEvent
 expect(enhanced.suggestedFilename()).toBe('enhanced.wav')
 expect(await enhanced.failure()).toBeNull()
 const enhancedStream=await enhanced.createReadStream();if(!enhancedStream)throw new Error('Enhanced WAV stream unavailable')
 const enhancedChunks:Buffer[]=[];for await(const chunk of enhancedStream)enhancedChunks.push(Buffer.from(chunk))
 expect(Buffer.concat(enhancedChunks)).toEqual(wave(.5))
 expect(await player.evaluate((e:HTMLAudioElement)=>e.paused)).toBe(false)
 expect(await player.evaluate((e:HTMLAudioElement)=>e.currentTime)).toBeGreaterThanOrEqual(1)
 expect(await player.evaluate((e:HTMLAudioElement)=>e.currentSrc)).toContain('/enhanced.wav')
 await expect(tracks.getByRole('button',{name:'增强音',exact:true})).toHaveAttribute('aria-pressed','true')
 await player.evaluate((e:HTMLAudioElement)=>e.pause())
 await tracks.getByRole('button',{name:'原音',exact:true}).focus()
 await page.keyboard.press('Enter')
 await expect(tracks.getByRole('button',{name:'原音',exact:true})).toHaveAttribute('aria-pressed','true')
 for(const control of await group.locator('button,a').all()){
  const box=await control.boundingBox();expect(box!.height).toBeGreaterThanOrEqual(44);expect(box!.x).toBeGreaterThanOrEqual(0);expect(box!.x+box!.width).toBeLessThanOrEqual(width)
 }
 if(width===390){const listenBox=await tracks.boundingBox();const downloadBox=await downloads.boundingBox();expect(downloadBox!.y).toBeGreaterThan(listenBox!.y+listenBox!.height)}
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 await page.screenshot({path:path.resolve(`../tmp/original-audio-download/controls-${width}-zh.png`)})
 await page.locator('.language-switcher select:visible').first().selectOption('en-US')
 await expect(page.getByRole('link',{name:'Download original',exact:true})).toBeVisible()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 await page.screenshot({path:path.resolve(`../tmp/original-audio-download/controls-${width}-en.png`)})
 expect(errors).toEqual([])
 }finally{await server.close()}
})

for(const status of ['off','bypassed','fallback'] as const)test(`original WAV remains downloadable with enhancement ${status}`,async({page})=>{
 const {errors}=await fixture(page,true,false,false,{status})
 await showAsrResults(page)
 await expect(page.getByRole('link',{name:'下载原音',exact:true})).toBeVisible()
 await expect(page.getByRole('link',{name:'下载增强音',exact:true})).toHaveCount(0)
 await expect(page.getByRole('group',{name:'试听音轨',exact:true})).toHaveCount(0)
 expect(errors).toEqual([])
})

test('historical task without saved WAV explains unavailable original download',async({page})=>{
 const {errors}=await fixture(page,true,false,false,{original:false,status:'off'})
 await showAsrResults(page)
 await expect(page.getByRole('button',{name:'下载原音',exact:true})).toBeDisabled()
 await expect(page.getByText('此任务未保存原音 WAV，无法下载。',{exact:true})).toBeVisible()
 await expect(page.getByRole('link',{name:'下载原音',exact:true})).toHaveCount(0)
 expect(errors).toEqual([])
})

for(const width of [1440,1024,390])for(const locale of ['zh-CN','en-US']){
 test(`processing switches are reachable and keyboard accessible at ${width}px in ${locale}`,async({page})=>{
  await page.setViewportSize({width,height:900})
  if(locale==='en-US')await page.addInitScript(()=>localStorage.setItem('audio-intel:ui-locale:v1','en-US'))
  const {errors}=await fixture(page)
  await expect(page.locator('html')).toHaveAttribute('lang',locale)
  if(width===390)await page.locator('.asr-settings-toggle').click()
  const group=page.locator('.asr-processing-options')
  const acceleration=group.getByRole('switch').first()
  const enhancement=group.getByRole('switch').last()
  await expect(acceleration).toBeChecked();await expect(enhancement).toBeChecked()
  if(width===390)await expect(page.locator('#asr-advanced-body')).toBeHidden()
  await acceleration.focus();await page.keyboard.press('Space');await expect(acceleration).not.toBeChecked()
  await group.locator('.setting-toggle-copy').first().click();await expect(acceleration).toBeChecked()
  const help=group.locator('.info-tooltip button').first()
  await help.focus();await expect(page.getByRole('tooltip')).toBeVisible()
  await page.keyboard.press('Escape');await expect(page.getByRole('tooltip')).toHaveCount(0)
  await group.getByRole('button').last().click()
  await expect(page.getByRole('dialog')).toBeVisible();await expect(enhancement).toBeChecked()
  await page.getByRole('dialog').getByRole('button').last().click()
  const boxes=await group.locator('.setting-toggle-row').evaluateAll(items=>items.map(e=>{const b=e.getBoundingClientRect();return{x:b.x,y:b.y,width:b.width,height:b.height}}))
  expect(boxes[1].y).toBeGreaterThanOrEqual(boxes[0].y+boxes[0].height)
  for(const control of await group.locator('button,input').all()){
   const box=(await control.boundingBox())!;expect(box.height).toBeGreaterThanOrEqual(44)
   expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(width)
  }
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  const noOverlap=await page.locator('.asr-settings').evaluate(element=>{
   const clip=element.querySelector('.asr-settings-body')!.getBoundingClientRect();const action=element.querySelector('.submission-actions')!.getBoundingClientRect()
   return [...element.querySelectorAll('.setting-toggle-row')].every(e=>{const b=e.getBoundingClientRect();return Math.min(b.bottom,clip.bottom)<=action.top||Math.max(b.top,clip.top)>=action.bottom})
  })
  expect(noOverlap).toBe(true)
  await group.scrollIntoViewIfNeeded()
  await page.screenshot({path:path.resolve(`../tmp/processing-settings/switches-${width}-${locale}.png`),fullPage:true})
  expect(errors).toEqual([])
 })
}

test('saved strengths distinguish custom, skipped and original audio states',async({page})=>{
 const {errors}=await fixture(page)
 const group=page.locator('.asr-processing-options')
 await expect(group).toContainText('人声 100% · 降噪 50%')
 await group.getByRole('button',{name:'增强高级设置',exact:true}).click()
 await page.getByRole('spinbutton',{name:'人声增益力度数值'}).fill('150')
 await page.getByRole('spinbutton',{name:'降噪混合比例数值'}).fill('0')
 await page.getByRole('button',{name:'完成',exact:true}).click()
 await expect(group).toContainText('人声 150% · 降噪 跳过');await expect(group).toContainText('自定义')
 const control=page.getByRole('switch',{name:'音频增强',exact:true})
 await control.uncheck();await expect(group).toContainText('使用原音');await expect(group).toContainText('开启后可调整')
 await expect(group).not.toContainText('自定义')
 await page.reload();await expect(control).not.toBeChecked()
 await control.check();await expect(group).toContainText('人声 150% · 降噪 跳过')
 await group.getByRole('button',{name:'增强高级设置',exact:true}).click()
 await page.getByRole('button',{name:'恢复增强默认值',exact:true}).click()
 await page.getByRole('button',{name:'完成',exact:true}).click()
 await expect(group).toContainText('人声 100% · 降噪 50%');await expect(group).not.toContainText('自定义')
 expect(errors).toEqual([])
})

for(const state of ['gain-only','noise-only','unavailable','unknown','custom-defaults'] as const){
 test(`processing options report capability state ${state}`,async({page})=>{
  const {errors}=await fixture(page)
  await page.route('**/api/v1/capabilities',async route=>{
   const body=await capabilitiesFor(page);body.events={...body.events,sse:false}
   const enhancement=body.asr.audio_enhancement!
   enhancement.speech_enhancement!.available=state==='gain-only'||state==='custom-defaults'
   enhancement.background_denoise!.available=state==='noise-only'||state==='custom-defaults'
   if(state==='unknown'){delete enhancement.speech_enhancement;delete enhancement.background_denoise}
   if(state==='custom-defaults'){
    enhancement.speech_enhancement!.strength!.default=1.2
    enhancement.background_denoise!.strength!.default=.4
   }
   return route.fulfill({json:body})
  })
  await page.reload()
  const group=page.locator('.asr-processing-options')
  await expect(group.getByRole('switch',{name:'音频增强',exact:true})).toBeChecked()
  if(state==='gain-only')await expect(group.getByRole('note')).toHaveText('降噪模型未就绪，人声增益仍可用。')
  if(state==='noise-only')await expect(group.getByRole('note')).toHaveText('人声增益未就绪，背景降噪仍可用。')
  if(state==='unavailable')await expect(group.getByRole('note')).toHaveText('增强组件未就绪，将使用原音。')
  if(state==='unknown'){
   await expect(group.getByRole('note')).toContainText('状态未知')
   await expect(group.getByRole('button',{name:'增强高级设置',exact:true})).toHaveCount(0)
  }
  if(state==='custom-defaults'){
   // An explicit reset reads server defaults rather than hard-coded UI defaults.
   await page.getByRole('button',{name:'恢复默认配置',exact:true}).click()
   await expect(group).toContainText('人声 120% · 降噪 40%');await expect(group).not.toContainText('自定义')
  }
  expect(errors).toEqual([])
 })
}
