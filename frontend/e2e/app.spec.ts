import {expect,test} from '@playwright/test'

test('Sandevistan-Audio branding and TTS transport render as local assets',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 await page.goto('/#tts')
 await expect(page).toHaveTitle('Sandevistan-Audio')
 await expect(page.locator('.brand-type b')).toHaveText('SANDEVISTAN-AUDIO')
 const mark=page.locator('.brand-lockup img')
 await expect(mark).toBeVisible()
 await expect.poll(()=>mark.evaluate((image:HTMLImageElement)=>image.naturalWidth)).toBeGreaterThan(0)
 const player=page.locator('.audio-transport audio')
 await expect(player).toHaveCount(1)
 await page.getByRole('button',{name:'播放最近生成'}).click()
 await expect.poll(()=>player.evaluate((audio:HTMLAudioElement)=>audio.currentTime)).toBeGreaterThan(.1)
 await page.getByRole('button',{name:'暂停最近生成'}).click()
 await expect.poll(()=>player.evaluate((audio:HTMLAudioElement)=>audio.paused)).toBe(true)
 expect(errors).toEqual([])
})

test('TTS draft and clone mode survive background polling',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 await page.goto('/#tts')
 await page.evaluate(()=>sessionStorage.removeItem('audio-intel:tts-draft-v1'))
 await page.reload()
 const text=page.locator('.text-editor textarea')
 await text.fill('')
 await page.waitForTimeout(4500)
 await expect(text).toHaveValue('')
 await page.getByRole('button',{name:'声音克隆'}).click()
 await expect(page.getByPlaceholder(/必须与参考音频逐字一致/)).toBeVisible()
 await page.waitForTimeout(4500)
 await expect(page.getByPlaceholder(/必须与参考音频逐字一致/)).toBeVisible()
 await expect(page.getByRole('button',{name:/生成语音/})).toBeDisabled()
 expect(errors).toEqual([])
})

test('ASR playback, seek and transcript search are interactive',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
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
 const duration=await page.locator('audio').evaluate((element:HTMLAudioElement)=>element.duration)
 await waveform.click({position:{x:Math.max(5,(await waveform.boundingBox())!.width*.7),y:25}})
 await expect.poll(async()=>page.locator('audio').evaluate((element:HTMLAudioElement)=>element.currentTime)).toBeGreaterThan(duration*.5)
 await page.locator('.transcript-tools input').fill('不存在的内容')
 await expect(page.locator('.segments article')).toHaveCount(0)
 await expect(page.getByText('没有匹配的转写片段')).toBeVisible()
 expect(errors).toEqual([])
})

test('navigation and mobile layout render without overflow',async({page})=>{
 await page.goto('/#jobs')
 await expect(page.getByRole('heading',{name:'任务记录'})).toBeVisible()
 await expect(page.locator('.filter')).toBeVisible()
 await page.getByRole('button',{name:'系统状态'}).click()
 await expect(page.getByRole('heading',{name:'系统状态'})).toBeVisible()
 await page.setViewportSize({width:390,height:844})
 await page.goto('/#tts')
 await expect(page.getByRole('heading',{name:'语音合成'})).toBeVisible()
 const width=await page.evaluate(()=>document.documentElement.scrollWidth)
 expect(width).toBeLessThanOrEqual(390)
 await page.screenshot({path:'/tmp/audio-intel-after-mobile.png',fullPage:false})
})

