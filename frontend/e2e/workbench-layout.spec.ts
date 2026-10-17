import {capabilitiesFor,systemFor,expect,test,type Page} from './fixtures'
import type {Job,VoiceprintPerson} from '../src/lib/types'
import path from 'node:path'
import {execFileSync} from 'node:child_process'

const layoutKey='audio-intel:workbench-layout:v1'
const now='2026-10-11T00:00:00Z'
function wave(){
 const buffer=Buffer.alloc(44+32000*60)
 buffer.write('RIFF');buffer.writeUInt32LE(buffer.length-8,4);buffer.write('WAVEfmt ',8);buffer.writeUInt32LE(16,16);buffer.writeUInt16LE(1,20);buffer.writeUInt16LE(1,22);buffer.writeUInt32LE(16000,24);buffer.writeUInt32LE(32000,28);buffer.writeUInt16LE(2,32);buffer.writeUInt16LE(16,34);buffer.write('data',36);buffer.writeUInt32LE(buffer.length-44,40)
 return buffer
}
async function fixture(page:Page,gpuAvailable?:boolean){
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 const capability=await capabilitiesFor(page)
 capability.events.sse=false
 if(gpuAvailable!==undefined){
  capability.deployment={profile:'full',default_compute_device:'gpu',gpu_runtime_installed:true}
  for(const model of [...capability.asr.models,...capability.tts.model_capabilities])for(const device of model.compute_devices){
   device.available=device.id==='cpu'||gpuAvailable
   device.default=device.id===(gpuAvailable?'gpu':'cpu')
   if(device.id==='gpu'){
    device.unavailable_reason=gpuAvailable?undefined:'No compatible NVIDIA GPU is available'
    device.unavailable_reason_code=undefined
   }
  }
  const system=await systemFor(page)
  system.deployment=capability.deployment
  system.hardware.gpu=gpuAvailable?(system.hardware.gpu||{name:'Layout regression GPU',memory_used_mib:0,memory_total_mib:16384,utilization:0}):undefined
  await page.route('**/api/v1/system',route=>route.fulfill({json:system}))
 }
 const people:VoiceprintPerson[]=Array.from({length:27},(_,i)=>({id:`person-${i}`,name:i===26?'曾华':`Alice ${String(i).padStart(2,'0')}`,sort_initial:i===26?'Z':'A',sample_count:1,include_in_hotword_library:true,created_at:now,updated_at:now,samples:[{id:`sample-${i}`,person_id:`person-${i}`,name:'参考录音',state:'ready',language:'Chinese',duration:60,transcript:'这是用于声音克隆的参考录音。',words:[],tts_eligible:true,embedding_status:'ready',created_at:now,updated_at:now,audio_url:`/api/v1/voiceprints/samples/sample-${i}/audio`}]}))
 const scenes=Array.from({length:30},(_,i)=>({id:`scene-${i}`,start:i*2,end:i*2+2,frame_time:i*2,image:'frame.jpg',paragraphs:[{speaker:'s0',speaker_label:'张三',start:i*2,end:i*2+1,text:`第 ${i+1} 段：在截图旁阅读转写内容，无需先滚过整张幻灯片。`,segment_ids:[i]}]}))
 const job:Job={id:'layout-video',kind:'asr',purpose:'video_transcription',state:'succeeded',stage:'completed',progress:1,display_name:'桌面体验评审.mp4',created_at:now,updated_at:now,request:{purpose:'video_transcription'},result:{duration:60,language:'Chinese',model:'qwen3-asr-1.7b',timestamp_precision:'segment',playback_url:'/api/v1/jobs/layout-video/playback',waveform:Array.from({length:200},(_,i)=>.1+i%9/12),speakers:[{id:'s0',label:'张三'}],segments:scenes.map((scene,i)=>({id:i,start:scene.start,end:scene.end,speaker:'s0',speaker_label:'张三',text:scene.paragraphs[0].text,words:[]})),artifacts:[],video:{contract_version:1,revision:1,duration:60,scenes,original_scenes:scenes}}}
 const {request:_,result:__,...summary}=job
 const audio=wave()
 await page.route('**/api/v1/auth/session',route=>route.fulfill({json:{required:true,authenticated:true}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:capability}))
 await page.route(url=>url.pathname==='/api/v1/jobs',route=>route.fulfill({json:{items:[summary],count:1,total:1,has_more:false}}))
 await page.route('**/api/v1/jobs/layout-video',route=>route.fulfill({json:job}))
 await page.route('**/video-scenes',route=>route.fulfill({json:{revision:1,scenes}}))
 await page.route('**/video-frames/*',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#f0f3f6"/><text x="36" y="80" font-size="28" fill="#293641">Desktop workspace review</text><rect x="36" y="110" width="560" height="8" fill="#bd8e11"/><text x="36" y="180" font-size="20" fill="#293641">More room for controls and reading</text></svg>'}))
 await page.route(url=>url.pathname.endsWith('/playback')||url.pathname.match(/\/samples\/sample-\d+\/audio$/)!==null,route=>{
  const range=route.request().headers()['range']?.match(/bytes=(\d+)-(\d*)/)
  const start=range?Number(range[1]):0,end=range?.[2]?Math.min(Number(range[2]),audio.length-1):audio.length-1
  return route.fulfill({status:range?206:200,contentType:'audio/wav',headers:{'Accept-Ranges':'bytes',...(range?{'Content-Range':`bytes ${start}-${end}/${audio.length}`}:{})},body:audio.subarray(start,end+1)})
 })
 await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({json:{items:people,count:people.length}}))
 await page.route('**/api/v1/asr/hotword-lists',route=>route.fulfill({json:{items:[{id:'words',name:'项目词表',kind:'custom',terms:['语音合成','转写'],term_count:2,created_at:now,updated_at:now}],count:1}}))
 return {errors}
}
async function defaultPanes(page:Page,kind:'asr'|'tts'){
 const expected=kind==='asr'?.35:.5
 await expect.poll(async()=>{const input=(await page.locator(`#${kind}-input-pane`).boundingBox())!.width,settings=(await page.locator(`#${kind}-settings-pane`).boundingBox())!.width;return Math.abs(input/(input+settings)-expected)}).toBeLessThanOrEqual(.035)
}
async function drag(page:Page,delta:number){
 const box=(await page.getByRole('separator').boundingBox())!
 await page.mouse.move(box.x+box.width/2,box.y+box.height/3)
 await page.mouse.down();await page.mouse.move(box.x+box.width/2+delta,box.y+box.height/3,{steps:12});await page.mouse.up()
}
async function noOverflow(page:Page,width:number){
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 await expect(page.locator('vite-error-overlay')).toHaveCount(0)
 expect(await page.title()).toContain('Sandevistan-Audio')
}

