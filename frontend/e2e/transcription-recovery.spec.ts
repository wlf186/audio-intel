import {mockTimelineWaveform} from './timeline-fixture'
import {downloadConcise,openTranscriptExport} from './ux-helpers'
import {capabilitiesFor,expect,test,type Page} from './fixtures'
import {createHash} from 'node:crypto'

type EventWindow=Window&{emitJobEvent:(name:string,data:unknown)=>void;holdHash:boolean;releaseHash:()=>void}
async function fixture(page:Page,locale='zh-CN'){
 await page.addInitScript(locale=>{
  localStorage.setItem('audio-intel:ui-locale:v1',locale)
  class MockEventSource extends EventTarget{
   static instances:MockEventSource[]=[]
   onopen:((event:Event)=>void)|null=null
   constructor(public url:string){super();MockEventSource.instances.push(this);queueMicrotask(()=>this.onopen?.(new Event('open')))}
   close(){}
  }
  window.EventSource=MockEventSource as unknown as typeof EventSource
  ;(window as EventWindow).emitJobEvent=(name,data)=>{for(const source of MockEventSource.instances)source.dispatchEvent(new MessageEvent(name,{data:JSON.stringify(data)}))}
 },locale)
 const state={revision:1,conflict:false,patches:[] as Array<{revision:number;action:string;scene_id?:string;time?:number;export_hidden_scene_ids?:string[]}>,sceneReads:0,sceneDelay:undefined as Promise<void>|undefined,frameTime:2,advanceSceneOnSave:false,hidden:[] as string[]}
 const scenes=()=>[{id:'scene_1',start:0,end:10,frame_time:state.frameTime,image:`frame-${state.revision}.jpg`,paragraphs:[{speaker:'s0',speaker_label:'讲者',start:0,end:8,text:'Presentation discussion.',segment_ids:[0]}]},{id:'scene_2',start:10,end:20,frame_time:12,image:'frame2.jpg',paragraphs:[]}]
 const summary={id:'recovery-video',kind:'asr',purpose:'video_transcription',state:'succeeded',stage:'completed',progress:1,display_name:'Recovery fixture.mp4',created_at:'2026-10-10T00:00:00Z',updated_at:'2026-10-10T00:00:00Z',compute_device:'cpu'}
 const result=()=>({text:'Presentation discussion.',duration:20,language:'Chinese',timestamp_precision:'segment',speakers:[{id:'s0',label:'讲者'}],segments:[{id:0,start:0,end:8,speaker:'s0',text:'Presentation discussion.'}],artifacts:[],video:{contract_version:1,revision:state.revision,duration:20,time_origin:0,detector:'fixture',export_hidden_scene_ids:state.hidden,scenes:scenes(),original_scenes:scenes()}})
 await page.route('**/api/v1/auth/session',route=>route.fulfill({json:{required:true,authenticated:true}}))
 await page.route('**/api/v1/capabilities',async route=>{const body=await capabilitiesFor(page);body.events={...body.events,sse:true};await route.fulfill({json:body})})
 await page.route(url=>url.pathname==='/api/v1/jobs',route=>route.fulfill({json:{items:[summary,{...summary,id:'other-video',display_name:'Other fixture.mp4'}],count:2,total:2,limit:100,offset:0,has_more:false}}))
 await page.route(/\/api\/v1\/jobs\/(recovery-video|other-video)$/,route=>route.fulfill({json:{...summary,id:new URL(route.request().url()).pathname.split('/').pop(),request:{compute_device:'cpu',language:'Chinese'},result:result()}}))
 await mockTimelineWaveform(page,'recovery-video',20)
 await page.route('**/video-scenes',async route=>{
  if(route.request().method()==='PATCH'){
   state.patches.push(route.request().postDataJSON())
   if(state.conflict)return route.fulfill({status:409,json:{detail:'Sections changed'}})
   if(state.advanceSceneOnSave){state.revision++;const patch=state.patches.at(-1)!;if(patch.action==='frame')state.frameTime=patch.time!}
   if(state.patches.at(-1)?.action==='set_export_visibility')state.hidden=state.patches.at(-1)!.export_hidden_scene_ids!
   return route.fulfill({json:result()})
  }
  state.sceneReads++;await state.sceneDelay;return route.fulfill({json:{revision:state.revision,scenes:scenes()}})
 })
 await page.route('**/video-frames/*',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540"><rect width="960" height="540" fill="white"/><text x="70" y="130" font-size="48">Presentation fixture</text></svg>'}))
 await page.route(/\/api\/v1\/jobs\/(recovery-video|other-video)\/source$/,route=>route.fulfill({contentType:'audio/wav',body:Buffer.from('UklGRiQAAABXQVZFZm10IBAAAAABAAEAgD4AAAB9AAACABAAZGF0YQAAAAA=','base64')}))
 return {state,summary,async open(){await page.goto('/#asr');await page.getByRole('tab',{name:locale==='zh-CN'?'任务与结果':'Tasks & results',exact:true}).click();await expect(page.locator('.video-section')).toHaveCount(2)}}
}

for(const width of [1440,390])test(`visibility save waits for refreshed scenes before reopening at ${width}px`,async({page},info)=>{
 const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 await page.setViewportSize({width,height:960})
 const f=await fixture(page);await f.open();f.state.advanceSceneOnSave=true
 await page.getByRole('button',{name:'画面检测',exact:true}).click()
 await page.getByRole('checkbox',{name:'选择画面 1',exact:true}).check()
 const held=Promise.withResolvers<void>();f.state.sceneDelay=held.promise
 try{
  await page.getByRole('dialog').getByRole('button',{name:'导出时隐藏',exact:true}).click()
  await expect(page.getByRole('dialog').getByRole('status')).toContainText('已保存');await page.keyboard.press('Escape')
  await expect.poll(()=>f.state.sceneReads).toBeGreaterThanOrEqual(2)
  const restore=page.getByRole('button',{name:'画面检测',exact:true})
  await expect(restore).toBeDisabled()
  await expect(page.locator('.video-transcript [role=status]')).toContainText('正在读取图文分段')
  held.resolve()
  await expect(page.locator('.video-section').first().getByRole('img',{name:'00:00:02 的视频画面',exact:true})).toBeVisible()
  await restore.click()
  const dialog=page.getByRole('dialog',{name:'画面检测',exact:true})
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button',{name:'全部恢复',exact:true}).click()
  await expect(dialog.locator('.scene-hidden-badge')).toHaveCount(0);await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(page.locator('.video-section')).toHaveCount(2)
  expect(f.state.patches).toEqual([{revision:1,action:'set_export_visibility',export_hidden_scene_ids:['scene_1']},{revision:2,action:'set_export_visibility',export_hidden_scene_ids:[]}])
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  await page.screenshot({path:info.outputPath('restored-after-refresh.png')})
  expect(errors).toEqual([])
 }finally{held.resolve()}
})

for(const width of [1440,390])for(const locale of ['zh-CN','en-US']){
 const zh=locale==='zh-CN'
 test(`scene revisions preserve drafts and reading controls ${width} ${locale}`,async({page},testInfo)=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error'&&!m.text().includes('409 (Conflict)'))errors.push(m.text())})
  await page.setViewportSize({width,height:960})
  const f=await fixture(page,locale);await f.open()
  expect(await page.locator('.video-section .video-scene-actions').count()).toBe(0)
  await expect(page.getByRole('button',{name:zh?'编辑分段':'Edit sections',exact:true})).toHaveCount(0)
  await page.getByRole('button',{name:zh?'画面检测':'Picture detection',exact:true}).click()
  const dialog=page.getByRole('dialog')
  const input=dialog.getByRole('spinbutton').first()
  await input.fill('4')
  await dialog.getByRole('checkbox').first().check()
  f.state.revision=2;f.summary.updated_at='2026-10-10T00:01:00Z'
  await page.evaluate(job=>(window as EventWindow).emitJobEvent('update',{jobs:[job],removed_job_ids:[]}),f.summary)
  await expect(dialog.getByRole('button',{name:zh?'导出时隐藏':'Hide in exports',exact:true})).toBeDisabled()
  await expect(input).toHaveValue('4')
  await expect(dialog.getByRole('alert')).toContainText(zh?'关闭并重新打开':'Close and reopen')
  expect(f.state.patches).toHaveLength(0)
  await page.keyboard.press('Escape')
  await page.getByRole('button',{name:zh?'画面检测':'Picture detection',exact:true}).click()
  await dialog.getByRole('checkbox').first().check()
  f.state.conflict=true
  const hide=dialog.getByRole('button',{name:zh?'导出时隐藏':'Hide in exports',exact:true})
  await hide.click();await expect(hide).toBeDisabled()
  expect(f.state.patches.at(-1)).toMatchObject({revision:2,action:'set_export_visibility'})
  await expect(dialog.getByRole('checkbox').first()).toBeChecked()
  await page.keyboard.press('Escape')
  f.state.conflict=false
  await page.getByRole('button',{name:zh?'画面检测':'Picture detection',exact:true}).click()
  await dialog.getByRole('checkbox').first().check();await hide.click()
  await expect(dialog.locator('.scene-hidden-badge')).toHaveCount(1)
  await page.keyboard.press('Escape')
  await expect(page.locator('.video-section .video-scene-actions')).toHaveCount(0)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  expect(await page.locator('.video-transcript button:visible').evaluateAll(items=>items.filter(item=>item.getBoundingClientRect().height<44).length)).toBe(0)
  await page.screenshot({path:testInfo.outputPath('reading.png'),fullPage:true})
  expect(errors).toEqual([])
 })
 test(`PDF recoverable errors and single POST ${width} ${locale}`,async({page},testInfo)=>{
  await page.setViewportSize({width,height:960})
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error'&&!/429|413|401|ERR_FAILED/.test(m.text()))errors.push(m.text())})
  const f=await fixture(page,locale);let status=429;const methods:string[]=[]
  await page.route('**/transcript-export',route=>{
   methods.push(route.request().method())
   expect(route.request().postDataJSON()).toEqual({format:'pdf',redact:true,replacements:[]})
   if(status===0)return route.abort('failed')
   if(status!==200)return route.fulfill({status,headers:{'Retry-After':'1'},json:{detail:'fixture'}})
   return route.fulfill({contentType:'application/pdf',headers:{'Content-Disposition':"attachment; filename*=UTF-8''%E8%AE%B2%E5%BA%A7.pdf"},body:'%PDF-test'})
  })
  await f.open()
  await openTranscriptExport(page)
  const exportButton=page.locator('.transcript-export-modal .modal-actions .primary')
  await downloadConcise(page);await expect(exportButton).toBeDisabled();await expect(page.locator('.transcript-export-modal [role=alert]')).toContainText(zh?'繁忙':'busy');await expect(exportButton).toBeEnabled()
  status=413;await downloadConcise(page);await expect(page.locator('.transcript-export-modal [role=alert]')).toContainText(zh?'上限':'limit')
  status=0;await downloadConcise(page);await expect(page.locator('.transcript-export-modal [role=alert]')).toContainText(zh?'中断':'interrupted')
  status=200;const downloading=page.waitForEvent('download');await downloadConcise(page);const download=await downloading;expect(download.suggestedFilename()).toBe('讲座.pdf');expect(await download.failure()).toBeNull()
  expect(methods).toEqual(['POST','POST','POST','POST'])
  await expect(page.locator('.transcript-export-modal [role=alert]')).toHaveCount(0)
  await page.screenshot({path:testInfo.outputPath('export.png'),fullPage:true})
  status=401;await downloadConcise(page);await expect(page.getByRole('heading',{name:zh?'访问验证':'Access verification',exact:true})).toBeVisible()
  expect(errors).toEqual([])
 })
}

