import {expect,test,type Page} from '@playwright/test'
import type {Job,JobSummary} from '../src/lib/types'
import {expandAsrSettings,showAsrResults} from './asr-helpers'
function wave(){const b=Buffer.alloc(160044);b.write('RIFF');b.writeUInt32LE(b.length-8,4);b.write('WAVEfmt ',8);b.writeUInt32LE(16,16);b.writeUInt16LE(1,20);b.writeUInt16LE(1,22);b.writeUInt32LE(16000,24);b.writeUInt32LE(32000,28);b.writeUInt16LE(2,32);b.writeUInt16LE(16,34);b.write('data',36);b.writeUInt32LE(b.length-44,40);for(let i=44;i<b.length;i+=2)b.writeInt16LE(Math.round(Math.sin(i/15)*8000),i);return b}
async function fixture(page:Page){
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())})
 const now=new Date().toISOString()
 const old:Job={id:'asr-old',kind:'asr',state:'succeeded',stage:'completed',progress:1,display_name:'会议转写.wav',created_at:now,updated_at:now,request:{compute_device:'cpu',language:'Chinese'},result:{duration:5,language:'Chinese',timestamp_precision:'word_or_character',waveform:Array.from({length:240},(_,i)=>.1+(i%10)/15),segments:Array.from({length:220},(_,i)=>({id:i,start:i===0?0:i*2,end:i===0?5:i*2+2,speaker:'Speaker_0',speaker_label:'张三',text:`第 ${i+1} 条转写内容`,words:[{text:'转写',start:0,end:2}]})),speakers:[{id:'Speaker_0',label:'张三'}],artifacts:[{name:'transcript.txt',path:'transcript.txt',mime_type:'text/plain',size_bytes:10}]}}
 const state={jobs:[old],detailReads:[] as string[],failDetail:false,failHotwords:false,failRename:false,failJobs:false,submitted:undefined as Job|undefined}
 const summary=(j:Job):JobSummary=>{const {request:_,result:__,...v}=j;return v}
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname
  if(path==='/api/v1/capabilities'){const response=await route.fetch();const body=await response.json();body.events={...body.events,sse:false};return route.fulfill({response,json:body})}
  if(path==='/api/v1/jobs'&&state.failJobs)return route.fulfill({status:503,json:{detail:'任务列表暂不可用'}})
  if(path==='/api/v1/jobs')return route.fulfill({json:{items:state.jobs.map(summary),count:state.jobs.length,total:state.jobs.length,has_more:false}})
  if(path==='/api/v1/asr/hotword-lists')return state.failHotwords?route.fulfill({status:503,json:{detail:'Hotwords offline'}}):route.fulfill({json:{items:[{id:'medical',name:'医学术语',kind:'custom',terms:['医学'],term_count:1,created_at:now,updated_at:now}],count:1}})
  if(path==='/api/v1/asr/jobs'&&route.request().method()==='POST'){
   const job:Job={...old,id:'asr-new',state:'queued',stage:'queued',progress:0,created_at:new Date(Date.now()+10000).toISOString(),display_name:'本次转写.wav',result:undefined};state.submitted=job;state.jobs.unshift(job);return route.fulfill({status:202,json:job})
  }
  if(path.includes('/speakers/'))return state.failRename?route.fulfill({status:503,json:{detail:'重命名暂不可用'}}):route.fulfill({json:old.result})
  if(path.endsWith('/source')){const b=wave();const r=route.request().headers()['range']?.match(/bytes=(\d+)-(\d*)/);const start=r?Number(r[1]):0;const end=r&&r[2]?Number(r[2]):b.length-1;return route.fulfill({status:r?206:200,contentType:'audio/wav',headers:{'Accept-Ranges':'bytes',...(r?{'Content-Range':`bytes ${start}-${end}/${b.length}`}:{})},body:b.subarray(start,end+1)})}
  if(path.endsWith('/artifacts/transcript.txt'))return route.fulfill({contentType:'text/plain',body:'转写内容'})
  const job=state.jobs.find(j=>path===`/api/v1/jobs/${j.id}`)
  if(job){state.detailReads.push(job.id);return state.failDetail?route.fulfill({status:503,json:{detail:'详情暂不可用'}}):route.fulfill({json:job})}
  if(route.request().method()!=='GET')throw new Error(`Unexpected mutation: ${path}`)
  return route.continue()
 })
 await page.goto('/#asr');await expect(page.getByRole('tab',{name:'新建转写',exact:true})).toHaveAttribute('aria-selected','true')
 return {state,errors}
}
for(const width of [1440,1280,1024,390,720])test(`ASR fixed workspace navigation and reading at ${width}px`,async({page})=>{
 await page.setViewportSize({width,height:width===720?450:900})
 const {state,errors}=await fixture(page)
 await page.locator('input[type=file]').setInputFiles({name:'草稿.wav',mimeType:'audio/wav',buffer:wave()})
 await expect(page.locator('.asr-file-info')).toContainText('草稿.wav')
 await expect(page.locator('.asr-results')).toBeHidden();expect(state.detailReads).toEqual([])
 await page.locator('.asr-workspace').evaluate(async e=>{await Promise.all(e.getAnimations().map(a=>a.finished))})
 const tabs=page.locator('.asr-tabs');const before=await tabs.boundingBox()
 await showAsrResults(page);await expect(page.locator('.result-head')).toContainText('会议转写.wav')
 expect(state.detailReads).toEqual(['asr-old'])
 await page.locator('#main-content').evaluate(e=>e.scrollTo(0,0));await page.evaluate(()=>window.scrollTo(0,0))
 if(width<1200){const picker=await page.locator('.asr-task-picker select').boundingBox();const manage=await page.locator('.asr-manage').boundingBox();expect(Math.abs(picker!.y-manage!.y)).toBeLessThanOrEqual(1)}
 const after=await tabs.boundingBox();expect(Math.abs(after!.x-before!.x)).toBeLessThanOrEqual(1);expect(Math.abs(after!.y-before!.y)).toBeLessThanOrEqual(1)
 await page.getByRole('button',{name:'播放',exact:true}).click()
 await expect.poll(()=>page.locator('.result-panel audio').evaluate((e:HTMLAudioElement)=>e.currentTime)).toBeGreaterThan(.1)
 await page.getByRole('tab',{name:'新建转写',exact:true}).click()
 await expect.poll(()=>page.locator('.result-panel audio').evaluate((e:HTMLAudioElement)=>e.paused)).toBe(true)
 await expect(page.locator('.asr-file-info')).toContainText('草稿.wav')
 if(width===1440||width===390)await page.screenshot({path:`/tmp/asr-create-${width}.png`})
 await showAsrResults(page)
 await page.locator('.segments').evaluate(e=>e.scrollTop=180)
 await page.getByRole('tab',{name:'新建转写',exact:true}).click();await showAsrResults(page)
 await expect.poll(()=>page.locator('.segments').evaluate(e=>e.scrollTop)).toBeGreaterThanOrEqual(179)
 await page.locator('.transcript-tools input').fill('第 220 条')
 await expect(page.locator('.segments article')).toHaveCount(1)
 await page.getByRole('tab',{name:'新建转写',exact:true}).click();await showAsrResults(page)
 await expect(page.locator('.transcript-tools input')).toHaveValue('第 220 条')
 await page.locator('.transcript-tools input').fill('');await expect(page.locator('.segments article')).toHaveCount(40)
 const seek=page.locator('.wave-area canvas');await seek.focus();await page.keyboard.press('End')
 await expect.poll(()=>page.locator('.result-panel audio').evaluate((e:HTMLAudioElement)=>e.currentTime)).toBeGreaterThan(4)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 if(width===1440||width===390){await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:`/tmp/asr-results-${width}.png`})}
 await page.locator('.language-switcher select:visible').first().selectOption('en-US')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 await page.getByRole('tab',{name:'Tasks & results',exact:true}).focus();await page.keyboard.press('Home');await expect(page.getByRole('tab',{name:'New transcription',exact:true})).toHaveAttribute('aria-selected','true')
 expect(errors).toEqual([])
})
test('ASR submitted task stays selected through status updates and another completion',async({page})=>{
 test.setTimeout(60000)
 const {state,errors}=await fixture(page)
 await page.locator('input[type=file]').setInputFiles({name:'本次.wav',mimeType:'audio/wav',buffer:wave()})
 await page.getByRole('button',{name:'开始转写',exact:true}).click()
 await expect(page.getByRole('tab',{name:'新建转写'})).toHaveAttribute('aria-selected','true')
 await page.getByRole('button',{name:'查看本次任务'}).click();await expect(page.locator('.asr-task-status')).toContainText('本次转写.wav')
 state.submitted!.state='running';state.submitted!.stage='transcription';state.submitted!.progress=.5;state.submitted!.updated_at=new Date(Date.now()+1000).toISOString()
 await expect(page.locator('.asr-task-status')).toContainText('50%',{timeout:10000})
 await page.locator('.asr-recent-jobs .job-mini').filter({hasText:'会议转写.wav'}).click();await expect(page.locator('.result-head')).toContainText('会议转写.wav')
 state.submitted!.state='succeeded';state.submitted!.stage='completed';state.submitted!.progress=1;state.submitted!.result=state.jobs[1].result;state.submitted!.updated_at=new Date(Date.now()+2000).toISOString()
 await expect(page.locator('.asr-recent-jobs .job-mini').filter({hasText:'本次转写.wav'})).toContainText('已完成',{timeout:10000})
 await expect(page.locator('.result-head')).toContainText('会议转写.wav')
 state.submitted!.state='failed';state.submitted!.error_message='测试失败';state.submitted!.updated_at=new Date(Date.now()+3000).toISOString()
 await expect(page.locator('.asr-recent-jobs .job-mini').filter({hasText:'本次转写.wav'})).toContainText('失败',{timeout:10000})
 await page.locator('.asr-recent-jobs .job-mini').filter({hasText:'本次转写.wav'}).click();await expect(page.locator('.asr-task-status')).toContainText('测试失败')
 state.submitted!.state='cancelled';state.submitted!.updated_at=new Date(Date.now()+4000).toISOString();await expect(page.locator('.asr-task-status')).toContainText('已取消',{timeout:10000})
 expect(errors).toEqual([])
})
test('ASR hotword and detail failures retry locally and rename errors stay in the dialog',async({page})=>{
 const {state,errors}=await fixture(page)
 await expandAsrSettings(page)
 await page.getByRole('button',{name:'选择热词表',exact:true}).click();await page.getByRole('dialog').getByLabel('搜索热词表').fill('不存在');await expect(page.getByRole('dialog')).toContainText('没有匹配的热词表');await page.getByRole('dialog').getByLabel('搜索热词表').fill('医学');await page.getByRole('checkbox',{name:/医学术语/}).check();await page.getByRole('dialog').getByRole('button',{name:'完成',exact:true}).click()
 state.failHotwords=true;state.failDetail=true;await page.reload()
 await expandAsrSettings(page)
 await expect(page.locator('.asr-hotword-summary')).toContainText('暂不可用')
 await expect(page.getByRole('button',{name:'开始转写'})).toBeDisabled()
 await page.getByRole('button',{name:'选择热词表',exact:true}).click();await expect(page.getByRole('dialog')).not.toContainText('未配置热词表')
 state.failHotwords=false;await page.getByRole('dialog').getByRole('button',{name:'重试',exact:true}).click();await expect(page.getByRole('checkbox',{name:/医学术语/})).toBeChecked();await page.getByRole('dialog').getByRole('button',{name:'完成',exact:true}).click()
 await showAsrResults(page);await expect(page.getByRole('heading',{name:'转写结果加载失败'})).toBeVisible()
 state.failDetail=false;await page.getByRole('button',{name:'重新加载'}).click();await expect(page.locator('.result-head')).toContainText('会议转写.wav')
 state.failRename=true;await page.getByRole('button',{name:'重命名说话人 张三'}).first().click();await page.getByRole('dialog').locator('input').fill('李四');await page.getByRole('dialog').getByRole('button',{name:'保存名称'}).click()
 await expect(page.getByRole('dialog').getByRole('alert')).toContainText('重命名暂不可用')
 expect(errors.filter(e=>!e.includes('503'))).toEqual([])
})
test('ASR task list failure retries and deleting a selected task does not select another result',async({page})=>{
 const {state,errors}=await fixture(page)
 state.failJobs=true;await page.reload();await showAsrResults(page)
 await expect(page.locator('.asr-task-list')).toContainText('任务列表暂不可用')
 await expect(page.locator('.result-panel')).toBeHidden()
 state.failJobs=false;await page.locator('.asr-task-list').getByRole('button',{name:'重试',exact:true}).click()
 await expect(page.locator('.result-head')).toContainText('会议转写.wav')
 await page.route('**/api/v1/jobs/batch-delete',async route=>{
  expect(route.request().postDataJSON().job_ids).toEqual(['asr-old'])
  state.jobs=[]
  return route.fulfill({json:{requested_count:1,deleted_count:1,failed_count:0,reclaimed_bytes:10,database_reclaimed_bytes:0,database_compacted:false,deleted:[{id:'asr-old',reclaimed_bytes:10}],failed:[]}})
 })
 await page.getByRole('button',{name:'管理全部任务'}).click()
 await expect(page.getByRole('group',{name:'任务类型筛选'}).getByRole('button',{name:'ASR',exact:true})).toHaveAttribute('aria-pressed','true')
 await page.getByRole('button',{name:'永久删除任务 会议转写.wav'}).click()
 await page.getByRole('dialog').getByRole('button',{name:'永久删除',exact:true}).click()
 await expect(page.getByRole('dialog')).toHaveCount(0)
 await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:'转写工作台'}).click();await showAsrResults(page)
 await expect(page.locator('.result-panel')).toContainText('已被删除或不可用')
 await expect(page.locator('.result-head')).toHaveCount(0)
 expect(errors.filter(e=>!e.includes('503'))).toEqual([])
})