test('ASR and TTS compute device defaults and selections survive polling',async({page})=>{
 await page.goto('/#asr')
 await page.evaluate(()=>sessionStorage.removeItem('audio-intel:asr-device-v1'))
 await page.reload()
 const asrDevice=page.getByLabel('ASR 计算设备')
 await expect(asrDevice).toHaveValue('gpu')
 await asrDevice.selectOption('cpu')
 await page.waitForTimeout(2500)
 await expect(asrDevice).toHaveValue('cpu')
 await page.goto('/#tts')
 await page.evaluate(()=>sessionStorage.removeItem('audio-intel:tts-draft-v1'))
 await page.reload()
 const ttsDevice=page.getByLabel('TTS 计算设备')
 await expect(ttsDevice).toHaveValue('cpu')
 await expect(page.getByText('CPU · FP32 · SDPA · BATCH 1')).toBeVisible()
 await ttsDevice.selectOption('gpu')
 await expect(page.getByText('GPU · BF16 · SDPA · ADAPTIVE BATCH 1–2')).toBeVisible()
 await page.waitForTimeout(2500)
 await expect(ttsDevice).toHaveValue('gpu')
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
 await page.screenshot({path:'/tmp/audio-intel-auth-login.png',fullPage:false})
 await page.getByPlaceholder('输入 AUDIO_INTEL_API_KEY').fill('browser-secret')
 await page.getByRole('button',{name:'进入工作台'}).click()
 await expect(page.getByRole('dialog',{name:'访问验证'})).toHaveCount(0)
 await expect(page.locator('.model-list>div')).toHaveCount(6)
 await page.screenshot({path:'/tmp/audio-intel-authenticated-system.png',fullPage:false})
 expect(submittedAuthorization).toBe('Bearer browser-secret')
 expect(await page.evaluate(()=>({stored:sessionStorage.getItem('audio-intel:key'),url:location.href}))).toEqual({stored:null,url:expect.not.stringContaining('browser-secret')})
 await page.getByRole('button',{name:'退出本地会话'}).click()
 await expect(page.getByRole('dialog',{name:'访问验证'})).toBeVisible()
 expect(errors).toEqual([])
})

