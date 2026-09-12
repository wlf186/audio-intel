import {expect,test,type Page} from '@playwright/test'
import type {Job,JobSummary} from '../src/lib/types'

function wave(){
 const data=Buffer.alloc(32044)
 data.write('RIFF');data.writeUInt32LE(data.length-8,4);data.write('WAVEfmt ',8);data.writeUInt32LE(16,16);data.writeUInt16LE(1,20);data.writeUInt16LE(1,22);data.writeUInt32LE(16000,24);data.writeUInt32LE(32000,28);data.writeUInt16LE(2,32);data.writeUInt16LE(16,34);data.write('data',36);data.writeUInt32LE(data.length-44,40)
 return data
}
async function fixture(page:Page){
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 const now=new Date().toISOString()
 const document:Job={id:'doc-result',kind:'tts',purpose:'tts_document',state:'succeeded',stage:'completed',progress:1,display_name:'文档任务',created_at:now,updated_at:now,request:{voice_mode:'preset'},result:{duration:2,model:'qwen3-tts-0.6b',speaker:'Vivian',document:{contract_version:1,title:'文档任务',downloads:{sections:'/api/v1/jobs/doc-result/document/download?mode=sections',complete:'/api/v1/jobs/doc-result/document/download?mode=complete'},sections:[0,1].map(i=>({id:`part-${i}`,index:i+1,title:`章节 ${i+1}`,sample_rate:16000,artifact_name:`part-${i}.mp3`,duration:1}))}}}
 const text:Job={...document,id:'text-result',purpose:undefined,display_name:'文本任务',created_at:'2026-01-01T00:00:00Z',result:{duration:1,speaker:'Vivian',format:'wav',artifacts:[{name:'speech.wav',path:'speech.wav',mime_type:'audio/wav',size_bytes:32044}]}}
 const state={jobs:[document,text],detailReads:[] as string[],submitted:undefined as Job|undefined,failDetail:false,waveformReads:[] as string[],failWaveform:false}
 const summary=(job:Job):JobSummary=>{const {request:_,result:__,...value}=job;return value}
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname
  if(path==='/api/v1/capabilities'){
   const response=await route.fetch();const body=await response.json();body.events={...body.events,sse:false};return route.fulfill({response,json:body})
  }
  if(path==='/api/v1/jobs')return route.fulfill({json:{items:state.jobs.map(summary),count:state.jobs.length,total:state.jobs.length,has_more:false}})
  if(path==='/api/v1/tts/jobs'&&route.request().method()==='POST'){
   const job:Job={...text,id:'new-job',created_at:new Date(Date.now()+10000).toISOString(),state:'queued',stage:'queued',progress:0,display_name:'本次文本任务',result:undefined}
   state.submitted=job;state.jobs.unshift(job)
   return route.fulfill({status:202,json:job})
  }
  if(path.endsWith('/waveform')){state.waveformReads.push(path);return state.failWaveform?route.fulfill({status:503,json:{detail:'waveform temporarily unavailable'}}):route.fulfill({json:{artifact_name:'audio',duration:1,waveform:Array.from({length:240},(_,i)=>.1+(i%20)/25)}})}
  if(path.includes('/artifacts/')){
   const audio=wave()
   const range=route.request().headers()['range']?.match(/bytes=(\d+)-(\d*)/)
   const start=range?Number(range[1]):0
   const end=range&&range[2]?Math.min(Number(range[2]),audio.length-1):audio.length-1
   return route.fulfill({status:range?206:200,contentType:'audio/wav',headers:{'Accept-Ranges':'bytes',...(range?{'Content-Range':`bytes ${start}-${end}/${audio.length}`}:{})},body:audio.subarray(start,end+1)})
  }
  const job=state.jobs.find(item=>path===`/api/v1/jobs/${item.id}`)
  if(job){state.detailReads.push(job.id);return state.failDetail?route.fulfill({status:503,json:{detail:'详情暂时不可用'}}):route.fulfill({json:job})}
  return route.continue()
 })
 await page.goto('/#tts')
 await expect(page.getByRole('tab',{name:'文本合成',exact:true})).toHaveAttribute('aria-selected','true')
 return {state,errors}
}