for(const viewport of [{width:1024,height:768},{width:1280,height:720},{width:1366,height:768},{width:1440,height:900},{width:1920,height:1080}])for(const gpuAvailable of [true,false]){
 test(`desktop controls and default panes at ${viewport.width}x${viewport.height}, GPU ${gpuAvailable?'available':'unavailable'}`,async({page})=>{
  await page.setViewportSize(viewport)
  const {errors}=await fixture(page,gpuAvailable)
  await page.goto('/#asr');await defaultPanes(page,'asr')
  await page.locator('input[type=file]').setInputFiles({name:'会议录音.wav',mimeType:'audio/wav',buffer:wave()})
  await expect(page.locator('.asr-file-info')).toContainText('会议录音.wav')
  await expect(page.getByText('会议录音.wav',{exact:true})).toHaveCount(1)
  await expect(page.locator('.asr-upload .dropzone')).toHaveCount(0)
  expect((await page.locator('.asr-upload').boundingBox())!.height).toBeLessThan(180)
  await expect(page.locator('.asr-settings-toggle')).toHaveAttribute('aria-expanded','true')
  await expect(page.getByLabel('ASR 模型', {exact:true})).toBeVisible()
  await expect(page.getByLabel('ASR 计算设备')).toHaveValue(gpuAvailable?'gpu':'cpu')
  if(!gpuAvailable){
   await expect(page.locator('.asr-settings').getByText('No compatible NVIDIA GPU is available',{exact:true})).toHaveCount(1)
   await expect(page.locator('.asr-settings .device-hint')).toBeVisible()
  }
  await expect(page.getByRole('button',{name:'开始转写',exact:true})).toBeInViewport()
  if(viewport.width>=1280){
   // Stacked processing rows intentionally scroll inside the settings pane.
   for(const control of await page.locator('.asr-settings-body select,.asr-settings-body [role=switch],.asr-settings-body .enhancement-settings-button').all()){
    await control.scrollIntoViewIfNeeded();await expect(control).toBeInViewport()
   }
   await page.locator('.asr-settings-body').evaluate(e=>e.scrollTop=0)
  }
  await noOverflow(page,viewport.width)
  await page.screenshot({path:`/tmp/pc-layout-final-asr-${viewport.width}.png`})
  await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:'语音合成',exact:true}).click()
  await defaultPanes(page,'tts')
  await page.locator('.text-editor textarea').fill('调整分栏后仍保留这段文本。')
  await expect(page.locator('.document-settings-toggle')).toHaveAttribute('aria-expanded','true')
  await expect(page.getByLabel('TTS 模型',{exact:true})).toBeVisible()
  await expect(page.getByLabel('TTS 计算设备')).toHaveValue(gpuAvailable?'gpu':'cpu')
  if(!gpuAvailable){
   await expect(page.locator('#tts-device-warning')).toBeVisible()
   await expect(page.locator('.tts-settings').getByText('No compatible NVIDIA GPU is available',{exact:true})).toHaveCount(1)
  }
  await expect(page.getByRole('button',{name:'生成语音',exact:true})).toBeInViewport()
  await page.locator('#tts-acceleration').scrollIntoViewIfNeeded()
  await expect(page.locator('#tts-acceleration')).toBeInViewport()
  await noOverflow(page,viewport.width)
  await page.screenshot({path:`/tmp/pc-layout-final-tts-${viewport.width}.png`})
  await page.locator('.language-switcher select:visible').first().selectOption('en-US')
  await expect(page.getByLabel('TTS model',{exact:true})).toBeVisible()
  await page.locator('#tts-acceleration').scrollIntoViewIfNeeded()
  await expect(page.locator('#tts-acceleration')).toBeInViewport()
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button').first().click()
  await expect(page.getByLabel('ASR model',{exact:true})).toBeVisible()
  await page.getByRole('switch',{name:'Audio enhancement',exact:true}).scrollIntoViewIfNeeded()
  await expect(page.getByRole('switch',{name:'Audio enhancement',exact:true})).toBeInViewport()
  await noOverflow(page,viewport.width)
  expect(errors).toEqual([])
 })
}