test('task duration, single/multi/select-all and partial batch deletion are interactive',async({page})=>{
 const now=new Date().toISOString()
 const jobs=[
  {id:'asr-completed',kind:'asr',state:'succeeded',stage:'completed',progress:1,display_name:'已完成转写',created_at:now,updated_at:now,started_at:now,finished_at:now,processing_seconds:3661,processing_as_of:now,attempts:1,compute_device:'gpu',compute_device_name:'NVIDIA RTX A1000 Laptop GPU',request:{compute_device:'gpu',compute_device_name:'NVIDIA RTX A1000 Laptop GPU'},result:{compute_device:'gpu',compute_device_name:'NVIDIA RTX A1000 Laptop GPU'}},
  {id:'asr-queued',kind:'asr',state:'queued',stage:'queued',progress:0,display_name:'排队转写',created_at:now,updated_at:now,processing_seconds:0,processing_as_of:now,attempts:0,request:{compute_device:'cpu'}},
  {id:'tts-failed',kind:'tts',state:'failed',stage:'failed',progress:.4,display_name:'失败合成',created_at:now,updated_at:now,started_at:now,finished_at:now,processing_seconds:61,processing_as_of:now,attempts:2,request:{compute_device:'cpu'}},
  {id:'tts-running',kind:'tts',state:'running',stage:'synthesizing',progress:.5,display_name:'运行中合成',created_at:now,updated_at:now,started_at:now,processing_seconds:5,processing_as_of:now,attempts:1,request:{compute_device:'gpu'}},
 ]
 let submitted:string[]=[]
 await page.route('**/api/v1/jobs',route=>route.request().method()==='GET'?route.fulfill({json:{items:jobs}}):route.fallback())
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
 const header=page.getByLabel('全选当前筛选任务')
 await page.getByLabel('选择任务 已完成转写').check()
 expect(await header.evaluate((element:HTMLInputElement)=>element.indeterminate)).toBe(true)
 await page.getByLabel('选择任务 排队转写').check()
 await expect(page.getByText('2 个任务已选择')).toBeVisible()
 await page.locator('.filter').getByRole('button',{name:'TTS'}).click()
 await expect(page.getByLabel('批量任务操作')).toHaveCount(0)
 await page.locator('.filter').getByRole('button',{name:'全部'}).click()
 await header.check()
 await expect(page.getByText('3 个任务已选择')).toBeVisible()
 await page.evaluate(()=>{window.confirm=()=>true})
 await page.getByRole('button',{name:'永久删除所选任务'}).click()
 await expect(page.getByRole('status')).toContainText('释放 10.0 MB')
 await expect(page.getByRole('alert')).toContainText('模拟文件占用')
 await expect(page.getByText('1 个任务已选择')).toBeVisible()
 expect(submitted.sort()).toEqual(['asr-completed','asr-queued','tts-failed'])
 await page.screenshot({path:'/tmp/audio-intel-jobs-batch-desktop.png',fullPage:false})
 await page.setViewportSize({width:390,height:844})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
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
 await page.route('**/api/v1/jobs',route=>route.fulfill({json:{items:[running]}}))
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
 const queued={id:'1234567890abcdef1234567890abcdef',kind:'tts',state:'queued',stage:'queued',progress:0,display_name:'首次合成即时入列',created_at:now,updated_at:now,processing_seconds:0,processing_as_of:now,attempts:0,compute_device:'cpu',compute_device_name:'CPU',request:{compute_device:'cpu',compute_device_name:'CPU'}}
 const running={...queued,state:'running',stage:'synthesizing',progress:.25,started_at:now,attempts:1}
 let jobsRequests=0
 let submitted=false
 let markStaleStarted:()=>void=()=>{}
 let releaseStale:()=>void=()=>{}
 const staleStarted=new Promise<void>(resolve=>{markStaleStarted=resolve})
 const staleGate=new Promise<void>(resolve=>{releaseStale=resolve})
 await page.route('**/api/v1/jobs',async route=>{
  jobsRequests+=1
  if(jobsRequests===1)return route.fulfill({json:{items:[]}})
  if(!submitted){markStaleStarted();await staleGate;return route.fulfill({json:{items:[]}})}
  return route.fulfill({json:{items:[running]}})
 })
 await page.route('**/api/v1/tts/jobs',route=>{submitted=true;return route.fulfill({status:202,json:queued})})
 await page.route('**/api/v1/tts/voices',route=>route.fulfill({json:{items:[],preset_speakers:['Vivian']}}))
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{gpu:{name:'Test GPU',memory_used_mib:0,memory_total_mib:4096,utilization:0}},models:[],storage:{}}}))
 await page.goto('/#tts')
 await expect(page).toHaveTitle('Sandevistan-Audio')
 await expect(page.getByRole('heading',{name:'语音合成'})).toBeVisible()
 await staleStarted
 await page.getByRole('button',{name:'生成语音'}).click()
 const queueItem=page.locator('.tts-preview .job-mini').filter({hasText:'首次合成即时入列'})
 await expect(queueItem).toBeVisible({timeout:1000})
 await expect(queueItem).toBeInViewport()
 await expect(queueItem).toContainText('等待处理')
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
 const now='2026-08-25T12:00:00+00:00'
 const queued={id:'abcdef1234567890abcdef1234567890',kind:'asr',state:'queued',stage:'queued',progress:0,display_name:'first-submit.wav',created_at:now,updated_at:now,processing_seconds:0,processing_as_of:now,attempts:0,compute_device:'gpu',compute_device_name:'Test GPU',source_url:'/api/v1/jobs/abcdef1234567890abcdef1234567890/source',request:{compute_device:'gpu',compute_device_name:'Test GPU'}}
 let submitted=false
 let releaseList:()=>void=()=>{}
 const listGate=new Promise<void>(resolve=>{releaseList=resolve})
 await page.route('**/api/v1/jobs',async route=>{if(submitted)await listGate;return route.fulfill({json:{items:submitted?[queued]:[]}})})
 await page.route('**/api/v1/asr/jobs',route=>{submitted=true;return route.fulfill({status:202,json:queued})})
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{gpu:{name:'Test GPU',memory_used_mib:0,memory_total_mib:4096,utilization:0}},models:[],storage:{}}}))
 await page.goto('/#asr')
 await page.locator('input[type="file"]').setInputFiles({name:'first-submit.wav',mimeType:'audio/wav',buffer:Buffer.from('RIFF-test')})
 await page.getByRole('button',{name:'开始转写'}).click()
 const queueItem=page.locator('.aside-jobs .job-mini').filter({hasText:'first-submit.wav'})
 await expect(queueItem).toBeVisible({timeout:1000})
 await expect(queueItem).toContainText('等待处理')
 releaseList()
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
 await page.route('**/api/v1/jobs',route=>route.fulfill({json:{items:[job]}}))
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{},models:[],storage:{}}}))
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
 const result={text:'尼克发言。凯文回答。',language:'Chinese',duration:4,timestamp_precision:'word_or_character',speakers:[{id:'Speaker_0',label:'尼克杨',label_source:'manual'},{id:'Speaker_1',label:'Speaker 1',label_source:'default'}],segments:[{id:0,start:0,end:2,speaker:'Speaker_0',speaker_label:'尼克杨',text:'尼克发言。',words:[{text:'尼克发言',start:.2,end:1.6}]},{id:1,start:2,end:4,speaker:'Speaker_1',speaker_label:'Speaker 1',text:'凯文回答。',words:[{text:'凯文回答',start:2.2,end:3.6}]}],waveform:[.2,.4],artifacts:[]}
 const job={id:'asr-speaker-tools',kind:'asr',state:'succeeded',stage:'completed',progress:1,display_name:'meeting.wav',created_at:now,updated_at:now,request:{compute_device:'cpu'},result}
 const people=[{id:'voice_nick',name:'尼克杨',sample_count:1,created_at:now,updated_at:now,samples:[{id:'sample_nick',person_id:'voice_nick',state:'ready',language:'Chinese',transcript:'已有样本',words:[],duration:5,created_at:now,updated_at:now,tts_eligible:true,embedding_status:'ready',audio_url:'/sample.wav'}]}]
 let enrollment:{job_id:string;segment_ids:number[]}|undefined
 await page.route('**/api/v1/jobs',route=>route.fulfill({json:{items:[job]}}))
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{},models:[],storage:{}}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true},limits:{max_clone_reference_seconds:15}}}))
 await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({json:{items:people}}))
 await page.route('**/api/v1/jobs/asr-speaker-tools/source',route=>route.fulfill({contentType:'audio/wav',body:Buffer.from('RIFF-test')}))
 await page.route('**/api/v1/jobs/asr-speaker-tools/speakers/Speaker_1',route=>route.fulfill({json:{...result,speakers:[result.speakers[0],{id:'Speaker_1',label:'凯文',label_source:'manual'}],segments:[result.segments[0],{...result.segments[1],speaker_label:'凯文'}]}}))
 await page.route('**/api/v1/voiceprints/people/voice_nick/samples/from-asr',async route=>{enrollment=await route.request().postDataJSON();await route.fulfill({status:201,json:{items:[]}})})
 await page.goto('/#asr')
 await expect(page.getByLabel('说话人数').locator('option')).toHaveCount(16)
 await expect(page.getByLabel('说话人数').locator('option').last()).toHaveText('15')
 await page.getByLabel('按说话人过滤').selectOption('Speaker_0')
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

