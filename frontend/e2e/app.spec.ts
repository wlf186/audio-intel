import {expect,test} from '@playwright/test'
import type {Page,Route} from '@playwright/test'

function testWave(){const dataBytes=16000*2*5;const wav=Buffer.alloc(44+dataBytes);wav.write('RIFF',0);wav.writeUInt32LE(wav.length-8,4);wav.write('WAVE',8);wav.write('fmt ',12);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(16000,24);wav.writeUInt32LE(32000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(dataBytes,40);for(let index=44;index<wav.length;index+=2)wav.writeInt16LE(Math.round(Math.sin(index/20)*1200),index);return wav}
function routeJobList(page:Page,handler:(route:Route)=>Promise<unknown>|unknown){return page.route(url=>url.pathname==='/api/v1/jobs',handler)}

test.beforeEach(async({page})=>{await page.route('**/api/v1/capabilities',async route=>{const response=await route.fetch();const body=await response.json();body.events={...body.events,sse:false};await route.fulfill({response,json:body})})})
test.afterEach(async({page})=>{await page.unrouteAll({behavior:'ignoreErrors'})})

test('HTTPS certificate help is available before authentication without protected requests',async({page})=>{
 const errors:string[]=[]
 const protectedRequests:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 page.on('request',request=>{const path=new URL(request.url()).pathname;if(['/api/v1/jobs','/api/v1/system','/api/v1/capabilities','/api/v1/voiceprints/people','/api/v1/asr/hotword-lists'].includes(path))protectedRequests.push(path)})
 await page.route('**/api/v1/auth/session',route=>route.fulfill({json:{required:true,authenticated:false}}))
 await page.route('**/api/v1/tls/bootstrap',route=>route.fulfill({json:{protocol:'https',ca_installation_available:true,ca_sha256_fingerprint:'AA:BB:CC:DD',ca_download_urls:{cer:'/api/v1/tls/root-ca.cer',pem:'/api/v1/tls/root-ca.pem'}}}))
 await page.goto('/#asr')
 await expect(page.getByRole('heading',{name:'访问验证'})).toBeVisible()
 await page.getByRole('button',{name:'先安装 HTTPS 根证书'}).click()
 await expect(page.getByText('ROOT CA · SHA-256')).toBeVisible()
 await expect(page.getByText('AA:BB:CC:DD')).toBeVisible()
 await expect(page.getByRole('link',{name:'下载 Windows / iOS 证书'})).toHaveAttribute('href','/api/v1/tls/root-ca.cer')
 await expect(page.getByText(/证书信任设置/)).toBeVisible()
 expect(protectedRequests).toEqual([])
 await page.screenshot({path:'/tmp/audio-intel-tls-preauth-desktop.png',fullPage:false})
 await page.setViewportSize({width:390,height:844})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 const back=page.getByRole('button',{name:'返回登录'})
 await expect(back).toBeVisible()
 const box=await back.boundingBox()
 expect(box!.height).toBeGreaterThanOrEqual(44)
 await page.screenshot({path:'/tmp/audio-intel-tls-preauth-mobile.png',fullPage:false})
 expect(errors).toEqual([])
})

test('global HTTPS certificate help opens from the header',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 await page.route('**/api/v1/tls/bootstrap',route=>route.fulfill({json:{protocol:'http',ca_installation_available:false}}))
 await page.goto('/#asr')
 await page.getByRole('button',{name:'打开 HTTPS 证书帮助'}).click()
 await expect(page.getByRole('heading',{name:'HTTPS 证书与浏览器录音'})).toBeVisible()
 await expect(page.getByText(/当前服务使用 HTTP/)).toBeVisible()
 await page.getByRole('button',{name:'关闭 HTTPS 证书帮助'}).click()
 await expect(page.getByRole('heading',{name:'HTTPS 证书与浏览器录音'})).toHaveCount(0)
 expect(errors).toEqual([])
})

test('summary SSE stays idle and task details load once with retry states',async({page})=>{
 const errors:string[]=[]
 let jobsCalls=0
 let detailCalls=0
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 await page.addInitScript(()=>{
  class MockEventSource extends EventTarget{
   static instances:MockEventSource[]=[]
   url:string
   onopen:((event:Event)=>void)|null=null
   onerror:((event:Event)=>void)|null=null
   constructor(url:string|URL){super();this.url=String(url);MockEventSource.instances.push(this);queueMicrotask(()=>this.onopen?.(new Event('open')))}
   close(){}
  }
  ;(window as typeof window&{EventSource:typeof EventSource;emitJobEvent:(name:string,data:unknown)=>void}).EventSource=MockEventSource as unknown as typeof EventSource
  ;(window as typeof window&{emitJobEvent:(name:string,data:unknown)=>void}).emitJobEvent=(name,data)=>{for(const source of MockEventSource.instances)source.dispatchEvent(new MessageEvent(name,{data:JSON.stringify(data)}))}
 })
 const now=new Date().toISOString()
 const summary={id:'summary-asr',kind:'asr',state:'succeeded',stage:'completed',progress:1,display_name:'按需详情.wav',created_at:now,updated_at:now,compute_device:'cpu',source_url:'data:audio/wav;base64,'+testWave().toString('base64')}
 const detail={...summary,request:{compute_device:'cpu',language:'Chinese',align:true},result:{text:'按需加载成功',language:'Chinese',duration:1,timestamp_precision:'segment',segments:[],speakers:[],artifacts:[]}}
 await routeJobList(page,route=>{jobsCalls+=1;return route.fulfill({json:{items:[summary],count:1,total:1,limit:100,offset:0,has_more:false}})})
 await page.route(url=>url.pathname==='/api/v1/jobs/summary-asr',async route=>{detailCalls+=1;if(detailCalls===1){await new Promise(resolve=>setTimeout(resolve,250));return route.fulfill({status:503,json:{detail:'temporary'}})}return route.fulfill({json:detail})})
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{default_model:'qwen3-asr-0.6b',models:[],hotword_library:{supported:true,max_lists:100,max_terms_per_list:200,max_selected_lists:8,max_selected_terms:500,max_prompt_chars:8000,max_name_chars:80,max_term_chars:64},speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true},limits:{max_clone_reference_seconds:15},events:{sse:true,global_url:'/api/v1/events',heartbeat_seconds:15,history_replay:false,global_mode:'summary_delta'}}}))
 await page.goto('/#asr')
 await expect(page.getByRole('heading',{name:'正在加载转写结果'})).toBeVisible()
 await expect(page.getByRole('heading',{name:'转写结果加载失败'})).toBeVisible()
 await page.getByRole('button',{name:'重新加载'}).click()
 await expect(page.locator('.result-head')).toContainText('按需详情.wav')
 await expect(page.locator('.waveform-empty')).toHaveAttribute('aria-label','音频播放位置')
 await page.evaluate(job=>{const target=window as typeof window&{emitJobEvent:(name:string,data:unknown)=>void};target.emitJobEvent('snapshot',{jobs:[job],workers:[]});target.emitJobEvent('heartbeat',{})},summary)
 await page.waitForTimeout(5200)
 expect(jobsCalls).toBe(1)
 expect(detailCalls).toBe(2)
 await page.screenshot({path:'/tmp/audio-intel-summary-detail-desktop.png',fullPage:false})
 await page.setViewportSize({width:390,height:844})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await page.screenshot({path:'/tmp/audio-intel-summary-detail-mobile.png',fullPage:false})
 expect(errors.filter(message=>!message.includes('503 (Service Unavailable)'))).toEqual([])
 expect(errors.filter(message=>message.includes('503 (Service Unavailable)'))).toHaveLength(1)
})

test('local API docs load offline and execute the health probe',async({page})=>{
 const errors:string[]=[]
 const external:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 page.on('request',request=>{if(new URL(request.url()).origin!=='http://127.0.0.1:20810')external.push(request.url())})
 await page.goto('/docs')
 await expect(page.locator('.swagger-ui')).toBeVisible()
 await expect(page.getByText('快速开始 / Quick start')).toBeVisible()
 const firstTag=page.locator('.opblock-tag-section').first()
 await expect.poll(async()=>Math.round((await firstTag.boundingBox())?.y||9999)).toBeLessThanOrEqual(950)
 await firstTag.locator('.opblock-tag').click()
 const health=page.locator('.opblock').filter({hasText:'/api/v1/health'}).first()
 await health.locator('.opblock-summary').click()
 await health.getByRole('button',{name:'Try it out'}).click()
 await health.getByRole('button',{name:'Execute'}).click()
 await expect(health.locator('.response-col_status').filter({hasText:'200'}).first()).toBeVisible()
 await page.screenshot({path:'/tmp/audio-intel-docs-desktop.png',fullPage:false})
 await page.setViewportSize({width:390,height:844})
 const width=await page.evaluate(()=>document.documentElement.scrollWidth)
 expect(width).toBeLessThanOrEqual(390)
 await expect.poll(async()=>Math.round((await firstTag.boundingBox())?.y||9999)).toBeLessThanOrEqual(1400)
 await page.screenshot({path:'/tmp/audio-intel-docs-mobile.png',fullPage:false})
 expect(external).toEqual([])
 expect(errors).toEqual([])
})

test('Sandevistan-Audio branding and TTS transport render as local assets',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 const now=new Date().toISOString()
 const job={id:'local-tts-preview',kind:'tts',state:'succeeded',stage:'completed',progress:1,display_name:'本地合成预览',created_at:now,updated_at:now,request:{compute_device:'cpu'},result:{duration:5,format:'wav',speaker:'Vivian',artifacts:[{name:'speech.wav',mime_type:'audio/wav',size_bytes:160044}]}}
 await routeJobList(page,route=>route.fulfill({json:{items:[job]}}))
 await page.route('**/api/v1/jobs/local-tts-preview/artifacts/speech.wav',route=>route.fulfill({contentType:'audio/wav',body:testWave()}))
 await page.goto('/#tts')
 await expect(page).toHaveTitle(/Sandevistan-Audio/)
 await expect(page.locator('.brand-type b')).toHaveText('SANDEVISTAN-AUDIO')
 const mark=page.locator('.brand-lockup img')
 await expect(mark).toBeVisible()
 await expect.poll(()=>mark.evaluate((image:HTMLImageElement)=>image.naturalWidth)).toBeGreaterThan(0)
 const player=page.locator('.audio-transport audio')
 await expect(player).toHaveCount(1)
 await page.getByRole('button',{name:'播放当前合成结果'}).click()
 await expect.poll(()=>player.evaluate((audio:HTMLAudioElement)=>audio.currentTime)).toBeGreaterThan(.1)
 await page.getByRole('button',{name:'暂停当前合成结果'}).click()
 await expect.poll(()=>player.evaluate((audio:HTMLAudioElement)=>audio.paused)).toBe(true)
 expect(errors).toEqual([])
})

test('TTS exposes the 0.6B control boundary without sending unsupported instructions',async({page})=>{
 const errors:string[]=[]
 let ttsBody=''
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 await routeJobList(page,route=>route.fulfill({json:{items:[],count:0,total:0,limit:100,offset:0,has_more:false}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true,aligner_languages:['Chinese','English']},tts:{languages:['Auto','Chinese','English'],default_language:'Auto',preset_speaker_native_languages:{Vivian:'Chinese'},controls:{instruction_voice_modes:[],speaking_rate_parameter:false,pitch_parameter:false,sampling_parameters:false}},limits:{max_clone_reference_seconds:15},events:{sse:false}}}))
 await page.route('**/api/v1/tts/jobs',async route=>{ttsBody=(await route.request().postDataBuffer())?.toString()||'';await route.fulfill({status:202,json:{id:'tts-control-contract',kind:'tts',state:'queued',stage:'queued',progress:0,display_name:'能力边界校验',created_at:new Date().toISOString(),request:{compute_device:'gpu'}}})})
 await page.goto('/#tts')
 await expect(page).toHaveTitle(/Sandevistan-Audio/)
 const note=page.getByText('当前模型与音色模式根据文本语义和标点自动处理韵律，不接受自然语言高级指令。')
 await expect(note).toBeVisible()
 await expect(page.locator('[name="instruct"], [name="instructions"]')).toHaveCount(0)
 await page.locator('.text-editor textarea').fill('这是能力边界校验。')
 await page.getByRole('button',{name:'生成语音'}).click()
 await expect.poll(()=>ttsBody).toContain('这是能力边界校验。')
 expect(ttsBody).not.toContain('name="instruct"')
 await note.scrollIntoViewIfNeeded()
 await page.screenshot({path:'/tmp/audio-intel-tts-controls-desktop.png',fullPage:false})
 await page.setViewportSize({width:390,height:844})
 await note.scrollIntoViewIfNeeded()
 await expect(note).toBeVisible()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await page.screenshot({path:'/tmp/audio-intel-tts-controls-mobile.png',fullPage:false})
 expect(errors).toEqual([])
})

test('TTS 1.7B exposes VoiceDesign instructions and applies the model GPU gate',async({page})=>{
 const errors:string[]=[]
 let ttsBody=''
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 const baseControls={speaking_rate_parameter:false,pitch_parameter:false,sampling_parameters:false,max_instruction_chars:1000}
 const models=[
  {id:'qwen3-tts-0.6b',name:'Qwen3-TTS 0.6B',default:true,installed:true,installation_state:'installed',voice_modes:['preset','profile','inline_clone','voiceprint'],compute_devices:[{id:'cpu',precision:'FP32',available:true,default:false,quantized:false},{id:'gpu',precision:'BF16',available:true,default:true,quantized:false,minimum_memory_mib:3840,total_memory_mib:4096}],controls:{...baseControls,instruction_voice_modes:[],instruction_required_voice_modes:[]},checkpoints:[]},
  {id:'qwen3-tts-1.7b',name:'Qwen3-TTS 1.7B',default:false,installed:true,installation_state:'installed',voice_modes:['preset','profile','inline_clone','voiceprint','voice_design'],compute_devices:[{id:'cpu',precision:'FP32',available:true,default:true,quantized:false},{id:'gpu',precision:'BF16',available:false,default:false,quantized:false,minimum_memory_mib:7936,total_memory_mib:4096,unavailable_reason_code:'insufficient_gpu_memory',unavailable_reason:'This model requires at least 7936 MiB total GPU memory; detected 4096 MiB'}],controls:{...baseControls,instruction_voice_modes:['preset','voice_design'],instruction_required_voice_modes:['voice_design']},checkpoints:[]},
 ]
 await routeJobList(page,route=>route.fulfill({json:{items:[],count:0,total:0,limit:100,offset:0,has_more:false}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true,aligner_languages:['Chinese']},tts:{default_model:'qwen3-tts-0.6b',model_capabilities:models,languages:['Auto','Chinese'],default_language:'Auto',preset_speaker_native_languages:{Vivian:'Chinese'},controls:models[0].controls},limits:{max_clone_reference_seconds:15},events:{sse:false}}}))
 await page.route('**/api/v1/tts/jobs',async route=>{ttsBody=(await route.request().postDataBuffer())?.toString()||'';await route.fulfill({status:202,json:{id:'tts-voice-design',kind:'tts',state:'queued',stage:'queued',progress:0,display_name:'音色设计',created_at:new Date().toISOString(),request:{model:'qwen3-tts-1.7b',compute_device:'cpu'}}})})
 await page.goto('/#tts')
 await page.getByLabel('TTS 模型').selectOption('qwen3-tts-1.7b')
 await expect(page.getByLabel('TTS 计算设备')).toHaveValue('cpu')
 await expect(page.getByText(/至少需要 7936 MiB 显存.*4096 MiB/)).toBeVisible()
 await page.getByRole('tab',{name:'音色设计'}).click()
 await page.getByLabel('合成文本').fill('欢迎收听今天的节目。')
 const submit=page.getByRole('button',{name:'生成语音'})
 await expect(submit).toBeDisabled()
 await page.getByRole('button',{name:'低沉温柔地表达'}).click()
 await expect(page.getByLabel('音色与表达指令')).toHaveValue('低沉温柔地表达')
 await submit.click()
 await expect.poll(()=>ttsBody).toContain('qwen3-tts-1.7b')
 expect(ttsBody).toContain('voice_design')
 expect(ttsBody).toContain('低沉温柔地表达')
 expect(ttsBody).toContain('cpu')
 await page.screenshot({path:'/tmp/audio-intel-tts-1.7b-voice-design-desktop.png',fullPage:false})
 await page.setViewportSize({width:390,height:844})
 await expect(page.getByLabel('TTS 模型')).toHaveValue('qwen3-tts-1.7b')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await page.screenshot({path:'/tmp/audio-intel-tts-1.7b-voice-design-mobile.png',fullPage:false})
 expect(errors).toEqual([])
})