test('divider drag, independent durable preferences and reset preserve input nodes',async({page})=>{
 const {errors}=await fixture(page)
 await page.goto('/#asr');await defaultPanes(page,'asr')
 await page.locator('input[type=file]').setInputFiles({name:'保留文件.wav',mimeType:'audio/wav',buffer:wave()})
 await page.locator('input[type=file]').evaluate(e=>{(window as unknown as {selectedInput:Element}).selectedInput=e})
 await drag(page,-180)
 const width=(await page.locator('#asr-input-pane').boundingBox())!.width
 expect(width).toBeLessThan(550)
 expect(await page.locator('input[type=file]').evaluate(e=>(window as unknown as {selectedInput:Element}).selectedInput===e)).toBe(true)
 await expect(page.locator('.asr-file-info')).toContainText('保留文件.wav')
 await page.getByRole('tab',{name:'新建视频转写',exact:true}).click()
 expect((await page.locator('#asr-input-pane').boundingBox())!.width).toBeCloseTo(width,0)
 await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:'语音合成',exact:true}).click()
 await defaultPanes(page,'tts')
 await page.locator('.text-editor textarea').fill('分栏调整保留草稿。')
 await page.locator('.text-editor textarea').evaluate(e=>{(window as unknown as {textInput:Element}).textInput=e})
 await drag(page,130)
 expect(await page.locator('.text-editor textarea').evaluate(e=>(window as unknown as {textInput:Element}).textInput===e)).toBe(true)
 await expect(page.locator('.text-editor textarea')).toHaveValue('分栏调整保留草稿。')
 const ratios=await page.evaluate(key=>JSON.parse(localStorage.getItem(key)!),layoutKey)
 expect(ratios.asr).toBeLessThan(.5);expect(ratios.tts).toBeGreaterThan(.5)
 await page.reload()
 await expect.poll(async()=>(await page.locator('#tts-input-pane').boundingBox())!.width).toBeGreaterThan(800)
 await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:'转写工作台',exact:true}).click()
 await expect.poll(async()=>(await page.locator('#asr-input-pane').boundingBox())!.width).toBeCloseTo(width,0)
 await page.locator('.asr-settings .reset-settings').click()
 expect(await page.evaluate(key=>JSON.parse(localStorage.getItem(key)!),layoutKey)).toEqual(ratios)
 await page.locator('.workbench-reset-layout:visible').click();await defaultPanes(page,'asr')
 expect(await page.evaluate(key=>JSON.parse(localStorage.getItem(key)!).tts,layoutKey)).toBe(ratios.tts)
 expect(errors).toEqual([])
})