test('voiceprint library sample can be explicitly selected for TTS clone',async({page})=>{
 const now='2026-08-25T12:00:00+00:00'
 const people=[{id:'voice_nick',name:'尼克杨',sample_count:1,created_at:now,updated_at:now,samples:[{id:'sample_long',person_id:'voice_nick',state:'ready',language:'Chinese',transcript:'这是一条超过十五秒的准确参考文本。',words:[],duration:20,created_at:now,updated_at:now,tts_eligible:true,embedding_status:'ready',audio_url:'/sample.wav'}]}]
 let submitted:Record<string,string>={}
 await page.route('**/api/v1/jobs',route=>route.fulfill({json:{items:[]}}))
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{},models:[],storage:{}}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true},limits:{max_clone_reference_seconds:15}}}))
 await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({json:{items:people}}))
 await page.route('**/api/v1/tts/voices',route=>route.fulfill({json:{items:[],preset_speakers:['Vivian']}}))
 await page.route('**/api/v1/tts/jobs',async route=>{const data=await route.request().postDataBuffer();const body=data?.toString()||'';submitted={body};await route.fulfill({status:202,json:{id:'tts-voiceprint',kind:'tts',state:'queued',stage:'queued',progress:0,display_name:'声纹克隆',created_at:now,request:{compute_device:'cpu'}}})})
 await page.goto('/#tts')
 await page.getByRole('button',{name:'声音克隆'}).click()
 await page.getByRole('button',{name:'声纹库',exact:true}).click()
 await expect(page.getByLabel('TTS 声纹样本')).toHaveValue('sample_long')
 await expect(page.getByText(/精确截断至 15 秒以内/)).toBeVisible()
 await page.getByRole('button',{name:'生成语音'}).click()
 expect(submitted.body).toContain('voiceprint')
 expect(submitted.body).toContain('sample_long')
 await page.locator('nav').getByRole('button',{name:/声纹库/}).click()
 await expect(page.getByRole('heading',{name:'声纹库'})).toBeVisible()
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
 await page.route('**/api/v1/jobs',route=>route.fulfill({json:{items:jobs}}))
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{},models:[],storage:{}}}))
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