test('TTS draft and clone mode survive background polling',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 await page.goto('/#tts')
 await page.evaluate(()=>{sessionStorage.removeItem('audio-intel:tts-content:v2');sessionStorage.setItem('audio-intel:tts-content:v1',JSON.stringify({text:'',refText:'',refLanguage:'Auto',refJobId:''}));sessionStorage.removeItem('audio-intel:tts-draft-v2');localStorage.removeItem('audio-intel:tts-preferences:v1')})
 await page.reload()
 const text=page.locator('.text-editor textarea')
 await text.fill('')
 await page.waitForTimeout(4500)
 await expect(text).toHaveValue('')
 await page.getByRole('tab',{name:'声音克隆'}).click()
 await expect(page.getByRole('region',{name:'一次性克隆参考'})).toBeVisible()
 await expect(page.getByText('克隆参考自动识别')).toBeVisible()
 await page.waitForTimeout(4500)
 await expect(page.getByRole('region',{name:'一次性克隆参考'})).toBeVisible()
 await expect(page.getByRole('button',{name:/生成语音/})).toBeDisabled()
 const presetTab=page.getByRole('tab',{name:'预置音色'})
 await presetTab.focus()
 await presetTab.press('ArrowRight')
 await expect(page.getByRole('tab',{name:'声音克隆'})).toHaveAttribute('aria-selected','true')
 expect(errors).toEqual([])
})

test('one-off clone reference model controls use the available width',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 const models=[
  {id:'qwen3-asr-0.6b',name:'Qwen3-ASR-0.6B',revision:'r1',installed:true,installation_state:'installed',default:true,compute_devices:[{id:'cpu',precision:'FP32',available:true,default:false,quantized:false},{id:'gpu',precision:'BF16',available:true,default:true,quantized:false,minimum_memory_mib:3840,total_memory_mib:4096}]},
  {id:'qwen3-asr-1.7b',name:'Qwen3-ASR-1.7B',revision:'r2',installed:true,installation_state:'installed',default:false,compute_devices:[{id:'cpu',precision:'FP32',available:true,default:true,quantized:false},{id:'gpu',precision:'BF16',available:false,default:false,quantized:false,minimum_memory_mib:7936,total_memory_mib:4096,unavailable_reason_code:'insufficient_gpu_memory',unavailable_reason:'This model requires at least 7936 MiB total GPU memory; detected 4096 MiB'}]},
 ]
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',offline:true,bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{gpu:{available:true}},models:[],storage:{data:'/tmp/data'}}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{default_model:'qwen3-asr-0.6b',models,speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true,aligner_languages:['Chinese','English']},tts:{languages:['Auto','Chinese','English'],default_language:'Auto',preset_speaker_native_languages:{Vivian:'Chinese'}},limits:{max_clone_reference_seconds:15},events:{sse:false}}}))
 await page.goto('/#tts')
 await page.getByRole('tab',{name:'声音克隆'}).click()
 const panel=page.getByRole('region',{name:'一次性克隆参考'})
 const model=page.getByLabel('克隆参考 ASR 模型')
 const device=page.getByLabel('克隆参考 ASR 计算设备')
 await expect(model.locator('option')).toHaveText(['Qwen3-ASR-0.6B','Qwen3-ASR-1.7B'])
 await device.selectOption('cpu')
 await expect(device).toHaveValue('cpu')
 await device.selectOption('gpu')
 await model.selectOption('qwen3-asr-1.7b')
 await expect(device).toHaveValue('cpu')
 const hint=panel.locator('.device-hint')
 await expect(hint).toContainText(/至少需要 7936 MiB 显存.*4096 MiB/)
 const expectInline=async(width:number,height:number)=>{
  await page.setViewportSize({width,height})
  await panel.scrollIntoViewIfNeeded()
  const panelBox=await panel.boundingBox();const modelBox=await model.boundingBox();const deviceBox=await device.boundingBox();const hintBox=await hint.boundingBox()
  expect(Math.abs(modelBox!.y-deviceBox!.y)).toBeLessThan(2)
  expect(modelBox!.width).toBeGreaterThan(deviceBox!.width*2)
  expect(modelBox!.x).toBeGreaterThanOrEqual(panelBox!.x)
  expect(deviceBox!.x+deviceBox!.width).toBeLessThanOrEqual(panelBox!.x+panelBox!.width)
  expect(hintBox!.y).toBeGreaterThanOrEqual(modelBox!.y+modelBox!.height)
  expect(hintBox!.width).toBeGreaterThan(deviceBox!.width*2)
 }
 await expectInline(1440,900)
 await panel.screenshot({path:'/tmp/audio-intel-clone-controls-desktop.png'})
 await expectInline(1024,820)
 await page.setViewportSize({width:390,height:844})
 await panel.scrollIntoViewIfNeeded()
 const mobileModelBox=await model.boundingBox();const mobileDeviceBox=await device.boundingBox();const mobilePanelBox=await panel.boundingBox()
 expect(Math.abs(mobileModelBox!.x-mobileDeviceBox!.x)).toBeLessThan(2)
 expect(Math.abs(mobileModelBox!.width-mobileDeviceBox!.width)).toBeLessThan(2)
 expect(mobileDeviceBox!.y).toBeGreaterThanOrEqual(mobileModelBox!.y+mobileModelBox!.height)
 expect(mobileModelBox!.width).toBeGreaterThan(mobilePanelBox!.width*.8)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await panel.screenshot({path:'/tmp/audio-intel-clone-controls-mobile.png'})
 expect(errors).toEqual([])
})

test('ASR playback, seek and transcript search are interactive',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 const now=new Date().toISOString()
 const result={text:'欢迎使用本地转写。',language:'Chinese',duration:5,timestamp_precision:'word_or_character',speakers:[{id:'Speaker_0',label:'Speaker 0'}],segments:[{id:0,start:0,end:5,speaker:'Speaker_0',speaker_label:'Speaker 0',text:'欢迎使用本地转写。',words:[{text:'欢迎',start:0,end:2},{text:'使用',start:2,end:3.5},{text:'本地转写',start:3.5,end:5}]}],waveform:[.2,.6,.3],artifacts:[]}
 const job={id:'local-asr-preview',kind:'asr',state:'succeeded',stage:'completed',progress:1,display_name:'本地转写预览.wav',created_at:now,updated_at:now,source_url:`data:audio/wav;base64,${testWave().toString('base64')}`,request:{compute_device:'cpu',language:'Chinese',align:true},result}
 await routeJobList(page,route=>route.fulfill({json:{items:[job]}}))
 await page.goto('/#asr')
 await expect(page.getByRole('heading',{name:'音频转写'})).toBeVisible()
 await expect(page.locator('audio')).toHaveCount(1)
 const clock=page.locator('.player-row strong')
 await page.getByRole('button',{name:'播放',exact:true}).click()
 await expect.poll(async()=>page.locator('audio').evaluate((element:HTMLAudioElement)=>element.currentTime)).toBeGreaterThan(.1)
 await page.getByRole('button',{name:'暂停',exact:true}).click()
 const paused=await page.locator('audio').evaluate((element:HTMLAudioElement)=>element.paused)
 expect(paused).toBe(true)
 await expect(clock).not.toHaveText('00:00:00.000')
 const waveform=page.locator('.wave-area canvas')
 await expect(waveform).toHaveAttribute('role','slider')
 const duration=await page.locator('audio').evaluate((element:HTMLAudioElement)=>element.duration)
 await waveform.click({position:{x:Math.max(5,(await waveform.boundingBox())!.width*.7),y:25}})
 await expect.poll(async()=>page.locator('audio').evaluate((element:HTMLAudioElement)=>element.currentTime)).toBeGreaterThan(duration*.5)
 await waveform.focus()
 await page.keyboard.press('Home')
 await expect.poll(async()=>page.locator('audio').evaluate((element:HTMLAudioElement)=>element.currentTime)).toBeLessThan(.1)
 await page.keyboard.press('End')
 await expect.poll(async()=>page.locator('audio').evaluate((element:HTMLAudioElement)=>element.currentTime)).toBeGreaterThan(duration*.9)
 await page.locator('.transcript-tools input').fill('不存在的内容')
 await expect(page.locator('.segments article')).toHaveCount(0)
 await expect(page.getByText('没有匹配的转写片段')).toBeVisible()
 expect(errors).toEqual([])
})

test('ASR model routing and task-scoped hotword selection are explicit',async({page})=>{
 const errors:string[]=[]
 let submitted=''
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 const models=[
  {id:'qwen3-asr-0.6b',name:'Qwen3-ASR-0.6B',revision:'r1',installed:true,installation_state:'installed',default:true,compute_devices:[{id:'cpu',precision:'FP32',available:true,default:false,quantized:false},{id:'gpu',precision:'BF16',available:true,default:true,quantized:false,minimum_memory_mib:3840,total_memory_mib:4096}]},
  {id:'qwen3-asr-1.7b',name:'Qwen3-ASR-1.7B',revision:'r2',installed:true,installation_state:'installed',default:false,compute_devices:[{id:'cpu',precision:'FP32',available:true,default:true,quantized:false},{id:'gpu',precision:'BF16',available:false,default:false,quantized:false,minimum_memory_mib:7936,total_memory_mib:4096,unavailable_reason_code:'insufficient_gpu_memory',unavailable_reason:'This model requires at least 7936 MiB total GPU memory; detected 4096 MiB'}]},
 ]
 const hotword={id:'hotwords_medical',name:'医疗术语',terms:['量子'],term_count:1,created_at:new Date().toISOString(),updated_at:new Date().toISOString()}
 const termOverflowHotword={id:'hotwords_team',name:'团队人名',terms:['Qwen','Whisper'],term_count:2,created_at:new Date().toISOString(),updated_at:new Date().toISOString()}
 const characterOverflowHotword={id:'hotwords_project',name:'项目代号',terms:['SandevistanProject'],term_count:1,created_at:new Date().toISOString(),updated_at:new Date().toISOString()}
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{model:'Qwen3-ASR-0.6B',default_model:'qwen3-asr-0.6b',models,hotword_library:{supported:true,max_lists:100,max_terms_per_list:200,max_selected_lists:8,max_selected_terms:2,max_prompt_chars:30,max_name_chars:80,max_term_chars:64},speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true,languages:['Auto','Chinese'],aligner_languages:['Chinese']},limits:{max_clone_reference_seconds:15},events:{sse:false}}}))
 await page.route('**/api/v1/asr/hotword-lists',route=>route.fulfill({json:{items:[hotword,termOverflowHotword,characterOverflowHotword],count:3}}))
 await page.route('**/api/v1/asr/jobs',async route=>{submitted=(await route.request().postDataBuffer())?.toString()||'';await route.fulfill({status:202,json:{id:'model-hotword-job',kind:'asr',state:'queued',stage:'queued',progress:0,display_name:'model.wav',created_at:new Date().toISOString(),request:{model:'qwen3-asr-1.7b',compute_device:'cpu'}}})})
 await page.goto('/#asr')
 await page.getByLabel('ASR 模型').selectOption('qwen3-asr-1.7b')
 await expect(page.getByLabel('ASR 计算设备')).toHaveValue('cpu')
 await expect(page.getByText(/至少需要 7936 MiB 显存.*4096 MiB/)).toBeVisible()
 await page.getByRole('checkbox',{name:/医疗术语/}).check()
 const termOverflow=page.getByRole('checkbox',{name:/团队人名/})
 await expect(termOverflow).toBeDisabled()
 await expect(page.getByText(/选择“团队人名”后将超出单次任务限制.*3 个唯一热词.*上限 2 个/)).toBeVisible()
 await expect(page.getByRole('checkbox',{name:/项目代号/})).toBeDisabled()
 await expect(page.getByRole('checkbox',{name:/项目代号/}).locator('..')).toHaveAttribute('title',/提示字符.*上限 30 个/)
 const characterIssueId=await page.getByRole('checkbox',{name:/项目代号/}).getAttribute('aria-describedby')
 expect(characterIssueId).toBeTruthy()
 await expect(page.locator(`#${characterIssueId}`)).toContainText(/提示字符.*上限 30 个/)
 await page.screenshot({path:'/tmp/audio-intel-asr-hotword-limit-desktop.png',fullPage:false})
 await page.setViewportSize({width:390,height:844})
 await expect(page.getByText(/选择“项目代号”后将超出单次任务限制/)).toBeVisible()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await page.screenshot({path:'/tmp/audio-intel-asr-hotword-limit-mobile.png',fullPage:false})
 await page.setViewportSize({width:1440,height:900})
 await termOverflow.evaluate(element=>{const checkbox=element as HTMLInputElement;checkbox.disabled=false;checkbox.click()})
 await expect(page.getByRole('alert').filter({hasText:'当前热词选择已超出单次任务限制'})).toContainText(/3 个唯一热词.*上限 2 个/)
 await expect(page.getByRole('button',{name:'开始转写'})).toBeDisabled()
 await termOverflow.uncheck()
 await expect(page.getByRole('alert').filter({hasText:'当前热词选择已超出单次任务限制'})).toHaveCount(0)
 await expect(page.getByRole('button',{name:'开始转写'})).toBeEnabled()
 await page.locator('input[type=file]').setInputFiles({name:'model.wav',mimeType:'audio/wav',buffer:testWave()})
 await page.getByRole('button',{name:'开始转写'}).click()
 await expect.poll(()=>submitted).toContain('qwen3-asr-1.7b')
 expect(submitted).toContain('hotwords_medical')
 expect(submitted).toContain('name="compute_device"')
 expect(submitted).toContain('cpu')
 await expect(page.getByRole('checkbox',{name:/医疗术语/})).not.toBeChecked()
 expect(errors).toEqual([])
})

