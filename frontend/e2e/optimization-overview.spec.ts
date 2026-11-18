import {test,expect} from './fixtures'
import {uxFixture} from './ux-fixtures'

for(const [width,height] of [[1440,900],[1280,720],[390,844]])test('draft overview, active budget and reading space at '+width,async({page},info)=>{
 await page.setViewportSize({width,height})
 const {state,errors}=await uxFixture(page)
 const published={id:'published',job_id:state.job.id,published:true,state:'succeeded',rewrite_mode:'editorial',model:'test',calls_limit:100,calls_used:18,total_chars:1500,completed_chars:1500,total_units:3,completed_units:3,revision:4}
 const candidate={...published,id:'candidate',published:false,state:'running',calls_used:2,completed_chars:500,error:'',budget_error:''}
 const resumeBodies:unknown[]=[]
 let processing=true
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published,candidate:processing?candidate:null}}))
 await page.route('**/transcript-optimization/usage?*',route=>{
  const old=new URL(route.request().url()).searchParams.get('run_id')===published.id
  return route.fulfill({json:{run_id:old?published.id:candidate.id,recorded:true,active_duration_ms:old?null:72500,
   timing_partial:false,calls_used:old?18:2,max_llm_calls:100,remaining_calls:old?82:98,stages:{topics:old?18:2},
   failed_or_interrupted:0,in_progress_calls:old?0:1,token_usage:{total_tokens:old?null:1250,total_recorded:old?0:1,
    prompt_tokens:old?null:1100,completion_tokens:old?null:150,input_recorded:old?0:1,output_recorded:old?0:1,attempts:old?18:2}}})
 })
 await page.route('**/transcript-optimization/pause',route=>{
  candidate.state='paused';candidate.budget_error='exhausted';candidate.error='本次调用预算已用完'
  return route.fulfill({json:{enabled:true,published,candidate}})
 })
 await page.route('**/transcript-optimization/resume',route=>{
  resumeBodies.push(route.request().postDataJSON());candidate.state='running';candidate.budget_error=''
  return route.fulfill({json:{enabled:true,published,candidate}})
 })
 await page.goto('/#asr')
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 const reader=page.locator('.reading-content .video-transcript')
 await expect(reader).toContainText('第 1 段')
 const trigger=page.getByRole('button',{name:'表达优化',exact:true})
 await expect(page.getByRole('button',{name:'优化详情',exact:true})).toHaveCount(0)
 if(width>=1024){
  const result=await reader.evaluate(el=>{
   const rect=el.getBoundingClientRect()
   let top=Math.max(rect.top,0),bottom=Math.min(rect.bottom,innerHeight)
   for(let a=el.parentElement;a;a=a.parentElement){
    if(/auto|scroll|hidden|clip/.test(getComputedStyle(a).overflowY)){
     const clip=a.getBoundingClientRect();top=Math.max(top,clip.top);bottom=Math.min(bottom,clip.bottom)
    }
   }
   return {visible:bottom-top,panel:el.closest('.result-panel')!.getBoundingClientRect().height}
  })
  expect(result.visible).toBeGreaterThanOrEqual(360)
  expect(result.visible/result.panel).toBeGreaterThanOrEqual(.6)
 }
 await trigger.focus();await trigger.press('Enter')
 const dialog=page.getByRole('dialog',{name:'表达优化'})
 await expect(dialog.getByRole('heading',{name:'表达优化',exact:true})).toBeFocused()
 const metrics=dialog.locator('.optimization-metrics')
 await expect(metrics).toContainText('已记录至少 1,250')
 await expect(metrics).toContainText('2 次')
 await dialog.getByText('用量明细',{exact:true}).click()
 await expect(dialog).toContainText('议题识别')
 await expect(dialog).toContainText('完整 Token 用量覆盖 1 / 2')
 await dialog.getByText('用量明细',{exact:true}).click()
 await dialog.getByRole('button',{name:'当前可读稿',exact:true}).click()
 await expect(metrics).toContainText('18 次')
 await expect(metrics).toContainText('历史未记录')
 await expect(metrics).toContainText('服务未返回')
 await expect(dialog.getByRole('button',{name:'查看优化稿'})).toBeVisible()
 await dialog.getByRole('button',{name:'本次优化',exact:true}).click()
 await expect(metrics).toContainText('已记录至少 1,250')
 await dialog.screenshot({path:info.outputPath('optimization-overview.png')})
 await dialog.getByRole('button',{name:'暂停',exact:true}).click()
 await dialog.getByRole('button',{name:'调整上限并继续',exact:true}).click()
 await dialog.getByLabel('本次总调用上限').fill('130')
 await dialog.getByRole('button',{name:'调整上限并继续',exact:true}).click()
 await expect(dialog).toHaveCount(0)
 expect(resumeBodies).toEqual([{run_id:'candidate',phase:'workflow',max_llm_calls:130}])
 await expect(trigger).toBeFocused()
 processing=false
 await trigger.click()
 await expect(dialog.getByRole('button',{name:'重新优化',exact:true})).toBeVisible()
 await expect(metrics).toContainText('服务未返回')
 await page.keyboard.press('Escape');await expect(trigger).toBeFocused()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 expect(errors).toEqual([])
})