for(const width of [1440,1280,1024,720,390]){
 test(`fixed navigation and a single result surface at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:width===720?450:900})
  const {state,errors}=await fixture(page)
  await page.locator('.text-editor textarea').fill('切换后保留的文本草稿')
  const navigation=page.locator('.tts-workspace-tabs')
  await page.locator('.tts-workspace').evaluate(async element=>{await Promise.all(element.getAnimations().map(animation=>animation.finished))})
  const origin=await navigation.boundingBox()
  expect(state.detailReads).toEqual([])
  for(const name of ['文档合成','任务与结果','文本合成','任务与结果','文档合成','文本合成']){
   await page.getByRole('tab',{name,exact:true}).click()
   await page.locator('#main-content').evaluate(element=>element.scrollTo(0,0))
   const box=await navigation.boundingBox()
   expect(Math.abs(box!.x-origin!.x)).toBeLessThanOrEqual(1)
   expect(Math.abs(box!.y-origin!.y)).toBeLessThanOrEqual(1)
   expect(Math.abs(box!.width-origin!.width)).toBeLessThanOrEqual(1)
   if(name==='任务与结果')await expect(page.locator('.document-results')).toBeVisible()
   else await expect(page.locator('.tts-preview')).toBeHidden()
   expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  }
  await expect(page.locator('.text-editor textarea')).toHaveValue('切换后保留的文本草稿')
  if(width===1440||width===390)await page.screenshot({path:`/tmp/tts-unified-text-zh-${width}.png`})
  await expect(page.locator('.tts-settings')).toHaveCount(1)
  await expect(page.locator('.document-results')).toHaveCount(1)
  await page.getByRole('tab',{name:'文本合成',exact:true}).focus()
  await page.keyboard.press('ArrowRight')
  await expect(page.getByRole('tab',{name:'文档合成',exact:true})).toBeFocused()
  await page.keyboard.press('End')
  await expect(page.getByRole('tab',{name:'任务与结果',exact:true})).toHaveAttribute('aria-selected','true')
  await page.locator('.document-results select').selectOption('1')
  await page.getByRole('tab',{name:'文本合成',exact:true}).click()
  await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
  await expect(page.locator('.document-results select')).toHaveValue('1')
  if(width===1440||width===390)await page.screenshot({path:`/tmp/tts-unified-results-zh-${width}.png`})
  await page.locator('.language-switcher select:visible').first().selectOption('en-US')
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  for(const button of await navigation.getByRole('tab').all())expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await expect(page).toHaveTitle(/Speech synthesis/)
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  await page.screenshot({path:`/tmp/tts-unified-results-${width}.png`})
  await page.getByRole('tab',{name:'Text',exact:true}).click()
  await expect(page.getByRole('tab',{name:'Text',exact:true})).toHaveAttribute('aria-selected','true')
  await page.screenshot({path:`/tmp/tts-unified-text-${width}.png`})
  expect(errors).toEqual([])
 })
}

test('submitted task is selected across queue updates without replacing a deliberate selection',async({page})=>{
 test.setTimeout(60000)
 const {state,errors}=await fixture(page)
 await page.locator('.text-editor textarea').fill('新任务')
 await page.getByRole('button',{name:'生成语音',exact:true}).click()
 await expect(page.getByRole('tab',{name:'文本合成',exact:true})).toHaveAttribute('aria-selected','true')
 await page.getByRole('button',{name:'查看本次任务',exact:true}).click()
 await expect(page.locator('.tts-task-status')).toContainText('本次文本任务')
 await expect(page.locator('.audio-card')).toHaveCount(0)
 state.submitted!.state='running';state.submitted!.stage='synthesis';state.submitted!.progress=.4;state.submitted!.updated_at=new Date(Date.now()+1000).toISOString()
 await expect(page.locator('.tts-task-status')).toContainText('40%',{timeout:10000})
 await page.locator('.tts-recent-jobs').getByRole('button').filter({hasText:'文档任务'}).click()
 await expect(page.locator('.document-results')).toBeVisible()
 state.submitted!.state='failed';state.submitted!.stage='failed';state.submitted!.error_message='测试合成失败';state.submitted!.updated_at=new Date(Date.now()+2000).toISOString()
 await expect(page.locator('.tts-recent-jobs').getByRole('button').filter({hasText:'本次文本任务'})).toContainText('失败',{timeout:10000})
 await expect(page.locator('.document-results')).toBeVisible()
 await page.locator('.tts-recent-jobs').getByRole('button').filter({hasText:'本次文本任务'}).click()
 await expect(page.locator('.tts-task-status')).toContainText('测试合成失败')
 state.submitted!.state='cancelled';state.submitted!.stage='cancelled';state.submitted!.updated_at=new Date(Date.now()+3000).toISOString()
 await expect(page.locator('.tts-task-status')).toContainText('已取消',{timeout:10000})
 state.submitted!.state='succeeded';state.submitted!.stage='completed';state.submitted!.progress=1;state.submitted!.result={duration:1,format:'wav',artifacts:[{name:'speech.wav',path:'speech.wav',mime_type:'audio/wav',size_bytes:32044}]};state.submitted!.updated_at=new Date(Date.now()+4000).toISOString()
 await expect(page.locator('.audio-card')).toContainText('本次文本任务',{timeout:10000})
 await expect(page.locator('.audio-card audio')).toHaveAttribute('src','/api/v1/jobs/new-job/artifacts/speech.wav')
 await page.getByRole('tab',{name:'文本合成',exact:true}).click()
 await expect(page.locator('.text-editor textarea')).toHaveValue('新任务')
 await page.getByRole('button',{name:'恢复默认配置'}).click()
 await expect(page.getByRole('tab',{name:'文本合成',exact:true})).toHaveAttribute('aria-selected','true')
 await expect(page.locator('.text-editor textarea')).toHaveValue('新任务')
 expect(errors).toEqual([])
})

test('result details expose failures and retry without replacing the selected task',async({page})=>{
 const {state}=await fixture(page)
 state.failDetail=true
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(page.locator('.tts-result-detail [role=alert]')).toContainText('详情暂时不可用')
 state.failDetail=false
 await page.locator('.tts-result-detail').getByRole('button',{name:'重新加载'}).click()
 await expect(page.locator('.document-results')).toBeVisible()
 await expect(page.locator('.tts-recent-jobs [aria-current=true]')).toContainText('文档任务')
})

test('task list distinguishes loading and failure and opens filtered task management',async({page})=>{
 const {state}=await fixture(page)
 let release:()=>void=()=>{}
 const gate=new Promise<void>(resolve=>{release=resolve})
 let count=0
 await page.route('**/api/v1/jobs',async route=>{
  count++
  if(count===1){await gate;return route.fulfill({status:503,json:{detail:'任务列表暂时不可用'}})}
  return route.fulfill({json:{items:state.jobs.map(({request:_,result:__,...job})=>job),total:2,count:2,has_more:false}})
 })
 await page.reload()
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(page.locator('.tts-task-list .resource-state.loading')).toBeVisible()
 await expect(page.locator('.tts-task-list').getByText('还没有合成结果')).not.toBeVisible()
 release()
 await expect(page.locator('.tts-task-list .resource-state.error')).toContainText('任务列表暂时不可用')
 await page.locator('.tts-task-list').getByRole('button',{name:'重试',exact:true}).click()
 await expect(page.locator('.document-results')).toBeVisible()
 await page.getByRole('button',{name:'全部合成任务与管理',exact:true}).click()
 await expect(page).toHaveURL(/#jobs$/)
 await expect(page.locator('.filter button[aria-pressed=true]')).toHaveText('TTS')
})

test('opening results pins the initial task while API submissions arrive',async({page})=>{
 const {state,errors}=await fixture(page)
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(page.locator('.document-results')).toBeVisible()
 state.jobs.unshift({...state.jobs[1],id:'api-submission',display_name:'API 新任务',created_at:new Date(Date.now()+10000).toISOString()})
 await expect(page.locator('.tts-recent-jobs')).toContainText('API 新任务',{timeout:10000})
 await expect(page.locator('.tts-recent-jobs [aria-current=true]')).toContainText('文档任务')
 await expect(page.locator('.document-results')).toBeVisible()
 expect(errors).toEqual([])
})


for(const width of [1440,390]){
 test(`document waveform paints, seeks and caches chapters at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:900})
  const {state,errors}=await fixture(page)
  expect(state.waveformReads).toEqual([])
  await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
  const canvas=page.locator('.document-results canvas')
  await expect(canvas).not.toHaveClass(/waveform-empty/)
  expect(state.waveformReads).toHaveLength(1)
  expect(await canvas.evaluate(element=>{
   const canvas=element as HTMLCanvasElement
   const data=canvas.getContext('2d')!.getImageData(0,0,canvas.width,canvas.height).data
   let colored=0
   for(let i=0;i<data.length;i+=4)if(data[i+1]>100&&data[i+3])colored++
   return colored
  })).toBeGreaterThan(500)
  await page.getByRole('button',{name:'播放当前合成结果'}).click()
  await expect(page.getByRole('button',{name:'暂停当前合成结果'})).toBeVisible()
  await page.getByRole('button',{name:'暂停当前合成结果'}).click()
  await canvas.focus()
  await page.keyboard.press('End')
  await expect(canvas).toHaveAttribute('aria-valuenow','1')
  await page.locator('.document-results select').selectOption('1')
  await expect(canvas).not.toHaveClass(/waveform-empty/)
  expect(state.waveformReads).toHaveLength(2)
  await expect(page.locator('.document-results audio')).toHaveAttribute('src',/part-1/)
  await page.locator('.document-results select').selectOption('0')
  await expect(canvas).not.toHaveClass(/waveform-empty/)
  expect(state.waveformReads).toHaveLength(2)
  await page.getByRole('tab',{name:'文本合成',exact:true}).click()
  await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
  await expect(canvas).not.toHaveClass(/waveform-empty/)
  expect(state.waveformReads).toHaveLength(2)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  await expect(page).toHaveTitle(/语音合成/)
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  await page.screenshot({path:`/tmp/tts-waveform-${width}.png`})
  expect(errors).toEqual([])
 })
}