test('hotword library supports create and mobile layout without overflow',async({page})=>{
 const errors:string[]=[]
 let items:any[]=[]
 let submittedTerms:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{hotword_library:{supported:true,max_lists:1,max_terms_per_list:200,max_selected_lists:8,max_selected_terms:500,max_prompt_chars:8000,max_name_chars:80,max_term_chars:64},speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true,aligner_languages:['Chinese']},limits:{max_clone_reference_seconds:15},events:{sse:false}}}))
 await page.route('**/api/v1/asr/hotword-lists*',async route=>{
  if(route.request().method()==='POST'){
   const body=route.request().postDataJSON() as {name:string;terms:string[]};submittedTerms=body.terms;items=[{id:'hotwords_project',name:body.name,terms:body.terms,term_count:body.terms.length,created_at:new Date().toISOString(),updated_at:new Date().toISOString()}]
   await route.fulfill({status:201,json:items[0]})
  }else await route.fulfill({json:{items,count:items.length}})
 })
 await page.goto('/#hotwords')
 await expect(page.getByRole('heading',{name:'热词库'})).toBeVisible()
 await expect(page.getByRole('heading',{name:'新建词表'})).toBeVisible()
 await expect(page.getByRole('button',{name:'新建词表'})).toHaveCount(0)
 await page.getByLabel('场景名称').fill('未保存草稿')
 await page.locator('.hotword-editor textarea').fill('alpha\nbeta')
 await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:'任务记录'}).click()
 await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:'热词库'}).click()
 await expect(page.getByLabel('场景名称')).toHaveValue('未保存草稿')
 await expect(page.locator('.hotword-editor textarea')).toHaveValue('alpha\nbeta')
 await page.getByRole('button',{name:'取消并清空'}).click()
 await page.getByLabel('场景名称').fill('项目代号')
 await page.locator('.hotword-editor textarea').fill('超'.repeat(65))
 await expect(page.getByText(/单个热词不能超过 64 个字符/)).toBeVisible()
 await expect(page.getByRole('button',{name:'保存词表'})).toBeDisabled()
 await page.locator('.hotword-editor textarea').fill('Sandevistan,Quantum;Project\n量子\nSandevistan,Quantum;Project')
 await page.getByRole('button',{name:'保存词表'}).click()
 expect(submittedTerms).toEqual(['Sandevistan,Quantum;Project','量子'])
 await expect(page.getByText('项目代号')).toBeVisible()
 await expect(page.getByText(/最多只能创建 1 个自定义词表/)).toBeVisible()
 await expect(page.getByRole('button',{name:'保存词表'})).toBeDisabled()
 await page.getByRole('button',{name:'编辑 项目代号'}).click()
 await expect(page.locator('.hotword-editor textarea')).toHaveValue('Sandevistan,Quantum;Project\n量子')
 await expect(page.getByText('逗号和分号会保留在词内')).toBeVisible()
 const editor=page.locator('.hotword-editor')
 const nameBox=await page.getByLabel('场景名称').boundingBox()
 const termsBox=await page.locator('.hotword-editor textarea').boundingBox()
 const editorBox=await editor.boundingBox()
 expect(nameBox!.width).toBeGreaterThan(editorBox!.width*.8)
 expect(termsBox!.width).toBeGreaterThan(editorBox!.width*.8)
 await page.screenshot({path:'/tmp/audio-intel-hotwords-desktop.png',fullPage:false})
 await page.getByRole('button',{name:'取消并清空'}).click()
 await expect(page.getByRole('heading',{name:'新建词表'})).toBeVisible()
 await expect(page.getByLabel('场景名称')).toHaveValue('')
 await expect(page.locator('.hotword-editor textarea')).toHaveValue('')
 await page.setViewportSize({width:390,height:844})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await page.screenshot({path:'/tmp/audio-intel-hotwords-mobile.png',fullPage:false})
 expect(errors).toEqual([])
})

test('partial library failures stay local and keep editors unavailable',async({page})=>{
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{hotword_library:{supported:true,max_lists:100,max_terms_per_list:200,max_selected_lists:8,max_selected_terms:500,max_prompt_chars:8000,max_name_chars:80,max_term_chars:64},speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true},limits:{max_clone_reference_seconds:15},events:{sse:false}}}))
 await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({status:503,json:{detail:'voiceprints unavailable'}}))
 await page.route('**/api/v1/asr/hotword-lists*',route=>route.fulfill({status:503,json:{detail:'hotwords unavailable'}}))
 await page.goto('/#voiceprints')
 await expect(page.getByRole('heading',{name:'声纹库暂不可用'})).toBeVisible()
 await expect(page.getByRole('button',{name:'新建人员'})).toBeDisabled()
 await expect(page.locator('.connection-banner')).toHaveCount(0)
 await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:'热词库'}).click()
 await expect(page.getByRole('heading',{name:'编辑器暂不可用'})).toBeVisible()
 await expect(page.getByLabel('场景名称')).toHaveCount(0)
 await expect(page.locator('.connection-banner')).toHaveCount(0)
})

test('voiceprint metadata synchronizes a read-only system hotword list',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 const now=new Date().toISOString()
 let people:any[]=[]
 let system={id:'hotwords_voiceprint_people',name:'声纹库人名',kind:'system',terms:[] as string[],term_count:0,created_at:now,updated_at:now}
 let submitted:any
 await routeJobList(page,route=>route.fulfill({json:{items:[],count:0,total:0,limit:100,offset:0,has_more:false}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{hotword_library:{supported:true,max_lists:100,max_terms_per_list:200,max_selected_lists:8,max_selected_terms:500,max_prompt_chars:8000,max_name_chars:80,max_term_chars:64},speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true,aligner_languages:['Chinese']},limits:{max_clone_reference_seconds:15},events:{sse:false}}}))
 await page.route('**/api/v1/voiceprints/people',async route=>{
  if(route.request().method()==='POST'){
   submitted=route.request().postDataJSON()
   const person={id:'voice_zhang',name:submitted.name,note:submitted.note,include_in_hotword_library:submitted.include_in_hotword_library,sample_count:0,samples:[],created_at:now,updated_at:now}
   people=[person]
   system={...system,terms:[person.name],term_count:1,updated_at:new Date().toISOString()}
   await route.fulfill({status:201,json:person})
  }else await route.fulfill({json:{items:people}})
 })
 await page.route('**/api/v1/asr/hotword-lists',route=>route.fulfill({json:{items:[system],count:1}}))
 await page.goto('/#voiceprints')
 await page.getByRole('button',{name:'新建人员'}).click()
 const dialog=page.getByRole('dialog',{name:'新建声纹人员'})
 await dialog.getByLabel('名字（必填）').fill('张三')
 await dialog.getByLabel('备注（选填）').fill('研发一部')
 await expect(dialog.getByRole('checkbox',{name:'加入热词库'})).toBeChecked()
 await dialog.getByRole('button',{name:'保存人员'}).click()
 expect(submitted).toEqual({name:'张三',note:'研发一部',include_in_hotword_library:true})
 await expect(page.locator('.samples-panel .person-note')).toHaveText('研发一部')
 await expect(page.getByText('已加入人名热词')).toBeVisible()
 await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:'热词库'}).click()
 const systemList=page.locator('.hotword-list article').filter({hasText:'声纹库人名'})
 await expect(systemList).toBeVisible()
 await expect(systemList.getByText('系统',{exact:true})).toBeVisible()
 await expect(page.getByRole('button',{name:'编辑 声纹库人名'})).toHaveCount(0)
 await expect(page.getByRole('button',{name:'删除 声纹库人名'})).toHaveCount(0)
 await page.getByRole('button',{name:'查看 声纹库人名'}).click()
 await expect(page.getByLabel('场景名称')).toBeDisabled()
 await expect(page.locator('.hotword-editor textarea')).toHaveValue('张三')
 await expect(page.getByText('此词表由系统维护')).toBeVisible()
 await page.setViewportSize({width:390,height:844})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 expect(errors).toEqual([])
})

test('Auto-detected languages outside the aligner list explain segment-only timestamps',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 const now='2026-08-26T12:00:00+00:00'
 const result={text:'مرحبا بالعالم',language:'Arabic',duration:3,timestamp_precision:'segment',speakers:[{id:'Speaker_0',label:'Speaker 0'}],segments:[{id:0,start:0,end:3,speaker:'Speaker_0',speaker_label:'Speaker 0',text:'مرحبا بالعالم',words:[]}],waveform:[.2,.5,.3],artifacts:[{name:'transcript.srt',path:'/tmp/transcript.srt',mime_type:'application/x-subrip',size_bytes:64}]}
 const job={id:'asr-auto-arabic',kind:'asr',state:'succeeded',stage:'completed',progress:1,display_name:'auto-arabic.wav',created_at:now,updated_at:now,request:{language:'Auto',align:true,compute_device:'cpu'},result}
 const languages=['Auto','Chinese','English','Cantonese','French','German','Italian','Japanese','Korean','Portuguese','Russian','Spanish']
 const wav=Buffer.alloc(44+16000);wav.write('RIFF',0);wav.writeUInt32LE(wav.length-8,4);wav.write('WAVE',8);wav.write('fmt ',12);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(8000,24);wav.writeUInt32LE(16000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(16000,40)
 await routeJobList(page,route=>route.fulfill({json:{items:[job]}}))
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',offline:true,bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{},models:[],storage:{data:'/tmp/data'}}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true,languages,default_language:'Auto',aligner_languages:languages.slice(1)},limits:{max_clone_reference_seconds:15}}}))
 await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({json:{items:[]}}))
 await page.route('**/api/v1/jobs/asr-auto-arabic/source',route=>route.fulfill({contentType:'audio/wav',body:wav}))
 await page.goto('/#asr')
 await expect(page.getByRole('note')).toContainText('自动检测为 Arabic')
 await expect(page.getByRole('note')).toContainText('已返回句段级时间戳')
 await expect(page.getByLabel('时间戳')).toHaveValue('word')
 await expect(page.getByText('Arabic · 句级时间戳')).toBeVisible()
 await expect(page.getByRole('button',{name:/查看 .*字词时间戳/})).toHaveCount(0)
 await expect(page.getByTitle('下载 transcript.srt')).toBeVisible()
 await page.getByPlaceholder('搜索转写内容').fill('مرحبا')
 await expect(page.locator('.segments article')).toHaveCount(1)
 await page.screenshot({path:'/tmp/audio-intel-asr-auto-segment-desktop.png',fullPage:false})
 await page.setViewportSize({width:390,height:844})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await page.screenshot({path:'/tmp/audio-intel-asr-auto-segment-mobile.png',fullPage:false})
 expect(errors).toEqual([])
})

test('navigation and mobile layout render without overflow',async({page})=>{
 await page.setViewportSize({width:1440,height:900})
 await page.goto('/#jobs')
 await expect(page.getByRole('heading',{name:'任务记录'})).toBeVisible()
 await expect(page.locator('.filter')).toBeVisible()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1440)
 for(const button of await page.getByRole('navigation',{name:'主导航'}).getByRole('button').all()){
  const box=await button.boundingBox();expect(box!.x).toBeGreaterThanOrEqual(0);expect(box!.x+box!.width).toBeLessThanOrEqual(1440)
 }
 await expect(page.getByRole('link',{name:'打开 API 文档'})).toBeVisible()
 await page.getByRole('button',{name:'系统状态'}).click()
 await expect(page.getByRole('heading',{name:'系统状态',exact:true})).toBeVisible()
 await page.setViewportSize({width:1024,height:820})
 await expect(page.getByRole('link',{name:'打开 API 文档'})).toBeVisible()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1024)
 await page.setViewportSize({width:390,height:844})
 await page.goto('/#tts')
 await expect(page.getByRole('heading',{name:'语音合成'})).toBeVisible()
 const mobileNavigation=page.getByRole('navigation',{name:'主导航'})
 await expect(mobileNavigation.getByRole('button')).toHaveCount(6)
 for(const button of await mobileNavigation.getByRole('button').all()){
  const box=await button.boundingBox();const viewportHeight=await page.evaluate(()=>window.innerHeight);expect(box!.y).toBeGreaterThanOrEqual(0);expect(box!.y+box!.height).toBeLessThanOrEqual(viewportHeight+1)
 }
 await expect(mobileNavigation.locator('.nav-label-short')).toHaveText(['转写','合成','热词','声纹','任务','系统'])
 await page.getByRole('button',{name:'查看单任务加速说明'}).focus()
 const tooltip=page.getByRole('tooltip')
 await expect(tooltip).toBeVisible()
 const tooltipBox=await tooltip.boundingBox()
 expect(tooltipBox?.x).toBeGreaterThanOrEqual(0)
 expect((tooltipBox?.x||0)+(tooltipBox?.width||0)).toBeLessThanOrEqual(390)
 const width=await page.evaluate(()=>document.documentElement.scrollWidth)
 expect(width).toBeLessThanOrEqual(390)
 await page.evaluate(()=>window.scrollTo(0,120))
 await expect.poll(()=>page.locator('.app-shell>header').evaluate(element=>Math.round(element.getBoundingClientRect().top))).toBe(0)
 await mobileNavigation.getByRole('button',{name:'系统状态'}).click()
 await expect(page.getByRole('heading',{name:'系统状态',exact:true})).toBeVisible()
 await expect.poll(()=>page.evaluate(()=>Math.round(window.scrollY))).toBe(0)
 await expect(page.locator('.local-mode .compact-label')).toHaveText('本地可用')
 await page.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight))
 await mobileNavigation.getByRole('button',{name:'系统状态'}).click()
 await expect.poll(()=>page.evaluate(()=>Math.round(window.scrollY))).toBe(0)
 const mobileDocs=page.getByRole('link',{name:'打开 API 文档'})
 await expect(mobileDocs).toBeVisible()
 await expect(mobileDocs.locator('.compact-label')).toHaveText('文档')
 const mobileDocsBox=await mobileDocs.boundingBox()
 expect(mobileDocsBox!.width).toBeGreaterThanOrEqual(44)
 expect(mobileDocsBox!.height).toBeGreaterThanOrEqual(44)
 await page.screenshot({path:'/tmp/audio-intel-after-mobile.png',fullPage:false})
})

test('mobile job pagination stays in flow and preserves the six-item app navigation',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 const createdAt='2026-08-27T10:42:05Z'
 const jobs=Array.from({length:30},(_,index)=>({id:`job-${String(index+1).padStart(3,'0')}`,kind:index%2?'tts':'asr',state:'succeeded',stage:'completed',progress:1,display_name:`任务 ${index+1}`,created_at:createdAt,processing_seconds:index+1,request:{compute_device:'cpu'}}))
 await routeJobList(page,route=>{const url=new URL(route.request().url());const limit=Number(url.searchParams.get('limit')||25);const offset=Number(url.searchParams.get('offset')||0);const items=jobs.slice(offset,offset+limit);return route.fulfill({json:{items,count:items.length,total:jobs.length,limit,offset,has_more:offset+limit<jobs.length}})})
 await page.setViewportSize({width:390,height:844})
 await page.goto('/#jobs')
 await expect(page.getByText('任务 1',{exact:true})).toBeVisible()
 await expect(page.locator('.created').first()).not.toContainText(/AM|PM/)
 const pagination=page.getByRole('navigation',{name:'任务分页'})
 await pagination.scrollIntoViewIfNeeded()
 await pagination.getByRole('button',{name:'下一页'}).click()
 await expect(page.getByText('任务 26',{exact:true})).toBeVisible()
 await expect(pagination).toContainText('第 2 / 2 页')
 await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:'系统状态'}).click()
 await expect(page.getByRole('heading',{name:'系统状态',exact:true})).toBeVisible()
 expect(errors).toEqual([])
})