test('switching tasks aborts a pending PDF without downloading stale content',async({page,responseGate})=>{
 const f=await fixture(page),held=responseGate();let downloads=0
 page.on('download',()=>downloads++)
 await page.route('**/transcript-export',async route=>{await held.hold();await route.fulfill({contentType:'application/pdf',body:'%PDF-old'}).catch(()=>undefined)})
 await f.open();const requested=page.waitForRequest('**/transcript-export')
 await downloadConcise(page);const request=await requested
 const failed=page.waitForEvent('requestfailed',{predicate:failed=>failed===request})
 await page.locator('.transcript-export-modal .modal-close').click()
 await page.getByRole('button',{name:/Other fixture.mp4/}).click()
 await expect(page.locator('.transcript-export button.primary')).toBeEnabled();held.release();await failed
 await expect(page.locator('.transcript-export button.primary')).toBeEnabled();expect(downloads).toBe(0)
})

for(const width of [1440,390])test(`resume verification reports confirmed bytes and can pause ${width}`,async({page})=>{
 await page.setViewportSize({width,height:960})
 const data=Buffer.alloc(12288,65);const id='0123456789abcdef0123456789abcdef'
 const chunks=[0,4096].map(offset=>({offset,size_bytes:4096,sha256:createHash('sha256').update(data.subarray(offset,offset+4096)).digest('hex')}))
 await page.addInitScript(({id,size})=>{
  sessionStorage.setItem('audio-intel:video-upload:v1',JSON.stringify({id,name:'resume.mp4',size,createKey:'create-key',jobKey:'job-key',params:{language:'Auto',compute_device:'cpu',model:'qwen3-asr-0.6b'}}))
  const state=window as EventWindow;state.holdHash=true
  window.Worker=new Proxy(Worker,{construct(Target,args){
   const worker=Reflect.construct(Target,args) as Worker
   const post=worker.postMessage.bind(worker)
   worker.postMessage=((...values:Parameters<Worker['postMessage']>)=>{const dispatch=()=>post(...values);if(state.holdHash)state.releaseHash=dispatch;else dispatch()}) as Worker['postMessage']
   return worker
  }})
 },{id,size:data.length})
 let patched=0;let submissions=0
 await page.route('**/api/v1/asr/video-uploads/'+id,route=>{
  if(route.request().method()==='PATCH'){patched++;expect(route.request().headers()['upload-offset']).toBe('8192');return route.fulfill({json:{id,offset:data.length,chunk_bytes:4096,chunks,job_id:null}})}
  return route.fulfill({json:{id,name:'resume.mp4',size_bytes:data.length,offset:8192,chunk_bytes:4096,chunks,job_id:null}})
 })
 await page.route('**/api/v1/asr/video-jobs',route=>{submissions++;return route.fulfill({status:202,json:{id:'resumed-job',kind:'asr',state:'queued',stage:'queued',progress:0,display_name:'resume.mp4',created_at:new Date().toISOString(),request:{}}})})
 await page.goto('/#asr');await page.getByRole('tab',{name:'新建视频转写',exact:true}).click();await page.locator('input[type=file]').setInputFiles({name:'resume.mp4',mimeType:'video/mp4',buffer:data})
 await page.getByRole('button',{name:'开始转写',exact:true}).click()
 const progress=page.locator('.submission-progress')
 await expect(progress).toContainText('正在校验已上传内容');await expect(progress.locator('progress')).toHaveAttribute('value','0')
 await expect.poll(()=>page.evaluate(()=>typeof (window as EventWindow).releaseHash)).toBe('function')
 await page.evaluate(()=>(window as EventWindow).releaseHash())
 await expect(progress.locator('progress')).toHaveAttribute('value','50')
 await page.getByRole('button',{name:'暂停上传',exact:true}).click();expect(patched).toBe(0)
 await page.evaluate(()=>{(window as EventWindow).holdHash=false})
 await page.getByRole('button',{name:'开始转写',exact:true}).click();await expect(page.getByRole('button',{name:'查看本次任务'})).toBeVisible()
 expect(patched).toBe(1);expect(submissions).toBe(1);expect(await page.evaluate(()=>sessionStorage.getItem('audio-intel:video-upload:v1'))).toBeNull()
})