test('divider keyboard, minimum widths, cancellation and resize round trip',async({page})=>{
 const {errors}=await fixture(page)
 await page.goto('/#tts');await defaultPanes(page,'tts')
 const divider=page.getByRole('separator')
 await divider.focus();await page.keyboard.press('Shift+ArrowRight')
 expect(Number(await divider.getAttribute('aria-valuenow'))).toBe(60)
 await page.keyboard.press('Home');expect((await page.locator('#tts-input-pane').boundingBox())!.width).toBeCloseTo(320,0)
 await page.keyboard.press('End');expect((await page.locator('#tts-settings-pane').boundingBox())!.width).toBeCloseTo(400,0)
 await divider.dblclick();await defaultPanes(page,'tts')
 await divider.focus();await page.keyboard.press('ArrowLeft')
 expect(Number(await divider.getAttribute('aria-valuenow'))).toBe(48)
 const before=await page.evaluate(key=>localStorage.getItem(key),layoutKey)
 const box=(await divider.boundingBox())!
 await page.mouse.move(box.x+8,box.y+40);await page.mouse.down();await page.mouse.move(box.x-150,box.y+40)
 await divider.dispatchEvent('pointercancel',{pointerId:1})
 await page.mouse.up()
 expect(await page.evaluate(key=>localStorage.getItem(key),layoutKey)).toBe(before)
 expect(Number(await divider.getAttribute('aria-valuenow'))).toBe(48)
 await divider.focus();await page.keyboard.press('End')
 const saved=await page.evaluate(key=>localStorage.getItem(key),layoutKey)
 await page.setViewportSize({width:1024,height:768})
 await expect.poll(async()=>(await page.locator('#tts-settings-pane').boundingBox())!.width).toBeCloseTo(400,0)
 await page.setViewportSize({width:390,height:844});await expect(divider).toBeHidden();await noOverflow(page,390)
 await page.setViewportSize({width:1440,height:900})
 await expect.poll(async()=>(await page.locator('#tts-settings-pane').boundingBox())!.width).toBeCloseTo(400,0)
 expect(await page.evaluate(key=>localStorage.getItem(key),layoutKey)).toBe(saved)
 expect(errors).toEqual([])
})