test('task history only reports jobs added after the live baseline is ready',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 const createdAt='2026-08-29T04:00:00Z'
 const historical={id:'history-baseline',kind:'tts',state:'failed',stage:'failed',progress:1,display_name:'已有历史任务',created_at:createdAt,updated_at:createdAt,request:{compute_device:'cpu'}}
 const updatedHistorical={...historical,state:'cancelled',stage:'cancelled',updated_at:'2026-08-29T04:01:00Z'}
 const newJob={id:'actually-new-job',kind:'asr',state:'queued',stage:'queued',progress:0,display_name:'真正新增任务',created_at:'2026-08-29T04:02:00Z',updated_at:'2026-08-29T04:02:00Z',request:{compute_device:'cpu'}}
 let globalCalls=0
 let initialGlobalReady=false
 await page.route('**/api/v1/auth/session',route=>route.fulfill({json:{required:false,authenticated:true}}))
 await routeJobList(page,async route=>{
  const url=new URL(route.request().url())
  const globalRequest=!url.searchParams.has('limit')
  if(globalRequest){
   globalCalls+=1
   if(globalCalls===1)await new Promise(resolve=>setTimeout(resolve,250))
   const items=globalCalls>=3?[newJob,updatedHistorical]:globalCalls===2?[updatedHistorical]:[historical]
   initialGlobalReady=true
   return route.fulfill({json:{items,count:items.length,total:items.length,limit:100,offset:0,has_more:false}})
  }
  const items=globalCalls>=3?[newJob,updatedHistorical]:globalCalls===2?[updatedHistorical]:[historical]
  return route.fulfill({json:{items,count:items.length,total:items.length,limit:25,offset:0,has_more:false}})
 })
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',offline:true,bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{},models:[],storage:{data:'/tmp/data'}}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true},limits:{max_clone_reference_seconds:15},events:{sse:false}}}))
 await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({json:{items:[]}}))
 await page.route('**/api/v1/asr/hotword-lists',route=>route.fulfill({json:{items:[],count:0}}))

 await page.goto('/#jobs')
 await expect(page.getByText('已有历史任务',{exact:true})).toBeVisible()
 await expect.poll(()=>initialGlobalReady).toBe(true)
 await expect(page.getByRole('region',{name:'新任务提示'})).toHaveCount(0)

 await expect.poll(()=>globalCalls,{timeout:7000}).toBeGreaterThanOrEqual(2)
 await expect(page.locator('.table-row').filter({hasText:'已有历史任务'}).getByText('已取消',{exact:true})).toBeVisible()
 await expect(page.getByRole('region',{name:'新任务提示'})).toHaveCount(0)

 await expect.poll(()=>globalCalls,{timeout:7000}).toBeGreaterThanOrEqual(3)
 const banner=page.getByRole('region',{name:'新任务提示'})
 await expect(banner).toBeVisible()
 await expect(page.getByText('真正新增任务',{exact:true})).toHaveCount(0)
 await page.screenshot({path:'/tmp/audio-intel-new-job-banner-desktop.png',fullPage:false})

 await page.setViewportSize({width:390,height:844})
 await expect(banner).toBeVisible()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await banner.getByRole('button',{name:'返回第一页查看'}).click()
 await expect(page.getByText('真正新增任务',{exact:true})).toBeVisible()
 await expect(banner).toHaveCount(0)
 await page.screenshot({path:'/tmp/audio-intel-new-job-banner-mobile.png',fullPage:false})
 expect(errors).toEqual([])
})

test('desktop submit actions remain visible and voiceprint model controls reflow cleanly',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 const now=new Date().toISOString()
 await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({json:{items:[{id:'voice_layout',name:'布局测试',sample_count:0,samples:[],created_at:now,updated_at:now}]}}))
 const expectInsideMain=async(name:string)=>{const button=page.getByRole('button',{name});await expect(button).toBeVisible();expect(await button.evaluate(element=>{const box=element.getBoundingClientRect();const main=element.closest('main')!.getBoundingClientRect();return box.top>=main.top&&box.bottom<=main.bottom})).toBe(true)}
 const expectNoControlOverlap=async(scope:string)=>{const layout=await page.locator(scope).evaluate(element=>{const action=element.querySelector('.submission-actions')!.getBoundingClientRect();const controls=[...element.querySelectorAll('select,.acceleration-control')].map(control=>{const box=control.getBoundingClientRect();return {label:control.getAttribute('aria-label')||control.className,top:box.top,bottom:box.bottom}});return {action:{top:action.top,bottom:action.bottom},controls}});expect(layout.controls.filter(control=>control.bottom>layout.action.top&&control.top<layout.action.bottom),JSON.stringify(layout)).toEqual([])}
 await page.setViewportSize({width:1440,height:900})
 await page.goto('/#asr')
 await expectInsideMain('开始转写')
 await expectNoControlOverlap('.control-panel')
 await page.goto('/#tts')
 await expectInsideMain('生成语音')
 await expectNoControlOverlap('.tts-editor')
 await page.setViewportSize({width:1024,height:820})
 await expectInsideMain('生成语音')
 await page.goto('/#voiceprints')
 const controls=[page.getByLabel('声纹入库 ASR 模型'),page.getByLabel('声纹样本语言'),page.getByLabel('声纹入库计算设备'),page.getByRole('button',{name:'自动转写并入库'})]
 const rows=await Promise.all(controls.map(control=>control.evaluate(element=>Math.round(element.getBoundingClientRect().top))))
 expect(new Set(rows).size).toBeLessThanOrEqual(2)
 await page.setViewportSize({width:390,height:844})
 await page.goto('/#tts')
 await expect(page.locator('.submission-actions')).toHaveCSS('position','static')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 expect(errors).toEqual([])
})

test('system worker state and heartbeat use Chinese local presentation',async({page})=>{
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',version:'test',offline:true,bind:'127.0.0.1:20810',services:['asr','tts'],workers:[{id:'asr-worker',kind:'asr',state:'idle',heartbeat_at:'2026-08-27T10:42:05Z'}],hardware:{cpu_percent:25,memory_used:8589934592,memory_total:17179869184,disk_used:10737418240,disk_total:42949672960},models:[{name:'Qwen3-ASR-0.6B',device:'CPU',installed:false,state:'missing',revision:'r1',missing_files:['model.safetensors'],path:'/models/asr'},{name:'Qwen3-TTS-0.6B',device:'CPU',installed:true,state:'installed',revision:'r2',missing_files:[],path:'/models/tts'}],storage:{data:'/tmp/data'}}}))
 await page.goto('/#system')
 const worker=page.locator('.worker-list')
 await expect(worker).toContainText('空闲')
 await expect(worker).not.toContainText('2026-08-27T10:42:05Z')
 await expect(page.getByRole('progressbar',{name:'项目磁盘'})).toHaveAttribute('aria-valuenow','25')
 await expect(page.locator('.resource-card').filter({hasText:'项目磁盘'})).toContainText('10 / 40 GB')
 await expect(page.locator('.model-group').filter({hasText:'ASR 与公共组件'})).toHaveAttribute('open','')
 await expect(page.locator('.model-group').filter({hasText:'TTS'})).not.toHaveAttribute('open','')
 const docsLink=page.getByRole('link',{name:'打开 API 文档',exact:true})
 await expect(docsLink).toHaveCount(1)
 await expect(docsLink).toHaveAttribute('href','/docs')
 await expect(docsLink).toHaveAttribute('target','_blank')
})

test('ASR and TTS preferences persist independently and reset per page',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 await page.route('**/api/v1/system',async route=>{
  const response=await route.fetch()
  const body=await response.json()
  body.hardware={...body.hardware,gpu:{...body.hardware?.gpu,available:true}}
  await route.fulfill({response,json:body})
 })
 await page.route('**/api/v1/capabilities',async route=>{
  const response=await route.fetch()
  const body=await response.json()
  const useGpuByDefault=(devices:Array<{id:string;available:boolean;default:boolean}>)=>{
   for(const device of devices){device.available=true;device.default=device.id==='gpu'}
  }
  useGpuByDefault(body.asr.compute_devices)
  useGpuByDefault(body.asr.models.find((model:{default:boolean})=>model.default).compute_devices)
  useGpuByDefault(body.tts.compute_devices)
  useGpuByDefault(body.tts.model_capabilities.find((model:{default:boolean})=>model.default).compute_devices)
  await route.fulfill({response,json:body})
 })
 await page.goto('/#asr')
 await page.evaluate(()=>{localStorage.clear();sessionStorage.clear();localStorage.setItem('audio-intel:asr-preferences:v1','{broken');localStorage.setItem('audio-intel:tts-preferences:v1','{broken')})
 await page.reload()
 const asrDevice=page.getByLabel('ASR 计算设备')
 const asrAcceleration=page.getByRole('checkbox',{name:'单任务加速',exact:true})
 await expect(asrDevice).toHaveValue('gpu')
 await expect(asrAcceleration).toBeChecked()
 await page.getByRole('button',{name:'查看单任务加速说明'}).hover()
 await expect(page.getByRole('tooltip')).toContainText('不改变模型、精度、分块或说话人算法')
 await expect(page.getByLabel('识别语言').locator('option')).toHaveCount(12)
 await page.getByLabel('识别语言').selectOption('French')
 await page.getByLabel('说话人数').selectOption('2')
 await page.getByLabel('时间戳').selectOption('segment')
 await page.getByRole('checkbox',{name:'允许使用声纹库识别人员'}).uncheck()
 await asrAcceleration.uncheck()
 await asrDevice.selectOption('cpu')
 await page.locator('nav').getByRole('button',{name:/语音合成/}).click()
 const ttsDevice=page.getByLabel('TTS 计算设备')
 const ttsAcceleration=page.getByRole('checkbox',{name:'单任务加速',exact:true})
 await expect(ttsDevice).toHaveValue('gpu')
 await expect(ttsAcceleration).toBeChecked()
 await expect(page.getByText('GPU · BF16 · SDPA · AUTO BATCH')).toBeVisible()
 await expect(page.getByLabel('输出语种')).toHaveValue('Auto')
 await page.getByLabel('输出语种').selectOption('English')
 await page.getByLabel('音色',{exact:true}).selectOption('Serena')
 await page.locator('.text-editor textarea').fill('这段文本只应保留在当前会话。')
 await ttsAcceleration.uncheck()
 await ttsDevice.selectOption('cpu')
 await expect(page.getByText('CPU · FP32 · SDPA · BATCH 1')).toBeVisible()
 await page.locator('nav').getByRole('button',{name:/转写工作台/}).click()
 await expect(asrDevice).toHaveValue('cpu')
 await expect(asrAcceleration).not.toBeChecked()
 await expect(page.getByLabel('识别语言')).toHaveValue('French')
 await page.reload()
 await expect(page.getByLabel('ASR 计算设备')).toHaveValue('cpu')
 await expect(page.getByRole('checkbox',{name:'单任务加速',exact:true})).not.toBeChecked()
 await page.getByRole('button',{name:'恢复默认配置'}).click()
 await expect(page.getByLabel('ASR 计算设备')).toHaveValue('gpu')
 await expect(page.getByRole('checkbox',{name:'单任务加速',exact:true})).toBeChecked()
 await expect(page.locator('.control-panel .notice')).toContainText('已恢复 ASR 默认配置')
 await page.locator('nav').getByRole('button',{name:/语音合成/}).click()
 await expect(ttsDevice).toHaveValue('cpu')
 await expect(ttsAcceleration).not.toBeChecked()
 await expect(page.locator('.text-editor textarea')).toHaveValue('这段文本只应保留在当前会话。')
 await page.getByRole('button',{name:'恢复默认配置'}).click()
 await expect(ttsDevice).toHaveValue('gpu')
 await expect(ttsAcceleration).toBeChecked()
 await expect(page.getByLabel('输出语种')).toHaveValue('Auto')
 await expect(page.getByLabel('音色',{exact:true})).toHaveValue('Vivian')
 await expect(page.locator('.text-editor textarea')).toHaveValue('这段文本只应保留在当前会话。')
 const stored=await page.evaluate(()=>({asr:JSON.parse(localStorage.getItem('audio-intel:asr-preferences:v2')||'{}'),tts:JSON.parse(localStorage.getItem('audio-intel:tts-preferences:v2')||'{}'),localContent:localStorage.getItem('audio-intel:tts-content:v2'),sessionContent:sessionStorage.getItem('audio-intel:tts-content:v2')}))
 expect(stored.asr).toMatchObject({computeDevice:'gpu',accelerateSingleTask:true})
 expect(stored.tts).toMatchObject({computeDevice:'gpu',accelerateSingleTask:true})
 expect(stored.localContent).toBeNull()
 expect(stored.sessionContent).toContain('这段文本只应保留在当前会话')
 await page.screenshot({path:'/tmp/audio-intel-preferences-desktop.png',fullPage:false})
 await page.setViewportSize({width:390,height:844})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await expect(page.getByRole('button',{name:'恢复默认配置'})).toBeVisible()
 await page.screenshot({path:'/tmp/audio-intel-preferences-mobile.png',fullPage:false})
 expect(errors).toEqual([])
})

test('shell status reflects system checks, bind changes and recovery',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 let state:'pending'|'ready'|'warning'|'failed'='pending'
 let release:()=>void=()=>{}
 const gate=new Promise<void>(resolve=>{release=resolve})
 await page.route('**/api/v1/system',async route=>{
  if(state==='pending')await gate
  if(state==='failed')return route.fulfill({status:503,json:{detail:'system unavailable'}})
  const offline=state!=='warning'
  return route.fulfill({json:{status:'ok',offline,bind:'localhost:21999',services:['asr','tts'],workers:[],hardware:{},models:[],storage:offline?{data:'/srv/audio-intel/data'}:{}}})
 })
 await page.goto('/#system')
 await expect(page.locator('.local-mode')).toContainText('OFFLINE_MODE // CHECKING')
 state='ready';release()
 await expect(page.locator('.local-mode')).toContainText('OFFLINE_MODE // ACTIVE')
 await expect(page.locator('footer .local-copy')).toContainText('DATA_LOCAL')
 await expect(page.locator('footer .local-copy')).toContainText('READY')
 await expect(page.locator('footer .local-copy')).toContainText('数据本地存储')
 await expect(page.locator('footer .bind')).toContainText('localhost:21999')
 await expect(page.locator('footer .shell-status').filter({hasText:'ASR_ENGINE'})).toContainText('READY')
 await expect(page.locator('.system-health')).toHaveCount(0)
 state='warning'
 await expect(page.locator('.local-mode')).toContainText('OFFLINE_MODE // INACTIVE',{timeout:5000})
 await expect(page.locator('footer .local-copy')).toContainText('UNVERIFIED')
 state='failed'
 await expect(page.locator('.local-mode')).toContainText('LOCAL_CORE // DISCONNECTED',{timeout:5000})
 await expect(page.locator('footer .bind')).toContainText('UNKNOWN')
 await expect(page.locator('footer .local-copy')).toContainText('UNKNOWN')
 await expect(page.locator('footer .shell-status').filter({hasText:'ASR_ENGINE'})).toContainText('UNKNOWN')
 state='ready'
 await expect(page.locator('.local-mode')).toContainText('OFFLINE_MODE // ACTIVE',{timeout:5000})
 await page.screenshot({path:'/tmp/audio-intel-status-desktop.png',fullPage:false})
 await page.setViewportSize({width:390,height:844})
 await expect(page.locator('.local-mode .compact-label')).toHaveText('本地可用')
 await expect(page.locator('footer')).not.toBeVisible()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await page.screenshot({path:'/tmp/audio-intel-status-mobile.png',fullPage:false})
 expect(errors.filter(message=>!message.includes('503 (Service Unavailable)'))).toEqual([])
})