test('long mobile transcripts scroll inside the bounded result panel',async({page})=>{
 const now='2026-08-25T12:00:00+00:00'
 const segments=Array.from({length:220},(_,id)=>({id,start:id*2,end:id*2+1.8,speaker:`Speaker_${id%3}`,speaker_label:`Speaker ${id%3}`,text:`第 ${id+1} 条会议转写内容，用于验证长列表内部滚动。`}))
 const result={text:segments.map(item=>item.text).join(''),language:'Chinese',duration:440,timestamp_precision:'segment',speakers:Array.from({length:3},(_,id)=>({id:`Speaker_${id}`,label:`Speaker ${id}`})),segments,waveform:[.2,.5,.3],artifacts:[]}
 const job={id:'long-mobile-asr',kind:'asr',state:'succeeded',stage:'completed',progress:1,display_name:'超长会议记录',created_at:now,updated_at:now,request:{compute_device:'cpu'},result}
 await page.route('**/api/v1/jobs',route=>route.fulfill({json:{items:[job]}}))
 await page.route('**/api/v1/system',route=>route.fulfill({json:{status:'ok',bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{},models:[],storage:{}}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:{asr:{speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true},limits:{max_clone_reference_seconds:15}}}))
 await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({json:{items:[]}}))
 await page.setViewportSize({width:390,height:844})
 await page.goto('/#asr')
 await expect(page.locator('.segments article')).toHaveCount(220)
 await page.locator('.result-panel').evaluate(element=>element.scrollIntoView({block:'start'}))
 await expect(page.locator('.result-panel')).toBeInViewport()
 await page.waitForTimeout(50)
 const dimensions=await page.locator('.segments').evaluate(element=>({clientHeight:element.clientHeight,scrollHeight:element.scrollHeight}))
 expect(dimensions.clientHeight).toBeGreaterThan(100)
 expect(dimensions.scrollHeight).toBeGreaterThan(dimensions.clientHeight*10)
 expect(await page.evaluate(()=>document.documentElement.scrollHeight)).toBeLessThan(3000)
 await page.locator('.segments').evaluate(element=>{element.scrollTop=element.scrollHeight})
 await expect(page.getByText('第 220 条会议转写内容，用于验证长列表内部滚动。')).toBeInViewport()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await page.screenshot({path:'/tmp/audio-intel-long-transcript-mobile.png',fullPage:false})
})