test('waveform failure allows playback and recovers with retry',async({page})=>{
 const {state,errors}=await fixture(page)
 state.failWaveform=true
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 const retry=page.getByRole('button',{name:'重试波形'})
 await expect(retry).toBeVisible()
 await page.getByRole('button',{name:'播放当前合成结果'}).click()
 await expect(page.getByRole('button',{name:'暂停当前合成结果'})).toBeVisible()
 state.failWaveform=false
 await retry.click()
 await expect(page.locator('.document-results canvas')).not.toHaveClass(/waveform-empty/)
 expect(state.waveformReads).toHaveLength(2)
 expect(errors.filter(error=>!error.includes('503'))).toEqual([])
})

test('ordered sequence waveforms load only for visible items',async({page})=>{
 const {state,errors}=await fixture(page)
 const job=state.jobs[0]
 job.result={duration:50,sequence:{contract_version:1,items:Array.from({length:50},(_,i)=>({id:`turn-${i}`,artifact_name:`item-${i}.wav`,duration:1,sample_rate:16000}))}}
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 const canvases=page.locator('.tts-sequence-results canvas')
 await expect(canvases.first()).not.toHaveClass(/waveform-empty/)
 expect(state.waveformReads.length).toBeLessThan(10)
 await canvases.last().scrollIntoViewIfNeeded()
 await expect(canvases.last()).not.toHaveClass(/waveform-empty/)
 expect(state.waveformReads.some(url=>url.includes('item-49.wav'))).toBeTruthy()
 expect(state.waveformReads.length).toBeLessThan(15)
 expect(errors).toEqual([])
})