test('voiceprint samples support upload and previewed microphone recording',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 await page.clock.install()
 await page.addInitScript(()=>{
  const scope=window as typeof window&{__micStops:number}
  scope.__micStops=0
  const makeStream=()=>({getTracks:()=>[{stop:()=>{scope.__micStops+=1}}]})
  Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>makeStream()}})
  class FakeMediaRecorder{
   static isTypeSupported(type:string){return type==='audio/webm;codecs=opus'}
   state:RecordingState='inactive'
   mimeType='audio/webm;codecs=opus'
   ondataavailable:((event:BlobEvent)=>void)|null=null
   onstop:((event:Event)=>void)|null=null
   onerror:((event:Event)=>void)|null=null
   constructor(_stream:MediaStream,_options?:MediaRecorderOptions){}
   start(){this.state='recording'}
   stop(){
    if(this.state==='inactive')return
    this.state='inactive'
    queueMicrotask(()=>{
     this.ondataavailable?.({data:new Blob(['browser-microphone-sample'],{type:this.mimeType})} as BlobEvent)
     this.onstop?.(new Event('stop'))
    })
   }
   pause(){this.state='paused'}
   resume(){this.state='recording'}
   requestData(){}
   addEventListener(){}
   removeEventListener(){}
   dispatchEvent(){return true}
   stream={} as MediaStream
   audioBitsPerSecond=0
   videoBitsPerSecond=0
  }
  Object.defineProperty(window,'MediaRecorder',{configurable:true,value:FakeMediaRecorder})
 })
 const now='2026-08-26T12:00:00+00:00'
 const people=[
  {id:'voice_recording',name:'录音测试人员',sample_count:0,created_at:now,updated_at:now,samples:[]},
  {id:'voice_other',name:'另一个人',sample_count:0,created_at:now,updated_at:now,samples:[]},
 ]
 const submitted:string[]=[]
 let submission=0
 await routeJobList(page,route=>route.fulfill({json:{items:[]}}))
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',offline:true,bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{},models:[],storage:{data:'/tmp/data'}}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true},limits:{max_clone_reference_seconds:15}}}))
 await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({json:{items:people}}))
 await page.route('**/api/v1/tts/voices',route=>route.fulfill({json:{items:[],preset_speakers:['Vivian']}}))
 await page.route('**/api/v1/voiceprints/people/voice_recording/samples/upload',async route=>{
  expect(route.request().headers()['idempotency-key']).toMatch(/^[0-9a-f-]{36}$/)
  submitted.push((await route.request().postDataBuffer())?.toString()||'')
  submission+=1
  await route.fulfill({status:202,json:{sample:{id:`sample_${submission}`,person_id:'voice_recording',state:'pending',language:'Chinese',words:[],created_at:now,updated_at:now,tts_eligible:false,embedding_status:'pending'},job:{id:`voiceprint-job-${submission}`,kind:'asr',state:'queued',stage:'queued',progress:0,display_name:'声纹样本入库 · 录音测试人员',created_at:now,request:{compute_device:'cpu'}}}})
 })
 await page.goto('/#voiceprints')
 await expect(page.getByRole('heading',{name:'声纹库',exact:true})).toBeVisible()
 await expect(page.getByLabel('声纹样本语言').locator('option')).toHaveCount(12)
 await page.getByLabel('声纹样本语言').selectOption('Spanish')
 await page.getByLabel('声纹入库计算设备').selectOption('cpu')
 const fileInput=page.locator('.sample-input-panel input[type="file"]')
 await fileInput.setInputFiles({name:'uploaded-sample.wav',mimeType:'audio/wav',buffer:Buffer.from('RIFF-upload')})
 await page.getByRole('button',{name:'自动转写并入库'}).click()
 await expect(page.getByRole('status')).toContainText('已创建“声纹样本入库”ASR 任务')
 expect(submitted[0]).toContain('uploaded-sample.wav')
 await page.getByRole('tab',{name:'麦克风录音'}).click()
 await page.getByRole('button',{name:'开始录音'}).click()
 await expect(page.getByText('正在录音',{exact:true})).toBeVisible()
 await page.clock.fastForward(4500)
 await page.getByRole('button',{name:'停止并试听'}).click()
 await expect(page.getByText('录音完成')).toBeVisible()
 await expect(page.locator('.recording-preview audio')).toBeVisible()
 await expect(page.getByText(/录音不足 5 秒/)).toBeVisible()
 await expect(page.getByRole('button',{name:'确认转写并入库'})).toBeEnabled()
 await page.getByRole('button',{name:'重新录制'}).click()
 await page.clock.fastForward(30_000)
 await expect(page.getByText('录音完成')).toBeVisible()
 await expect(page.getByText(/录音不足 5 秒/)).toHaveCount(0)
 await expect(page.locator('.recording-preview')).toContainText('00:00:30')
 await page.screenshot({path:'/tmp/audio-intel-voiceprint-recorder-desktop.png',fullPage:false})
 await page.getByRole('button',{name:'确认转写并入库'}).click()
 await expect(page.getByRole('status')).toContainText('已创建“声纹样本入库”ASR 任务')
 expect(submitted[1]).toContain('voiceprint-recording-')
 expect(submitted[1]).toContain('.webm')
 expect(submitted[1]).toContain('audio/webm;codecs=opus')
 expect(submitted[1]).toContain('Spanish')
 expect(submitted[1]).toContain('cpu')
 await page.evaluate(()=>Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>{throw new DOMException('denied','NotAllowedError')}}}))
 await page.getByRole('button',{name:'开始录音'}).click()
 await expect(page.getByRole('alert')).toContainText('麦克风权限被拒绝')
 await page.getByRole('tab',{name:'上传文件'}).click()
 await expect(page.getByRole('button',{name:'选择单人语音样本'})).toBeVisible()
 await page.evaluate(()=>{
  const scope=window as typeof window&{__micStops:number}
  Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>({getTracks:()=>[{stop:()=>{scope.__micStops+=1}}]})}})
 })
 await page.getByRole('tab',{name:'麦克风录音'}).click()
 await page.getByRole('button',{name:'开始录音'}).click()
 await page.clock.fastForward(1000)
 await page.getByRole('button',{name:'停止并试听'}).click()
 await expect(page.getByText('录音完成')).toBeVisible()
 await page.getByRole('button',{name:/另一个人/}).click()
 await expect(page.getByText('录音完成')).toHaveCount(0)
 await expect(page.getByRole('button',{name:'确认转写并入库'})).toBeDisabled()
 await page.getByRole('button',{name:/录音测试人员/}).click()
 await page.getByRole('button',{name:'开始录音'}).click()
 await page.locator('nav').getByRole('button',{name:/任务记录/}).click()
 expect(await page.evaluate(()=>(window as typeof window&{__micStops:number}).__micStops)).toBeGreaterThan(0)
 await page.setViewportSize({width:390,height:844})
 await page.goto('/#voiceprints')
 await page.getByRole('tab',{name:'麦克风录音'}).click()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await page.screenshot({path:'/tmp/audio-intel-voiceprint-recorder-mobile.png',fullPage:false})
 await page.evaluate(()=>{
  Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:undefined})
  Object.defineProperty(window,'MediaRecorder',{configurable:true,value:undefined})
 })
 await page.locator('nav').getByRole('button',{name:/任务记录/}).click()
 await page.locator('nav').getByRole('button',{name:/声纹库/}).click()
 await page.getByRole('tab',{name:'麦克风录音'}).click()
 await expect(page.getByRole('note')).toContainText('当前浏览器不支持麦克风录音')
 await expect(page.getByRole('tab',{name:'上传文件'})).toBeEnabled()
 expect(errors).toEqual([])
})

test('API key login and logout use an ephemeral browser session',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 let authenticated=false
 let submittedAuthorization=''
 await page.route('**/api/v1/auth/session',async route=>{
  if(route.request().method()==='GET')return route.fulfill({json:{required:true,authenticated}})
  if(route.request().method()==='POST'){submittedAuthorization=route.request().headers().authorization||'';authenticated=true;return route.fulfill({status:204})}
  authenticated=false
  return route.fulfill({status:204})
 })
 await page.goto('/#system')
 await expect(page.getByRole('dialog',{name:'访问验证'})).toBeVisible()
 const keyInput=page.getByPlaceholder('输入 AUDIO_INTEL_API_KEY')
 await expect(keyInput).toBeFocused()
 await page.keyboard.press('Escape')
 await expect(page.getByRole('dialog',{name:'访问验证'})).toBeVisible()
 await page.screenshot({path:'/tmp/audio-intel-auth-login.png',fullPage:false})
 await keyInput.fill('browser-secret')
 await keyInput.press('Tab')
 await expect(page.getByRole('button',{name:'进入工作台'})).toBeFocused()
 await page.keyboard.press('Tab')
 await expect(page.getByRole('button',{name:'先安装 HTTPS 根证书'})).toBeFocused()
 await page.keyboard.press('Tab')
 await expect(keyInput).toBeFocused()
 await page.getByRole('button',{name:'进入工作台'}).click()
 await expect(page.getByRole('dialog',{name:'访问验证'})).toHaveCount(0)
 await expect(page.locator('.model-list>div')).toHaveCount(10)
 await expect(page.locator('nav').getByRole('button',{name:/系统状态/})).toBeFocused()
 await page.screenshot({path:'/tmp/audio-intel-authenticated-system.png',fullPage:false})
 expect(submittedAuthorization).toBe('Bearer browser-secret')
 expect(await page.evaluate(()=>({stored:sessionStorage.getItem('audio-intel:key'),url:location.href}))).toEqual({stored:null,url:expect.not.stringContaining('browser-secret')})
 await page.setViewportSize({width:390,height:844})
 await expect(page.locator('.local-mode')).toBeVisible()
 await expect(page.getByRole('link',{name:'打开 API 文档'})).toBeVisible()
 await expect(page.getByRole('button',{name:'退出本地会话'})).toBeVisible()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await page.getByRole('button',{name:'退出本地会话'}).click()
 await expect(page.getByRole('dialog',{name:'访问验证'})).toBeVisible()
 expect(errors).toEqual([])
})

test('protected deep links wait for login before loading jobs and TTS voices',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 let authenticated=false
 let protectedBeforeLogin=0
 let jobsCalls=0
 let voicesCalls=0
 await page.route('**/api/v1/auth/session',async route=>{
  if(route.request().method()==='GET')return route.fulfill({json:{required:true,authenticated}})
  authenticated=true
  return route.fulfill({status:204})
 })
 await routeJobList(page,route=>{if(!authenticated)protectedBeforeLogin+=1;jobsCalls+=1;return route.fulfill({json:{items:[],count:0,total:0,limit:25,offset:0,has_more:false}})})
 await page.route('**/api/v1/tts/voices',route=>{if(!authenticated)protectedBeforeLogin+=1;voicesCalls+=1;return route.fulfill({json:{items:[],preset_speakers:['Vivian']}})})
 await page.goto('/#tts')
 await expect(page.getByRole('dialog',{name:'访问验证'})).toBeVisible()
 await page.waitForTimeout(150)
 expect(protectedBeforeLogin).toBe(0)
 expect(jobsCalls).toBe(0)
 expect(voicesCalls).toBe(0)
 await page.getByPlaceholder('输入 AUDIO_INTEL_API_KEY').fill('browser-secret')
 await page.getByRole('button',{name:'进入工作台'}).click()
 await expect(page.getByRole('heading',{name:'语音合成'})).toBeVisible()
 await expect.poll(()=>jobsCalls).toBeGreaterThan(0)
 await expect.poll(()=>voicesCalls).toBe(1)
 await expect(page.getByRole('alert')).toHaveCount(0)
 expect(errors).toEqual([])
})

test('task duration, single/multi/select-all and partial batch deletion are interactive',async({page})=>{
 const now=new Date().toISOString()
 const jobs=[
  {id:'asr-completed',kind:'asr',state:'succeeded',stage:'completed',progress:1,display_name:'已完成转写',created_at:now,updated_at:now,started_at:now,finished_at:now,processing_seconds:3661,processing_as_of:now,attempts:1,compute_device:'gpu',compute_device_name:'NVIDIA RTX A1000 Laptop GPU',request:{compute_device:'gpu',compute_device_name:'NVIDIA RTX A1000 Laptop GPU'},result:{compute_device:'gpu',compute_device_name:'NVIDIA RTX A1000 Laptop GPU'}},
  {id:'asr-queued',kind:'asr',state:'queued',stage:'queued',progress:0,display_name:'排队转写',created_at:now,updated_at:now,processing_seconds:0,processing_as_of:now,attempts:0,request:{compute_device:'cpu'}},
  {id:'tts-failed',kind:'tts',state:'failed',stage:'failed',progress:.4,display_name:'失败合成',created_at:now,updated_at:now,started_at:now,finished_at:now,processing_seconds:61,processing_as_of:now,attempts:2,error_code:'OutOfMemoryError',error_message:'CUDA out of memory while allocating 512 MiB',request:{compute_device:'cpu'}},
  {id:'tts-running',kind:'tts',state:'running',stage:'synthesizing',progress:.537,display_name:'运行中合成',created_at:now,updated_at:now,started_at:now,processing_seconds:5,processing_as_of:now,attempts:1,request:{compute_device:'gpu'},progress_detail:{stage_code:'synthesis',stage_progress:.537,basis:'estimated',current:1,total:3,unit:'text_chunk',activity:{sequence:2,current:41,total:90,unit:'codec_frame',basis:'estimated',updated_at:now}}},
 ]
 let submitted:string[]=[]
 await routeJobList(page,route=>route.request().method()==='GET'?route.fulfill({json:{items:jobs,count:jobs.length,total:jobs.length,limit:25,offset:0,has_more:false}}):route.fallback())
 await page.route('**/api/v1/jobs/batch-delete',async route=>{
  submitted=(await route.request().postDataJSON()).job_ids
  await route.fulfill({json:{requested_count:3,deleted_count:2,failed_count:1,reclaimed_bytes:10485760,database_reclaimed_bytes:4096,database_compacted:true,maintenance_error:null,deleted:[{id:'asr-completed',reclaimed_bytes:5242880},{id:'asr-queued',reclaimed_bytes:5242880}],failed:[{id:'tts-failed',code:'purge_failed',message:'模拟文件占用'}]}})
 })
 await page.goto('/#jobs')
 await expect(page.locator('.job-id').filter({hasText:'任务 ID：asr-complete…'})).toBeVisible()
 await expect(page.locator('.job-meta').filter({hasText:'NVIDIA RTX A1000 Laptop GPU'})).toBeVisible()
 await expect(page.locator('.elapsed').filter({hasText:'01:01:01'})).toBeVisible()
 await expect(page.locator('.elapsed').filter({hasText:'未开始'})).toBeVisible()
 await expect(page.getByLabel('选择任务 运行中合成')).toBeDisabled()
 await expect(page.getByText('54% 估算')).toBeVisible()
 await expect(page.getByText('当前批次 41/90 codec 帧（总量估算）')).toBeVisible()
 const failedRow=page.locator('.table-row').filter({hasText:'失败合成'})
 await expect(failedRow.getByText('显存或内存不足')).toBeVisible()
 await expect(failedRow.getByRole('cell')).toHaveCount(7)
 await expect(failedRow.getByRole('rowheader')).toHaveCount(1)
 const failureButton=page.getByRole('button',{name:'查看失败详情 失败合成'})
 await failureButton.click()
 const failureDialog=page.getByRole('dialog',{name:'任务失败详情'})
 await expect(failureDialog).toContainText('OutOfMemoryError')
 await expect(failureDialog.getByRole('textbox',{name:'技术详情'})).toHaveValue('CUDA out of memory while allocating 512 MiB')
 await page.keyboard.press('Escape')
 await expect(failureDialog).toHaveCount(0)
 await expect(failureButton).toBeFocused()
 const header=page.getByLabel('全选当前页可操作任务')
 await page.getByLabel('选择任务 已完成转写').check()
 expect(await header.evaluate((element:HTMLInputElement)=>element.indeterminate)).toBe(true)
 await page.getByLabel('选择任务 排队转写').check()
 await expect(page.getByText('2 个任务已选择')).toBeVisible()
 await page.locator('.filter').getByRole('button',{name:'TTS'}).click()
 await expect(page.getByLabel('批量任务操作')).toHaveCount(0)
 await page.locator('.filter').getByRole('button',{name:'全部'}).click()
 await header.check()
 await expect(page.getByText('3 个任务已选择')).toBeVisible()
 await page.getByRole('button',{name:'永久删除所选任务'}).click()
 await expect(page.getByRole('dialog',{name:'永久删除任务'})).toBeVisible()
 await page.getByRole('dialog',{name:'永久删除任务'}).getByRole('button',{name:'永久删除',exact:true}).click()
 await expect(page.getByRole('status')).toContainText('释放 10.0 MB')
 await expect(page.getByRole('alert')).toContainText('模拟文件占用')
 await expect(page.getByText('1 个任务已选择')).toBeVisible()
 expect(submitted.sort()).toEqual(['asr-completed','asr-queued','tts-failed'])
 await page.screenshot({path:'/tmp/audio-intel-jobs-batch-desktop.png',fullPage:false})
 await page.setViewportSize({width:390,height:844})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await expect(page.getByLabel('运行中合成 任务进度 54%')).toBeVisible()
 await expect(page.getByText('当前批次 41/90 codec 帧（总量估算）')).toBeVisible()
 const cancelTarget=await page.getByLabel('取消任务 运行中合成').boundingBox()
 expect(cancelTarget?.width).toBeGreaterThanOrEqual(44)
 expect(cancelTarget?.height).toBeGreaterThanOrEqual(44)
 await page.screenshot({path:'/tmp/audio-intel-jobs-batch-mobile.png',fullPage:false})
})