test('failed draft keeps its actions and usage has loading, retry and complete states',async({page,responseGate})=>{
 const {state,errors}=await uxFixture(page)
 const run={id:'failure',job_id:state.job.id,state:'failed',published:false,model:'test',calls_limit:20,calls_used:1,error:'模型连接失败',total_chars:800,completed_chars:40,revision:2}
 await page.route('**/api/v1/jobs/*/transcript-optimization',r=>r.fulfill({json:{enabled:true,candidate:run,published:null}}))
 const gate=responseGate()
 let reads=0
 await page.route('**/transcript-optimization/usage?*',async r=>{
  if(++reads===1){await gate.hold();return r.fulfill({status:503,json:{detail:'用量暂不可用'}})}
  return r.fulfill({json:{run_id:run.id,recorded:true,calls_used:1,max_llm_calls:20,remaining_calls:19,active_duration_ms:8100,timing_partial:true,
   in_progress_calls:0,stages:{generation:1},failed_or_interrupted:1,token_usage:{total_tokens:1490,total_recorded:1,
    prompt_tokens:null,completion_tokens:null,input_recorded:0,output_recorded:0,attempts:1}}})
 })
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果'}).click()
 await page.getByRole('button',{name:'表达优化',exact:true}).click()
 const d=page.getByRole('dialog')
 await gate.arrived
 await expect(d).toContainText('正在读取本稿用量')
 await expect(d.locator('.optimization-metrics')).toHaveCount(0)
 gate.release()
 await expect(d).toContainText('用量暂不可用')
 await d.getByRole('button',{name:'重试'}).click()
 await expect(d.locator('.optimization-metrics dd').last()).toHaveText('1,490')
 await expect(d.locator('.optimization-metrics dd').first()).toContainText('已记录至少')
 await expect(d).toContainText('模型连接失败')
 await expect(d.getByRole('button',{name:'继续优化',exact:true})).toBeEnabled()
 await expect(d.getByRole('button',{name:'重新优化',exact:true})).toBeEnabled()
 expect(errors).toEqual([])
})

test('long English controls fit on a 390px phone while a draft is paused',async({page})=>{
 await page.setViewportSize({width:390,height:844})
 await page.addInitScript(()=>localStorage.setItem('audio-intel:ui-locale:v1','en-US'))
 const {state,errors}=await uxFixture(page)
 const run={id:'mobile-run',job_id:state.job.id,model:'a-long-model-identifier-with-multiple-words',published:true,state:'succeeded',rewrite_mode:'editorial',calls_limit:100,revision:1}
 await page.route('**/api/v1/jobs/*/transcript-optimization',r=>r.fulfill({json:{enabled:true,published:run,candidate:{...run,id:'candidate',published:false,state:'paused',budget_error:'exhausted'}}}))
 await page.goto('/#asr');await page.getByRole('tab',{name:'Tasks & results',exact:true}).click()
 const entry=page.getByRole('button',{name:'Expression optimization',exact:true})
 await expect(entry).toContainText('Paused')
 await entry.click()
 const dialog=page.getByRole('dialog')
 await expect(dialog.getByRole('button',{name:'Adjust limit and resume'})).toBeInViewport()
 for(const control of await dialog.locator('button:visible,summary:visible').all()){
  const box=await control.boundingBox()
  expect(box!.x).toBeGreaterThanOrEqual(0);expect(box!.x+box!.width).toBeLessThanOrEqual(390)
  expect(box!.height).toBeGreaterThanOrEqual(44)
 }
 expect(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth)).toBeTruthy()
 await page.keyboard.press('Escape')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 expect(errors).toEqual([])
})