for(const stored of ['{bad json','{"asr":null,"tts":2}'])test(`invalid stored layout uses module defaults: ${stored}`,async({page})=>{
 await page.addInitScript(({key,value})=>localStorage.setItem(key,value),{key:layoutKey,value:stored})
 const {errors}=await fixture(page)
 await page.goto('/#asr');await defaultPanes(page,'asr')
 await drag(page,-60)
 expect((await page.locator('#asr-input-pane').boundingBox())!.width).toBeLessThan(650)
 expect(errors).toEqual([])
})

test('layout storage failure keeps resizing usable',async({page})=>{
 await page.addInitScript(key=>{const set=Storage.prototype.setItem;Storage.prototype.setItem=function(name,value){if(name===key)throw new DOMException('Storage unavailable','SecurityError');set.call(this,name,value)}},layoutKey)
 const {errors}=await fixture(page)
 await page.goto('/#tts');await defaultPanes(page,'tts')
 await drag(page,-80)
 expect((await page.locator('#tts-input-pane').boundingBox())!.width).toBeLessThan(650)
 await page.locator('.workbench-reset-layout:visible').click();await defaultPanes(page,'tts')
 expect(errors).toEqual([])
})

test('resizing preserves video source, playback position and selected region',async({page},info)=>{
 const root=path.resolve('..'),video=path.join(root,'tmp/pc-layout-video',`worker-${info.workerIndex}`,'preview.webm')
 const python=path.join(root,process.platform==='win32'?'.runtime/api/Scripts/python.exe':'.runtime/api/bin/python')
 execFileSync(python,['-c',"from pathlib import Path; from tests.test_video_transcription import video_file; import sys; p=Path(sys.argv[1]); p.parent.mkdir(parents=True,exist_ok=True); video_file(p)",video],{cwd:root,env:{...process.env,AUDIO_INTEL_LOAD_ENV:'0',AUDIO_INTEL_DATA_DIR:'tmp/pc-layout-video/data'}})
 const {errors}=await fixture(page)
 await page.goto('/#asr');await defaultPanes(page,'asr')
 await page.getByRole('tab',{name:'新建视频转写',exact:true}).click()
 await page.locator('input[type=file]').setInputFiles(video)
 const preview=page.locator('.video-region-media video')
 await expect.poll(()=>preview.evaluate((e:HTMLVideoElement)=>e.videoWidth)).toBeGreaterThan(0)
 await page.getByRole('button',{name:'框选 PPT 区域',exact:true}).click()
 await page.locator('.video-region-fields input').first().fill('20')
 const region=await page.locator('.video-region-fields input').evaluateAll(items=>items.map(e=>(e as HTMLInputElement).value))
 await preview.evaluate((e:HTMLVideoElement)=>{e.currentTime=2;(window as unknown as {videoNode:Element}).videoNode=e})
 const source=await preview.getAttribute('src')
 await drag(page,-130)
 expect(await preview.evaluate(e=>(window as unknown as {videoNode:Element}).videoNode===e)).toBe(true)
 await expect(preview).toHaveAttribute('src',source!)
 expect(await preview.evaluate((e:HTMLVideoElement)=>e.currentTime)).toBeCloseTo(2,1)
 expect(await page.locator('.video-region-fields input').evaluateAll(items=>items.map(e=>(e as HTMLInputElement).value))).toEqual(region)
 await expect(page.locator('.video-region-box')).toBeVisible()
 await expect(page.locator('.asr-file-info')).toContainText('preview.webm')
 expect(errors).toEqual([])
})