test('running task shows safe cancellation and becomes deletable after shutdown',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 const now=new Date().toISOString()
 const running={id:'tts-cancel',kind:'tts',state:'running',stage:'synthesizing_1_of_1',progress:.45,display_name:'待停止合成',created_at:now,updated_at:now,started_at:now,processing_seconds:8,processing_as_of:now,attempts:1,request:{compute_device:'gpu'}}
 const cancelling={...running,stage:'cancelling',updated_at:new Date(Date.now()+1000).toISOString()}
 const cancelled={...cancelling,state:'cancelled',stage:'cancelled',finished_at:new Date(Date.now()+2000).toISOString(),updated_at:new Date(Date.now()+2000).toISOString()}
 let statusRequests=0
 await page.route('**/api/v1/jobs/tts-cancel/cancel',route=>route.fulfill({json:cancelling}))
 await page.route('**/api/v1/jobs/tts-cancel',route=>{statusRequests+=1;return route.fulfill({json:statusRequests<2?cancelling:cancelled})})
 await routeJobList(page,route=>route.fulfill({json:{items:[running],count:1,total:1,limit:25,offset:0,has_more:false}}))
 await page.goto('/#jobs')
 const row=page.locator('.table-row').filter({hasText:'待停止合成'})
 await row.getByTitle('取消任务').click()
 await expect(row.getByLabel('正在安全停止 待停止合成')).toBeDisabled()
 await expect(row).toContainText('正在安全停止')
 await expect(page.getByRole('status')).toContainText('现在可以重试或永久删除')
 await expect(row.getByTitle('永久删除')).toBeVisible()
 await page.screenshot({path:'/tmp/audio-intel-cancelled-desktop.png',fullPage:false})
 await page.setViewportSize({width:390,height:844})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await expect(row.getByTitle('永久删除')).toBeVisible()
 await page.screenshot({path:'/tmp/audio-intel-cancelled-mobile.png',fullPage:false})
 expect(errors).toEqual([])
})

test('first TTS submission appears immediately and survives a stale poll',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 const now='2026-08-25T12:00:00+00:00'
 const queued={id:'1234567890abcdef1234567890abcdef',kind:'tts',state:'queued',stage:'queued',progress:0,display_name:'首次合成即时入列',created_at:now,updated_at:now,processing_seconds:0,processing_as_of:now,attempts:0,compute_device:'cpu',compute_device_name:'CPU',request:{compute_device:'cpu',compute_device_name:'CPU'},queue:{scope:'tts',position:2,depth:4,capacity:5,waiting_for:'worker'},estimate:{state:'ready',confidence:'low',sample_count:8,remaining_seconds:{lower:30,upper:90}}}
 const running={...queued,state:'running',stage:'synthesizing',progress:.25,started_at:now,attempts:1}
 let jobsRequests=0
 let submitted=false
 let markStaleStarted:()=>void=()=>{}
 let releaseStale:()=>void=()=>{}
 const staleStarted=new Promise<void>(resolve=>{markStaleStarted=resolve})
 const staleGate=new Promise<void>(resolve=>{releaseStale=resolve})
 await routeJobList(page,async route=>{
  jobsRequests+=1
  if(jobsRequests===1)return route.fulfill({json:{items:[]}})
  if(!submitted){markStaleStarted();await staleGate;return route.fulfill({json:{items:[]}})}
  return route.fulfill({json:{items:[running]}})
 })
 await page.route('**/api/v1/tts/jobs',route=>{expect(route.request().headers()['idempotency-key']).toMatch(/^[0-9a-f-]{36}$/);submitted=true;return route.fulfill({status:202,json:queued})})
 await page.route('**/api/v1/tts/voices',route=>route.fulfill({json:{items:[],preset_speakers:['Vivian']}}))
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',offline:true,bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{gpu:{name:'Test GPU',memory_used_mib:0,memory_total_mib:4096,utilization:0}},models:[],storage:{data:'/tmp/data'}}}))
 await page.goto('/#tts')
 await expect(page).toHaveTitle(/Sandevistan-Audio/)
 await expect(page.getByRole('heading',{name:'语音合成'})).toBeVisible()
 await staleStarted
 await page.getByRole('button',{name:'生成语音'}).click()
 const queueItem=page.locator('.tts-preview .job-mini').filter({hasText:'首次合成即时入列'})
 await expect(queueItem).toBeVisible({timeout:1000})
 await expect(queueItem).toBeInViewport()
 await expect(queueItem).toContainText('等待处理')
 await expect(queueItem).toContainText('队列第 2 / 4')
 await expect(queueItem).toContainText('预计剩余 30 秒–2 分钟')
 releaseStale()
 await page.waitForTimeout(150)
 await expect(queueItem).toBeVisible()
 await expect(queueItem).toContainText('正在处理',{timeout:5000})
 await page.screenshot({path:'/tmp/audio-intel-tts-first-submit-after.png',fullPage:false})
 await page.setViewportSize({width:390,height:844})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await page.screenshot({path:'/tmp/audio-intel-tts-first-submit-mobile.png',fullPage:false})
 expect(errors).toEqual([])
})

test('first ASR submission appears immediately from the accepted job response',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 await page.addInitScript(()=>Object.defineProperty(Object.getPrototypeOf(globalThis.crypto),'randomUUID',{configurable:true,value:undefined}))
 const now='2026-08-25T12:00:00+00:00'
 const queued={id:'abcdef1234567890abcdef1234567890',kind:'asr',state:'queued',stage:'queued',progress:0,display_name:'first-submit.wav',created_at:now,updated_at:now,processing_seconds:0,processing_as_of:now,attempts:0,compute_device:'gpu',compute_device_name:'Test GPU',source_url:'/api/v1/jobs/abcdef1234567890abcdef1234567890/source',request:{compute_device:'gpu',compute_device_name:'Test GPU'}}
 let submitted=false
 let releaseList:()=>void=()=>{}
 const listGate=new Promise<void>(resolve=>{releaseList=resolve})
 await routeJobList(page,async route=>{if(submitted)await listGate;return route.fulfill({json:{items:submitted?[queued]:[]}})})
 await page.route('**/api/v1/asr/jobs',route=>{expect(route.request().headers()['idempotency-key']).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);submitted=true;return route.fulfill({status:202,json:queued})})
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',offline:true,bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{gpu:{name:'Test GPU',memory_used_mib:0,memory_total_mib:4096,utilization:0}},models:[],storage:{data:'/tmp/data'}}}))
 await page.goto('/#asr')
 await page.locator('input[type="file"]').setInputFiles({name:'first-submit.wav',mimeType:'audio/wav',buffer:Buffer.from('RIFF-test')})
 await page.getByRole('button',{name:'开始转写'}).click()
 const queueItem=page.locator('.aside-jobs .job-mini').filter({hasText:'first-submit.wav'})
 await expect(queueItem).toBeVisible({timeout:1000})
 await expect(queueItem).toContainText('等待处理')
 releaseList()
 await page.screenshot({path:'/tmp/audio-intel-asr-http-uuid-desktop.png',fullPage:false})
 await page.setViewportSize({width:390,height:844})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await page.screenshot({path:'/tmp/audio-intel-asr-http-uuid-mobile.png',fullPage:false})
 expect(errors).toEqual([])
})

test('large ASR uploads expose progress, cancel safely, and retry with the same key',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 await page.addInitScript(()=>{
  let attempt=0
  const keys:string[]=[]
  class MockUploadXHR{
   status=0;statusText='';responseText='';withCredentials=false;timeout=0
   onload:(()=>void)|null=null;onerror:(()=>void)|null=null;ontimeout:(()=>void)|null=null;onabort:(()=>void)|null=null
   upload={onloadstart:null as ((event:ProgressEvent)=>void)|null,onprogress:null as ((event:ProgressEvent)=>void)|null,onload:null as ((event:ProgressEvent)=>void)|null}
   private timers:number[]=[]
   open(_method:string,_url:string){}
   setRequestHeader(name:string,value:string){if(name.toLowerCase()==='idempotency-key'){keys.push(value);(window as typeof window&{__uploadKeys?:string[]}).__uploadKeys=keys}}
   getResponseHeader(_name:string){return null}
   send(_body?:Document|XMLHttpRequestBodyInit|null){
    attempt+=1
    const currentAttempt=attempt
    const total=1024*1024
    this.upload.onloadstart?.(new ProgressEvent('loadstart'))
    this.timers.push(window.setTimeout(()=>this.upload.onprogress?.(new ProgressEvent('progress',{lengthComputable:true,loaded:total*.25,total})),30))
    if(currentAttempt===1)return
    this.timers.push(window.setTimeout(()=>this.upload.onprogress?.(new ProgressEvent('progress',{lengthComputable:true,loaded:total*.7,total})),200))
    this.timers.push(window.setTimeout(()=>this.upload.onload?.(new ProgressEvent('load')),350))
    this.timers.push(window.setTimeout(()=>{
     this.status=202
     this.responseText=JSON.stringify({id:'large-upload-job',kind:'asr',state:'queued',stage:'queued',progress:0,display_name:'large-upload.wav',created_at:new Date().toISOString(),request:{compute_device:'gpu'}})
     this.onload?.()
    },800))
   }
   abort(){this.timers.forEach(timer=>clearTimeout(timer));this.onabort?.()}
  }
  Object.defineProperty(window,'XMLHttpRequest',{configurable:true,value:MockUploadXHR})
 })
 await routeJobList(page,route=>route.fulfill({json:{items:[]}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{default_model:'qwen3-asr-0.6b',models:[],speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true},limits:{max_clone_reference_seconds:15,max_upload_bytes:2*1024*1024},events:{sse:false}}}))
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',offline:true,bind:'0.0.0.0:20810',services:['asr','tts'],workers:[],hardware:{gpu:{available:true}},models:[],storage:{data:'/tmp/data'}}}))
 await page.goto('/#asr')
 await page.locator('input[type="file"]').setInputFiles({name:'large-upload.wav',mimeType:'audio/wav',buffer:Buffer.alloc(1024*1024,1)})
 await page.getByRole('button',{name:'开始转写'}).click()
 const status=page.getByRole('region',{name:'ASR 音频提交状态'})
 await expect(status).toContainText('25%')
 await page.getByRole('button',{name:'取消上传'}).click()
 await expect(page.getByRole('status')).toContainText('上传已取消')
 await expect(page.getByText('large-upload.wav')).toBeVisible()
 const firstKey=(await page.evaluate(()=>(window as typeof window&{__uploadKeys?:string[]}).__uploadKeys?.[0]))||''
 await page.setViewportSize({width:390,height:844})
 await page.getByRole('button',{name:'开始转写'}).click()
 await expect(status).toContainText('70%')
 await expect(status).toContainText('上传完成，正在创建任务')
 await expect(page.getByRole('button',{name:'取消上传'})).toHaveCount(0)
 await expect(page.locator('.aside-jobs .job-mini').filter({hasText:'large-upload.wav'})).toBeVisible()
 const uploadKeys=await page.evaluate(()=>(window as typeof window&{__uploadKeys?:string[]}).__uploadKeys||[])
 expect(uploadKeys).toEqual([firstKey,firstKey])
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await page.screenshot({path:'/tmp/audio-intel-upload-progress-mobile.png',fullPage:false})
 expect(errors).toEqual([])
})

test('task history labels shortened IDs and copies the complete ID over HTTP fallback',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 const id='fedcba0987654321fedcba0987654321'
 const now='2026-08-25T12:00:00+00:00'
 const job={id,kind:'tts',state:'succeeded',stage:'completed',progress:1,display_name:'完整 ID 复制验证',created_at:now,updated_at:now,started_at:now,finished_at:now,processing_seconds:12,processing_as_of:now,attempts:1,compute_device:'cpu',compute_device_name:'CPU',request:{compute_device:'cpu',compute_device_name:'CPU'},result:{compute_device:'cpu',compute_device_name:'CPU'}}
 await page.addInitScript(()=>{
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:()=>Promise.reject(new Error('Clipboard API blocked'))}})
  Object.defineProperty(document,'execCommand',{configurable:true,value:(command:string)=>{if(command!=='copy')return false;const active=document.activeElement as HTMLTextAreaElement|null;(window as typeof window&{__copiedJobId?:string}).__copiedJobId=active?.value;return true}})
 })
 await routeJobList(page,route=>route.fulfill({json:{items:[job]}}))
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',offline:true,bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{},models:[],storage:{data:'/tmp/data'}}}))
 await page.goto('/#jobs')
 const shortId=page.locator('.job-id')
 await expect(shortId).toHaveText('任务 ID：fedcba098765…')
 await expect(shortId).toHaveAttribute('title',`完整任务 ID：${id}`)
 await page.getByRole('button',{name:`复制完整任务 ID ${id}`}).click()
 await expect(page.getByRole('button',{name:`已复制完整任务 ID ${id}`})).toBeVisible()
 await expect(page.getByRole('status')).toContainText(`已复制完整任务 ID ${id}`)
 expect(await page.evaluate(()=>(window as typeof window&{__copiedJobId?:string}).__copiedJobId)).toBe(id)
 await page.screenshot({path:'/tmp/audio-intel-job-id-copy-desktop.png',fullPage:false})
 await page.setViewportSize({width:390,height:844})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await expect(page.getByRole('button',{name:`已复制完整任务 ID ${id}`})).toBeVisible()
 await page.screenshot({path:'/tmp/audio-intel-job-id-copy-mobile.png',fullPage:false})
 expect(errors).toEqual([])
})