test('changing chapter ignores a pending old waveform',async({page})=>{
 const {state,errors}=await fixture(page)
 let release!:()=>void
 const gate=new Promise<void>(resolve=>{release=resolve})
 let started!:()=>void
 const requested=new Promise<void>(resolve=>{started=resolve})
 await page.route('**/part-0.mp3/waveform',async route=>{
  started();await gate
  await route.fulfill({json:{artifact_name:'part-0.mp3',duration:1,waveform:Array(240).fill(.99)}}).catch(()=>{})
 })
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await requested
 await expect(page.locator('.document-results canvas')).toHaveClass(/waveform-empty/)
 await page.locator('.document-results select').selectOption('1')
 await expect(page.locator('.document-results canvas')).not.toHaveClass(/waveform-empty/)
 const image=await page.locator('.document-results canvas').evaluate(element=>(element as HTMLCanvasElement).toDataURL())
 release()
 await page.getByRole('tab',{name:'文本合成',exact:true}).click()
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(page.locator('.document-results select')).toHaveValue('1')
 await expect(page.locator('.document-results canvas')).not.toHaveClass(/waveform-empty/)
 expect(await page.locator('.document-results canvas').evaluate(element=>(element as HTMLCanvasElement).toDataURL())).toBe(image)
 expect(state.waveformReads).toHaveLength(1)
 expect(errors).toEqual([])
})

test('single text uses its persisted waveform without another request',async({page})=>{
 const {state,errors}=await fixture(page)
 state.jobs[0].result={duration:1,format:'wav',waveform:Array(240).fill(.4),artifacts:[{name:'speech.wav',path:'speech.wav',mime_type:'audio/wav',size_bytes:32044}]}
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(page.locator('.audio-card canvas')).not.toHaveClass(/waveform-empty/)
 expect(state.waveformReads).toEqual([])
 expect(errors).toEqual([])
})