test('resizing a clone workspace preserves person, sample and reference interval',async({page})=>{
 await page.addInitScript(()=>localStorage.setItem('audio-intel:tts-reference-ranges:v1',JSON.stringify({'sample-0':{start:3,end:8}})))
 const {errors}=await fixture(page)
 await page.goto('/#tts');await defaultPanes(page,'tts')
 await page.getByRole('tab',{name:'声音克隆',exact:true}).click()
 await page.getByRole('tab',{name:'声纹库',exact:true}).click()
 await expect(page.getByLabel('TTS 声纹样本')).toHaveValue('sample-0')
 const card=page.locator('.reference-range-card')
 const text=await card.textContent()
 await card.evaluate(e=>{(window as unknown as {rangeNode:Element}).rangeNode=e})
 await drag(page,-150)
 expect(await card.evaluate(e=>(window as unknown as {rangeNode:Element}).rangeNode===e)).toBe(true)
 await expect(page.getByLabel('声纹人员',{exact:true})).toHaveValue('person-0')
 await expect(page.getByLabel('TTS 声纹样本')).toHaveValue('sample-0')
 expect(await card.textContent()).toBe(text)
 expect(errors).toEqual([])
})

for(const locale of ['zh-CN','en-US'])test(`compact library and video reading in ${locale}`,async({page})=>{
 await page.addInitScript(locale=>localStorage.setItem('audio-intel:ui-locale:v1',locale),locale)
 await page.setViewportSize({width:1280,height:720})
 const {errors}=await fixture(page)
 await page.goto('/#library/voiceprints')
 await expect(page.locator('h1')).toHaveCount(1)
 await expect(page.locator('.person-row')).toHaveCount(27)
  expect(await page.locator('.people-alphabet').evaluate(e=>e.scrollHeight-e.clientHeight)).toBeLessThanOrEqual(1)
 expect((await page.locator('.voiceprint-layout').boundingBox())!.y).toBeLessThan(250)
 const main=await page.locator('#main-content').evaluate(e=>e.scrollTop)
 await page.locator('.people-alphabet button').filter({hasText:/^Z$/}).click()
 await expect(page.locator('#people-group-Z')).toBeInViewport()
  await expect(page.locator('.people-alphabet button').filter({hasText:/^Z$/})).toHaveAttribute('aria-current','location')
 expect(await page.locator('#main-content').evaluate(e=>e.scrollTop)).toBe(main)
 await noOverflow(page,1280)
 await page.screenshot({path:`/tmp/pc-layout-final-library-${locale}.png`})
 await page.locator('.library-tabs button').last().click()
 await expect(page.locator('h1')).toHaveCount(1)
 await expect(page.locator('.hotword-editor textarea')).toBeVisible()
 const playback=Promise.withResolvers<void>()
 let playbackRequested=false
 await page.route('**/api/v1/jobs/layout-video/playback',async route=>{playbackRequested=true;await playback.promise;await route.fallback()})
 try{
  await page.goto('/#asr')
  await page.locator('.asr-tabs button').last().click()
  await expect(page.locator('.video-section')).toHaveCount(20)
  await expect.poll(()=>playbackRequested).toBe(true)
  await expect(page.locator('.media-loading')).toBeVisible()
  await expect(page.locator('.video-paragraph p').first()).toBeInViewport()
 }finally{playback.resolve()}
 await expect.poll(()=>page.locator('.result-panel audio').evaluate((e:HTMLAudioElement)=>e.readyState)).toBeGreaterThanOrEqual(1)
 await expect(page.locator('.media-loading')).toHaveCount(0)
 const reader=page.locator('.video-transcript')
 expect((await reader.boundingBox())!.height).toBeGreaterThanOrEqual(280)
 await expect(page.locator('.video-paragraph p').first()).toBeInViewport()
 await expect(page.locator('.wave-area')).toBeHidden()
 await page.locator('.video-frame').first().click()
 const dialog=page.getByRole('dialog')
  await expect(dialog.locator('img')).toBeVisible()
 await dialog.getByRole('button',{name:locale==='zh-CN'?'关闭':'Close',exact:true}).click()
 await expect(dialog).toHaveCount(0)
 const range=page.locator('.video-playback-progress')
 await range.fill('15')
 await expect.poll(()=>page.locator('.result-panel audio').evaluate((e:HTMLAudioElement)=>e.currentTime)).toBeCloseTo(15,0)
 await page.locator('.waveform-toggle').click();await expect(page.locator('.wave-area')).toBeVisible()
 await page.locator('.waveform-toggle').click()
 await noOverflow(page,1280)
 await page.screenshot({path:`/tmp/pc-layout-final-video-${locale}.png`})
 expect(errors).toEqual([])
})