test('ASR speaker limit, filter, rename and voiceprint segment enrollment are interactive',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 const now='2026-08-25T12:00:00+00:00'
 const result={text:'尼克发言。凯文回答。',language:'Chinese',duration:4,timestamp_precision:'word_or_character',speakers:[{id:'Speaker_0',label:'尼克杨（研发一部）',label_source:'voiceprint',voiceprint_match:{person_id:'voice_nick',name:'尼克杨',note:'研发一部',score:.82}},{id:'Speaker_1',label:'Speaker 1',label_source:'default'}],segments:[{id:0,start:0,end:2,speaker:'Speaker_0',speaker_label:'尼克杨（研发一部）',text:'尼克发言。',words:[{text:'尼克发言',start:.2,end:1.6}]},{id:1,start:2,end:4,speaker:'Speaker_1',speaker_label:'Speaker 1',text:'凯文回答。',words:[{text:'凯文回答',start:2.2,end:3.6}]}],waveform:[.2,.4],artifacts:[]}
 const job={id:'asr-speaker-tools',kind:'asr',state:'succeeded',stage:'completed',progress:1,display_name:'meeting.wav',created_at:now,updated_at:now,request:{compute_device:'cpu'},result}
 const people=[{id:'voice_nick',name:'尼克杨',note:'研发一部',include_in_hotword_library:true,sample_count:1,created_at:now,updated_at:now,samples:[{id:'sample_nick',person_id:'voice_nick',state:'ready',language:'Chinese',transcript:'已有样本',words:[],duration:5,created_at:now,updated_at:now,tts_eligible:true,embedding_status:'ready',audio_url:'/sample.wav'}]}]
 let enrollment:{job_id:string;segment_ids:number[]}|undefined
 await routeJobList(page,route=>route.fulfill({json:{items:[job]}}))
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',offline:true,bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{},models:[],storage:{data:'/tmp/data'}}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true},limits:{max_clone_reference_seconds:15}}}))
 await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({json:{items:people}}))
 await page.route('**/api/v1/jobs/asr-speaker-tools/source',route=>route.fulfill({contentType:'audio/wav',body:Buffer.from('RIFF-test')}))
 await page.route('**/api/v1/jobs/asr-speaker-tools/speakers/Speaker_1',route=>route.fulfill({json:{...result,speakers:[result.speakers[0],{id:'Speaker_1',label:'凯文',label_source:'manual'}],segments:[result.segments[0],{...result.segments[1],speaker_label:'凯文'}]}}))
 await page.route('**/api/v1/voiceprints/people/voice_nick/samples/from-asr',async route=>{enrollment=await route.request().postDataJSON();await route.fulfill({status:201,json:{items:[]}})})
 await page.goto('/#asr')
 await expect(page.getByLabel('说话人数').locator('option')).toHaveCount(16)
 await expect(page.getByLabel('说话人数').locator('option').last()).toHaveText('15')
 await page.getByLabel('按说话人过滤').selectOption('Speaker_0')
 await expect(page.locator('.segments .speaker-identity small')).toHaveText('（研发一部）')
 await page.getByLabel('选择片段 1').check()
 await page.getByLabel('按说话人过滤').selectOption('Speaker_1')
 await expect(page.getByLabel('段落选择操作')).toHaveCount(0)
 await expect(page.locator('.segments article')).toHaveCount(1)
 await expect(page.locator('.segments article')).toContainText('凯文回答')
 await expect(page.getByLabel('选择片段 2')).toBeEnabled()
 const renameTrigger=page.locator('.segments .speaker')
 await renameTrigger.click()
 const renameDialog=page.getByRole('dialog',{name:'重命名说话人'})
 const renameInput=renameDialog.getByLabel('新名称')
 await expect(renameInput).toBeFocused()
 await page.keyboard.press('Escape')
 await expect(renameDialog).toHaveCount(0)
 await expect(renameTrigger).toBeFocused()
 await renameTrigger.click()
 await renameInput.fill('凯文')
 await page.getByRole('button',{name:'保存名称'}).click()
 await expect(page.getByLabel('按说话人过滤').locator('option',{hasText:'凯文'})).toHaveCount(1)
 await page.getByLabel('按说话人过滤').selectOption('Speaker_0')
 await page.getByLabel('选择片段 1').check()
 await page.getByRole('button',{name:'加入声纹库'}).click()
 await expect(page.getByRole('dialog',{name:'加入声纹库'})).toContainText('已匹配：尼克杨')
 await page.getByRole('button',{name:'确认加入'}).click()
 expect(enrollment).toEqual({job_id:'asr-speaker-tools',segment_ids:[0]})
 expect(errors).toEqual([])
})

test('TTS microphone explains the HTTP security boundary and keeps upload available',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 await page.addInitScript(()=>Object.defineProperty(window,'isSecureContext',{configurable:true,value:false}))
 await routeJobList(page,route=>route.fulfill({json:{items:[]}}))
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',offline:true,bind:'0.0.0.0:20810',services:['asr','tts'],workers:[],hardware:{gpu:{available:true}},models:[],storage:{data:'/tmp/data'}}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true,aligner_languages:['Chinese','English']},tts:{languages:['Auto','Chinese','English'],default_language:'Auto',preset_speaker_native_languages:{Vivian:'Chinese'}},limits:{max_clone_reference_seconds:15},events:{sse:false}}}))
 await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({json:{items:[]}}))
 await page.route('**/api/v1/tts/voices',route=>route.fulfill({json:{items:[],preset_speakers:['Vivian']}}))
 await page.goto('/#tts')
 await page.evaluate(()=>{localStorage.removeItem('audio-intel:tts-preferences:v2');sessionStorage.removeItem('audio-intel:tts-content:v2')})
 await page.reload()
 await page.getByRole('tab',{name:'声音克隆'}).click()
 await page.getByRole('tab',{name:'麦克风录音'}).click()
 await expect(page.locator('.recorder-unavailable')).toContainText('麦克风录音需要通过 localhost、127.0.0.1 或 HTTPS 访问')
 await expect(page.getByRole('tab',{name:'上传文件'})).toBeEnabled()
 await page.screenshot({path:'/tmp/audio-intel-tts-http-microphone-desktop.png',fullPage:false})
 await page.setViewportSize({width:390,height:844})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await page.screenshot({path:'/tmp/audio-intel-tts-http-microphone-mobile.png',fullPage:false})
 expect(errors).toEqual([])
})

test('one-off TTS clone references are auto-analyzed, editable and recoverable',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 await page.addInitScript(()=>{
  Object.defineProperty(Object.getPrototypeOf(globalThis.crypto),'randomUUID',{configurable:true,value:undefined})
  Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>({getTracks:()=>[{stop:()=>undefined}]})}})
  class FakeMediaRecorder{
   static isTypeSupported(type:string){return type==='audio/webm;codecs=opus'}
   state:RecordingState='inactive';mimeType='audio/webm;codecs=opus'
   ondataavailable:((event:BlobEvent)=>void)|null=null;onstop:((event:Event)=>void)|null=null;onerror:((event:Event)=>void)|null=null
   constructor(_stream:MediaStream,_options?:MediaRecorderOptions){}
   start(){this.state='recording'}
   stop(){this.state='inactive';queueMicrotask(()=>{this.ondataavailable?.({data:new Blob(['tts-clone-recording'],{type:this.mimeType})} as BlobEvent);this.onstop?.(new Event('stop'))})}
   pause(){};resume(){};requestData(){};addEventListener(){};removeEventListener(){};dispatchEvent(){return true}
   stream={} as MediaStream;audioBitsPerSecond=0;videoBitsPerSecond=0
  }
  Object.defineProperty(window,'MediaRecorder',{configurable:true,value:FakeMediaRecorder})
 })
 const now='2026-08-26T12:00:00+00:00'
 let analysisCount=0
 let analysisJob:Record<string,unknown>|null=null
 let analysisBodies:string[]=[]
 let ttsBody=''
 await routeJobList(page,route=>route.fulfill({json:{items:analysisJob?[analysisJob]:[]}}))
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',offline:true,bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{gpu:{available:true}},models:[],storage:{data:'/tmp/data'}}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true,aligner_languages:['Chinese','English']},tts:{languages:['Auto','Chinese','English'],default_language:'Auto',preset_speaker_native_languages:{Vivian:'Chinese'}},limits:{max_clone_reference_seconds:15}}}))
 await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({json:{items:[]}}))
 await page.route('**/api/v1/tts/voices',route=>route.fulfill({json:{items:[],preset_speakers:['Vivian']}}))
 await page.route('**/api/v1/jobs/*/artifacts/reference.wav',route=>route.fulfill({contentType:'audio/wav',body:Buffer.from('RIFF-reference')}))
 await page.route('**/api/v1/tts/clone-references',async route=>{
  expect(route.request().headers()['idempotency-key']).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  analysisCount+=1
  analysisBodies.push((await route.request().postDataBuffer())?.toString()||'')
  const id=`reference-analysis-${analysisCount}`
  const queued={id,kind:'asr',state:'queued',stage:'queued',progress:0,display_name:`TTS 克隆参考分析 · reference-${analysisCount}.wav`,created_at:now,updated_at:now,request:{purpose:'tts_clone_reference',compute_device:'gpu',accelerate_single_task:true}}
  await route.fulfill({status:202,json:queued})
  analysisJob={...queued,state:'succeeded',stage:'completed',progress:1,result:{text:analysisCount===1?'自动识别的上传文本。':'自动识别的录音文本。',language:'Chinese',duration:6,artifacts:[{name:'reference.wav',media_type:'audio/wav',size:1200}]}}
 })
 await page.route('**/api/v1/tts/jobs',async route=>{expect(route.request().headers()['idempotency-key']).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);ttsBody=(await route.request().postDataBuffer())?.toString()||'';await route.fulfill({status:202,json:{id:'tts-inline',kind:'tts',state:'queued',stage:'queued',progress:0,display_name:'一次性克隆',created_at:now,updated_at:now,request:{compute_device:'gpu'}}})})
 await page.goto('/#tts')
 await page.evaluate(()=>{localStorage.removeItem('audio-intel:tts-preferences:v2');sessionStorage.removeItem('audio-intel:tts-content:v2')})
 await page.reload()
 await page.getByRole('tab',{name:'声音克隆'}).click()
 await page.locator('.clone-reference-panel input[type="file"]').setInputFiles({name:'reference-upload.wav',mimeType:'audio/wav',buffer:Buffer.from('RIFF-reference')})
 await expect(page.getByText('参考识别完成')).toBeVisible({timeout:8000})
 await expect(page.getByLabel('自动识别文本（可修正）')).toHaveValue('自动识别的上传文本。')
 expect(analysisBodies[0]).toContain('reference-upload.wav')
 expect(analysisBodies[0]).toContain('gpu')
 expect(analysisBodies[0]).toContain('true')
 await page.getByRole('tab',{name:'麦克风录音'}).click()
 await page.getByRole('button',{name:'开始录音'}).click()
 await page.getByRole('button',{name:'停止并试听'}).click()
 await expect(page.getByText('参考录音完成')).toBeVisible()
 await page.getByRole('button',{name:'使用并自动分析'}).click()
 await expect(page.getByLabel('自动识别文本（可修正）')).toHaveValue('自动识别的录音文本。',{timeout:8000})
 expect(analysisBodies[1]).toContain('voiceprint-recording-')
 expect(analysisBodies[1]).toContain('.webm')
 const referenceText=page.getByLabel('自动识别文本（可修正）')
 await referenceText.fill('')
 await page.locator('.text-editor textarea').fill('这是需要生成的内容。')
 await expect(page.getByRole('button',{name:'生成语音'})).toBeDisabled()
 await referenceText.fill('人工核对后的准确文本。')
 await page.getByLabel('参考音频语种').selectOption('Chinese')
 await page.getByRole('button',{name:'生成语音'}).click()
 expect(ttsBody).toContain('reference_job_id')
 expect(ttsBody).toContain('reference-analysis-2')
 expect(ttsBody).toContain('人工核对后的准确文本。')
 expect(ttsBody).toContain('reference_language')
 expect(ttsBody).not.toContain('reference_audio')
 const saved=await page.evaluate(()=>sessionStorage.getItem('audio-intel:tts-content:v2'))
 expect(saved).toContain('reference-analysis-2')
 await page.setViewportSize({width:390,height:844})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await page.screenshot({path:'/tmp/audio-intel-clone-reference-mobile.png',fullPage:false})
 expect(errors).toEqual([])
})

test('voiceprint library sample can be explicitly selected for TTS clone',async({page})=>{
 const now='2026-08-25T12:00:00+00:00'
 const people=[{id:'voice_nick',name:'尼克杨',sample_count:1,created_at:now,updated_at:now,samples:[{id:'sample_long',person_id:'voice_nick',state:'ready',language:'Chinese',transcript:'这是一条超过十五秒的准确参考文本。',words:[],duration:20,created_at:now,updated_at:now,tts_eligible:true,embedding_status:'ready',audio_url:'/sample.wav'}]}]
 let submitted:Record<string,string>={}
 await routeJobList(page,route=>route.fulfill({json:{items:[]}}))
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',offline:true,bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{},models:[],storage:{data:'/tmp/data'}}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true},limits:{max_clone_reference_seconds:15}}}))
 await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({json:{items:people}}))
 await page.route('**/api/v1/tts/voices',route=>route.fulfill({json:{items:[],preset_speakers:['Vivian']}}))
 await page.route('**/api/v1/tts/jobs',async route=>{const data=await route.request().postDataBuffer();const body=data?.toString()||'';submitted={body};await route.fulfill({status:202,json:{id:'tts-voiceprint',kind:'tts',state:'queued',stage:'queued',progress:0,display_name:'声纹克隆',created_at:now,request:{compute_device:'cpu'}}})})
 await page.goto('/#tts')
 await page.getByRole('tab',{name:'声音克隆'}).click()
 await page.getByRole('tab',{name:'声纹库',exact:true}).click()
 await expect(page.getByLabel('TTS 声纹样本')).toHaveValue('sample_long')
 await expect(page.getByText(/精确截断至 15 秒以内/)).toBeVisible()
 await page.getByLabel('TTS 计算设备').selectOption('cpu')
 await page.getByRole('button',{name:'生成语音'}).click()
 expect(submitted.body).toContain('voiceprint')
 expect(submitted.body).toContain('sample_long')
 await page.locator('nav').getByRole('button',{name:/声纹库/}).click()
 await expect(page.getByRole('heading',{name:'声纹库',exact:true})).toBeVisible()
 await page.setViewportSize({width:390,height:844})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
})

test('compact desktop widths keep the full shell and TTS workspace in view',async({page})=>{
 for(const width of [1180,1100,1024,960,901]){
  await page.setViewportSize({width,height:820})
  await page.goto('/#tts')
  await expect(page.getByRole('heading',{name:'语音合成'})).toBeVisible()
  await expect(page.locator('nav').getByRole('button',{name:/系统状态/})).toBeVisible()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  const bounds=await page.evaluate(()=>[document.querySelector('.app-shell>header')?.getBoundingClientRect(),document.querySelector('.app-shell>main')?.getBoundingClientRect()].map(rect=>({left:rect?.left||0,right:rect?.right||0})))
  expect(bounds.every(rect=>rect.left>=0&&rect.right<=width+.5)).toBe(true)
  if(width===1024)await page.screenshot({path:'/tmp/audio-intel-compact-1024.png',fullPage:false})
 }
})

