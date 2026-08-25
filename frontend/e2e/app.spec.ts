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
 await waveform.click({position:{x:Math.max(5,(await waveform.boundingBox())!.width*.7),y:25}})
 await expect.poll(async()=>page.locator('audio').evaluate((element:HTMLAudioElement)=>element.currentTime)).toBeGreaterThan(2)
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
 await ttsDevice.selectOption('gpu')
 await page.waitForTimeout(2500)
 await expect(ttsDevice).toHaveValue('gpu')
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
 await page.route('**/api/v1/health',route=>route.fulfill({json:{status:'ok',bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{gpu:{name:'Test GPU',memory_used_mib:0,memory_total_mib:4096,utilization:0}},models:[],storage:{}}}))
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
 await page.route('**/api/v1/health',route=>route.fulfill({json:{status:'ok',bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{gpu:{name:'Test GPU',memory_used_mib:0,memory_total_mib:4096,utilization:0}},models:[],storage:{}}}))
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
 await page.route('**/api/v1/health',route=>route.fulfill({json:{status:'ok',bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{},models:[],storage:{}}}))
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