test('mobile controls and tooltip remain reachable without horizontal overflow',async({page})=>{
 await page.setViewportSize({width:390,height:844})
 const {errors}=await fixture(page,false)
 await page.goto('/#asr')
 await expect(page.getByRole('separator')).toBeHidden()
 await expect(page.locator('.asr-settings .notice')).toBeVisible()
 await page.locator('input[type=file]').setInputFiles({name:'会议.wav',mimeType:'audio/wav',buffer:wave()})
 await page.locator('.asr-settings-toggle').click();await page.locator('.asr-advanced-toggle').click()
 await expect(page.locator('.asr-settings .notice')).toHaveCount(0)
 await expect(page.locator('#asr-device-warning')).toBeVisible()
 await expect(page.getByLabel('ASR 计算设备')).toHaveValue('cpu')
 for(const help of await page.locator('.asr-settings-body .acceleration-control .info-tooltip button').all()){
  await help.focus();await expect(page.getByRole('tooltip')).toBeVisible()
  const tooltip=(await page.getByRole('tooltip').boundingBox())!
  expect(tooltip.x).toBeGreaterThanOrEqual(0);expect(tooltip.x+tooltip.width).toBeLessThanOrEqual(390)
  await page.keyboard.press('Escape');await expect(page.getByRole('tooltip')).toHaveCount(0)
 }
 await noOverflow(page,390)
 expect(await page.locator('.asr-create button:visible,.asr-create select:visible').evaluateAll(items=>items.filter(e=>e.getBoundingClientRect().height<44).map(e=>e.textContent))).toEqual([])
 await page.evaluate(()=>{(document.activeElement as HTMLElement)?.blur();window.scrollTo(0,0);document.querySelector('#main-content')?.scrollTo(0,0)})
 await page.screenshot({path:'/tmp/pc-layout-final-mobile-asr.png',fullPage:true})
 await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:'语音合成',exact:true}).click()
 await page.locator('.document-settings-toggle').click()
 await expect(page.getByLabel('TTS 模型',{exact:true})).toBeVisible()
 await noOverflow(page,390)
 expect(await page.locator('.tts-settings button:visible,.tts-settings select:visible').evaluateAll(items=>items.filter(e=>e.getBoundingClientRect().height<44).map(e=>e.textContent))).toEqual([])
 await page.evaluate(()=>{(document.activeElement as HTMLElement)?.blur();window.scrollTo(0,0);document.querySelector('#main-content')?.scrollTo(0,0)})
 await page.screenshot({path:'/tmp/pc-layout-final-mobile-tts.png',fullPage:true})
 expect(errors).toEqual([])
})

test('pending page focus preserves an already focused workspace control @timing',async({page})=>{
 const {errors}=await fixture(page)
 await page.clock.install({time:new Date('2026-01-01T00:00:00Z')});await page.clock.pauseAt(new Date('2026-01-01T00:01:00Z'))
 await page.goto('/#tts');await defaultPanes(page,'tts')
 const divider=page.getByRole('separator');await divider.focus()
 await page.clock.runFor(32);await expect(divider).toBeFocused()
 await page.keyboard.press('Home');await expect.poll(async()=>(await page.locator('#tts-input-pane').boundingBox())!.width).toBeCloseTo(320,0)
 expect(errors).toEqual([])
})