test('view result reveals ASR and TTS result panels on mobile',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 const now='2026-08-25T12:00:00+00:00'
 const asrResult={text:'移动端结果。',language:'Chinese',duration:3,timestamp_precision:'segment',speakers:[{id:'Speaker_0',label:'Speaker 0'}],segments:[{id:0,start:0,end:3,speaker:'Speaker_0',speaker_label:'Speaker 0',text:'移动端结果。'}],waveform:[.2,.5,.3],artifacts:[]}
 const jobs=[
  {id:'mobile-asr',kind:'asr',state:'succeeded',stage:'completed',progress:1,display_name:'移动端转写',created_at:now,updated_at:now,request:{compute_device:'cpu'},result:asrResult},
  {id:'mobile-tts',kind:'tts',state:'succeeded',stage:'completed',progress:1,display_name:'移动端合成',created_at:now,updated_at:now,request:{compute_device:'cpu'},result:{duration:2,speaker:'Vivian',format:'wav',compute_device:'cpu',waveform:[.2,.4],artifacts:[]}},
 ]
 await routeJobList(page,route=>route.fulfill({json:{items:jobs,count:jobs.length,total:jobs.length,limit:25,offset:0,has_more:false}}))
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',offline:true,bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{},models:[],storage:{data:'/tmp/data'}}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true},limits:{max_clone_reference_seconds:15}}}))
 await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({json:{items:[]}}))
 await page.route('**/api/v1/tts/voices',route=>route.fulfill({json:{items:[],preset_speakers:['Vivian']}}))
 await page.route('**/api/v1/jobs/*/source',route=>route.fulfill({contentType:'audio/wav',body:Buffer.from('RIFF-test')}))
 await page.setViewportSize({width:390,height:844})
 await page.goto('/#jobs')
 const assertRevealed=async(selector:string)=>expect.poll(async()=>{const box=await page.locator(selector).boundingBox();return Boolean(box&&box.y>=59&&box.y<844)}).toBe(true)
 await page.locator('.table-row').filter({hasText:'移动端转写'}).getByTitle('查看结果').click()
 await expect(page).toHaveURL(/#asr$/)
 await assertRevealed('.result-panel')
 await page.locator('nav').getByRole('button',{name:/任务记录/}).click()
 await page.locator('.table-row').filter({hasText:'移动端合成'}).getByTitle('查看结果').click()
 await expect(page).toHaveURL(/#tts$/)
 await assertRevealed('.tts-preview')
 await page.screenshot({path:'/tmp/audio-intel-mobile-result-reveal.png',fullPage:false})
 expect(errors).toEqual([])
})

test('task history paginates on the server and pins an old selected result',async({page})=>{
 const base=Date.parse('2026-08-25T12:00:00+00:00')
 const jobs=Array.from({length:61},(_,index)=>({
  id:`history-${String(index+1).padStart(2,'0')}`,kind:index%2===0?'asr':'tts',state:'succeeded',stage:'completed',progress:1,
  display_name:`历史任务 ${index+1}`,created_at:new Date(base+index*1000).toISOString(),updated_at:new Date(base+index*1000).toISOString(),request:{compute_device:'cpu'},
  result:index%2===0?{text:`结果 ${index+1}`,language:'Chinese',duration:2,timestamp_precision:'segment',speakers:[],segments:[],waveform:[.2,.4],artifacts:[]}:{duration:2,speaker:'Vivian',format:'wav',compute_device:'cpu',waveform:[.2,.4],artifacts:[]},
 }))
 const sorted=[...jobs].sort((left,right)=>Date.parse(right.created_at)-Date.parse(left.created_at))
 const requests:string[]=[]
 await page.route('**/api/v1/auth/session',route=>route.fulfill({json:{required:false,authenticated:true}}))
 await routeJobList(page,route=>{
  const url=new URL(route.request().url());requests.push(url.search)
  const kind=url.searchParams.get('kind'),state=url.searchParams.get('state'),query=(url.searchParams.get('q')||'').toLowerCase()
  const filtered=sorted.filter(job=>(!kind||job.kind===kind)&&(!state||job.state===state)&&(!query||job.id.includes(query)||job.display_name.toLowerCase().includes(query)))
  const limit=Number(url.searchParams.get('limit')||100),offset=Number(url.searchParams.get('offset')||0),items=filtered.slice(offset,offset+limit)
  return route.fulfill({json:{items,count:items.length,total:filtered.length,limit,offset,has_more:offset+items.length<filtered.length}})
 })
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',offline:true,bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{},models:[],storage:{data:'/tmp/data'}}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true},limits:{max_clone_reference_seconds:15}}}))
 await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({json:{items:[]}}))
 await page.route('**/api/v1/tts/voices',route=>route.fulfill({json:{items:[],preset_speakers:['Vivian']}}))
 await page.route('**/api/v1/jobs/*/source',route=>route.fulfill({contentType:'audio/wav',body:Buffer.from('RIFF-test')}))
 await page.goto('/#jobs')
 await expect(page.locator('.table-row')).toHaveCount(25)
 await expect(page.getByText('第 1 / 3 页 · 共 61 条')).toBeVisible()
 await page.getByRole('button',{name:'第 3 页'}).click()
 await expect(page.locator('.table-row')).toHaveCount(11)
 await expect(page.getByText('第 3 / 3 页 · 共 61 条')).toBeVisible()
 const oldRow=page.locator('.table-row').filter({has:page.getByText('历史任务 1',{exact:true})})
 await oldRow.getByRole('button',{name:/查看任务结果/}).click()
 await expect(page).toHaveURL(/#asr$/)
 await expect(page.locator('.aside-jobs .job-mini')).toHaveCount(6)
 await expect(page.locator('.aside-jobs .job-mini[aria-current="true"]')).toContainText('历史任务 1')
 await page.locator('nav').getByRole('button',{name:/任务记录/}).click()
 await expect(page.getByText('第 3 / 3 页 · 共 61 条')).toBeVisible()
 await page.getByPlaceholder('任务名称或 ID').fill('history-58')
 await expect(page.locator('.table-row')).toHaveCount(1)
 await expect(page.locator('.table-row')).toContainText('历史任务 58')
 await page.locator('.filter').getByRole('button',{name:'TTS'}).click()
 await expect(page.locator('.table-row')).toHaveCount(1)
 expect(requests.some(value=>value.includes('limit=25')&&value.includes('offset=50'))).toBe(true)
 expect(requests.some(value=>value.includes('q=history-58')&&value.includes('kind=tts'))).toBe(true)
})

test('task lists stay newest-first and retain an older selected task',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 const base=Date.parse('2026-08-25T12:00:00+00:00')
 const asrJobs=Array.from({length:7},(_,index)=>({
  id:`sorted-asr-${index+1}`,kind:'asr',state:'succeeded',stage:'completed',progress:1,
  display_name:`ASR 任务 ${index+1}`,created_at:new Date(base+index*2000).toISOString(),
  updated_at:new Date(base+index*2000).toISOString(),request:{compute_device:'cpu'},
  result:{text:`ASR 结果 ${index+1}`,language:'Chinese',duration:2,timestamp_precision:'segment',speakers:[],segments:[],waveform:[.2,.4],artifacts:[]},
 }))
 const ttsJobs=Array.from({length:7},(_,index)=>({
  id:`sorted-tts-${index+1}`,kind:'tts',state:'succeeded',stage:'completed',progress:1,
  display_name:`TTS 任务 ${index+1}`,created_at:new Date(base+index*2000+1000).toISOString(),
  updated_at:new Date(base+index*2000+1000).toISOString(),request:{compute_device:'cpu'},
  result:{duration:2,speaker:'Vivian',format:'wav',compute_device:'cpu',waveform:[.2,.4],artifacts:[]},
 }))
 const jobs=[asrJobs[2],ttsJobs[0],asrJobs[6],ttsJobs[4],asrJobs[0],ttsJobs[6],asrJobs[4],ttsJobs[2],asrJobs[1],ttsJobs[5],asrJobs[5],ttsJobs[1],asrJobs[3],ttsJobs[3]]
 await page.route('**/api/v1/auth/session',route=>route.fulfill({json:{required:false,authenticated:true}}))
 await routeJobList(page,route=>{const sorted=[...jobs].sort((left,right)=>Date.parse(right.created_at)-Date.parse(left.created_at));return route.fulfill({json:{items:sorted,count:sorted.length,total:sorted.length,limit:25,offset:0,has_more:false}})})
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',offline:true,bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{},models:[],storage:{data:'/tmp/data'}}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true},limits:{max_clone_reference_seconds:15}}}))
 await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({json:{items:[]}}))
 await page.route('**/api/v1/tts/voices',route=>route.fulfill({json:{items:[],preset_speakers:['Vivian']}}))
 await page.route('**/api/v1/jobs/*/source',route=>route.fulfill({contentType:'audio/wav',body:Buffer.from('RIFF-test')}))
 await page.goto('/#jobs')
 await expect(page).toHaveTitle(/Sandevistan-Audio/)
 await expect(page.getByRole('heading',{name:'任务记录'})).toBeVisible()
 const expectedHistory=[...jobs].sort((left,right)=>Date.parse(right.created_at)-Date.parse(left.created_at)).map(job=>job.display_name)
 await expect(page.locator('.table-row .job-name>b')).toHaveText(expectedHistory)

 await page.locator('nav').getByRole('button',{name:/转写工作台/}).click()
 await expect(page.locator('.aside-jobs .job-mini')).toHaveCount(5)
 await expect(page.locator('.aside-jobs .job-mini[aria-current="true"]')).toContainText('ASR 任务 7')
 await page.locator('nav').getByRole('button',{name:/任务记录/}).click()

 await page.locator('.table-row').filter({hasText:'ASR 任务 1'}).getByTitle('查看结果').click()
 await expect(page).toHaveURL(/#asr$/)
 await expect(page.getByRole('heading',{name:'任务列表'})).toBeVisible()
 await expect(page.locator('.aside-jobs .job-mini')).toHaveText([/ASR 任务 7/,/ASR 任务 6/,/ASR 任务 5/,/ASR 任务 4/,/ASR 任务 3/,/ASR 任务 1/])
 const selectedAsr=page.locator('.aside-jobs .job-mini[aria-current="true"]')
 await expect(selectedAsr).toHaveCount(1)
 await expect(selectedAsr).toContainText('ASR 任务 1')
 await expect(selectedAsr).toHaveClass(/selected/)
 await expect(page.locator('.result-head')).toContainText('ASR 任务 1')
 await selectedAsr.scrollIntoViewIfNeeded()
 await page.screenshot({path:'/tmp/audio-intel-selected-asr-desktop.png',fullPage:false})

 await page.locator('nav').getByRole('button',{name:/任务记录/}).click()
 await page.locator('.table-row').filter({hasText:'TTS 任务 1'}).getByTitle('查看结果').click()
 await expect(page).toHaveURL(/#tts$/)
 await expect(page.locator('.tts-preview>h2')).toHaveText(['当前合成结果','任务列表'])
 await expect(page.locator('.audio-card')).toContainText('TTS 任务 1')
 await expect(page.locator('.tts-preview .job-mini')).toHaveText([/TTS 任务 7/,/TTS 任务 6/,/TTS 任务 5/,/TTS 任务 4/,/TTS 任务 3/,/TTS 任务 1/])
 const selectedTts=page.locator('.tts-preview .job-mini[aria-current="true"]')
 await expect(selectedTts).toHaveCount(1)
 await expect(selectedTts).toContainText('TTS 任务 1')
 await expect(selectedTts).toHaveClass(/selected/)
 await expect(page.locator('vite-error-overlay')).toHaveCount(0)
 await page.screenshot({path:'/tmp/audio-intel-selected-tts-desktop.png',fullPage:false})

 await page.setViewportSize({width:390,height:844})
 await selectedTts.scrollIntoViewIfNeeded()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await expect(selectedTts).toBeInViewport()
 await page.screenshot({path:'/tmp/audio-intel-selected-tts-mobile.png',fullPage:false})
 expect(errors).toEqual([])
})

test('long mobile transcripts render progressively and pass scroll at boundaries',async({page})=>{
 const now='2026-08-25T12:00:00+00:00'
 const segments=Array.from({length:220},(_,id)=>({id,start:id*2,end:id*2+1.8,speaker:`Speaker_${id%3}`,speaker_label:`Speaker ${id%3}`,text:`第 ${id+1} 条会议转写内容，用于验证长列表内部滚动。`}))
 const result={text:segments.map(item=>item.text).join(''),language:'Chinese',duration:440,timestamp_precision:'segment',speakers:Array.from({length:3},(_,id)=>({id:`Speaker_${id}`,label:`Speaker ${id}`})),segments,waveform:[.2,.5,.3],artifacts:[]}
 const job={id:'long-mobile-asr',kind:'asr',state:'succeeded',stage:'completed',progress:1,display_name:'超长会议记录',created_at:now,updated_at:now,request:{compute_device:'cpu'},result}
 await routeJobList(page,route=>route.fulfill({json:{items:[job]}}))
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',offline:true,bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{},models:[],storage:{data:'/tmp/data'}}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true},limits:{max_clone_reference_seconds:15}}}))
 await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({json:{items:[]}}))
 await page.setViewportSize({width:390,height:844})
 await page.goto('/#asr')
 await expect(page.locator('.segments article')).toHaveCount(40)
 await expect(page.getByText('已展示 40 / 匹配 220 / 总计 220')).toBeVisible()
 const loadMore=page.getByRole('button',{name:'加载更多'})
 await expect(loadMore).toHaveCount(1)
 await loadMore.dispatchEvent('click')
 await expect(page.locator('.segments article')).toHaveCount(80)
 await page.locator('.result-panel').evaluate(element=>element.scrollIntoView({block:'start'}))
 await expect(page.locator('.result-panel')).toBeInViewport()
 await page.waitForTimeout(50)
 const dimensions=await page.locator('.segments').evaluate(element=>({clientHeight:element.clientHeight,scrollHeight:element.scrollHeight}))
 expect(dimensions.clientHeight).toBeGreaterThan(100)
 expect(dimensions.scrollHeight).toBeGreaterThan(dimensions.clientHeight*10)
 expect(await page.evaluate(()=>document.documentElement.scrollHeight)).toBeLessThan(3000)
 await page.locator('.segments').evaluate(element=>{element.scrollTop=element.scrollHeight})
 await expect.poll(()=>page.locator('.segments article').count()).toBeGreaterThan(80)
 await page.locator('.transcript-tools input').fill('第 220 条会议转写内容')
 await expect(page.locator('.segments article')).toHaveCount(1)
 await expect(page.getByText('第 220 条会议转写内容，用于验证长列表内部滚动。')).toBeVisible()
 await page.locator('.transcript-tools input').fill('')
 await expect(page.locator('.segments article')).toHaveCount(40)
 await page.locator('.result-panel').evaluate(element=>element.scrollIntoView({block:'start'}))
 await page.locator('.segments').evaluate(element=>{element.scrollTop=0})
 const outerBefore=await page.evaluate(()=>window.scrollY)
 expect(outerBefore).toBeGreaterThan(0)
 await page.locator('.segments').hover()
 await page.mouse.wheel(0,-600)
 await expect.poll(()=>page.evaluate(()=>window.scrollY)).toBeLessThan(outerBefore)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await page.locator('.result-panel').evaluate(element=>element.scrollIntoView({block:'start'}))
 await page.screenshot({path:'/tmp/audio-intel-long-transcript-mobile.png',fullPage:false})
})
