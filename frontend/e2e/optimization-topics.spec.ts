import {test,expect} from './fixtures'
import {uxFixture} from './ux-fixtures'

for(const width of [1440,390])test(`topic outline loads once, retries and seeks at ${width}`,async({page},info)=>{
 await page.setViewportSize({width,height:900})
 const {errors}=await uxFixture(page)
 const run={id:'e'.repeat(32),state:'succeeded',published:true,model:'qwen3.5:9b',total_units:1,completed_units:1,progress:1,revision:1,review_state:'completed',topic_state:'completed',topic_revision:1,calls_limit:100}
 let loads=0
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 await page.route('**/transcript-optimization/topics?*',async route=>{
  loads++
  if(loads===1){await route.abort('failed');return}
  await route.fulfill({json:{run_id:run.id,version:1,topic_state:'completed',topic_revision:1,error:null,outline:{version:1,groups:[{topic_id:'t1',title:'预算与上线',summary:'讨论预算和上线要求。',primary_item_ids:['scene-1'],related_item_ids:[]}],items:[{id:'scene-1',kind:'scene',title:'上线要求',number:2,start:2,end:4,primary_topic_id:'t1',related_topic_ids:[]}]},result:{no_substantive_content:false,unclassified:[],topics:[{id:'t1',title:'预算与上线',summary:'讨论预算和上线要求。',ranges:[{start:2,end:5,timing_precision:'segment',source_refs:[]},{start:8,end:10,timing_precision:'segment',source_refs:[]}]}]}}})
 })
 await page.goto('/#asr')
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 const directory=page.getByRole('button',{name:'议题与画面目录',exact:true})
 await expect(directory).toBeVisible()
 await expect.poll(()=>loads).toBe(1)
 await directory.click()
 await expect(page.getByRole('navigation',{name:'议题与画面目录'}).getByRole('alert')).toBeVisible()
 expect(errors).toEqual(['Failed to load resource: net::ERR_FAILED'])
 errors.length=0 // The deliberately injected transport failure was asserted above.
 await page.getByRole('navigation',{name:'议题与画面目录'}).getByRole('button',{name:'重试',exact:true}).click()
 const seek=page.getByRole('navigation',{name:'议题与画面目录'}).getByRole('button',{name:'2. 上线要求',exact:true})
 await expect(seek).toBeVisible()
 expect((await seek.boundingBox())!.height).toBeGreaterThanOrEqual(44)
 await seek.click()
 await expect.poll(()=>page.locator('.result-panel audio').evaluate((audio:HTMLAudioElement)=>audio.currentTime)).toBeCloseTo(2,0)
 if(await directory.getAttribute('aria-expanded')==='false')await directory.click()
 await page.locator('.topic-directory-tools').getByRole('button',{name:'原文依据',exact:true}).click()
 await expect(page.locator('.topic-directory').getByRole('heading',{name:'预算与上线',exact:true})).toBeVisible()
 await page.locator('.topic-range summary').first().click()
 await expect(page.locator('.topic-range details').first()).toHaveAttribute('open','')
 let preflights=0
 await page.route('**/transcript-optimization/topics/estimate',route=>{
  preflights++
  const body=route.request().postDataJSON()
  expect(body.run_id).toBe(run.id)
  expect(body.regenerate).toBe(true)
  return route.fulfill({json:{estimated_calls:2,minimum_limit:3,recommended_limit:20,fingerprint:'a'.repeat(64)}})
 })
 await page.locator('.topic-directory').getByRole('button',{name:'重新识别议题',exact:true}).click()
 const modal=page.getByRole('dialog',{name:'议题目录',exact:true})
 await expect(modal.getByRole('spinbutton')).toHaveValue('100')
 expect((await modal.getByRole('spinbutton').boundingBox())!.height).toBeGreaterThanOrEqual(44)
 await page.route('**/transcript-optimization/topics',route=>{
  const body=route.request().postDataJSON()
  expect(route.request().headers()['idempotency-key']).toBeTruthy()
  expect(body.regenerate).toBe(true)
  expect(body.max_llm_calls).toBe(100)
  run.topic_state='queued'
  return route.fulfill({status:202,json:{enabled:true,published:run,candidate:null}})
 })
 await modal.getByRole('button',{name:'开始识别',exact:true}).click()
 await expect(modal).toHaveCount(0)
 await expect(page.locator('.topic-directory')).toContainText('正在识别议题')
 expect(preflights).toBe(1)
 await page.screenshot({path:info.outputPath('topic-directory.png')})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 // The workspace clips decorative overflow; visible controls remain inside the viewport.
 expect(errors).toEqual([])
})

for(const width of [1440,390])test(`topics start from existing toolbar without an empty directory at ${width}`,async({page})=>{
 await page.setViewportSize({width,height:900})
 const {errors}=await uxFixture(page)
 const run={id:'d'.repeat(32),state:'succeeded',published:true,model:'test',total_units:1,completed_units:1,progress:1,revision:1,topic_state:'not_requested',calls_limit:100}
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 await page.route('**/transcript-optimization/topics/estimate',route=>route.fulfill({json:{estimated_calls:2,minimum_limit:2,recommended_limit:10,fingerprint:'a'.repeat(64)}}))
 await page.route('**/transcript-optimization/topics',route=>{
  expect(route.request().postDataJSON().regenerate).toBe(false)
  run.topic_state='queued'
  return route.fulfill({status:202,json:{enabled:true,published:run,candidate:null}})
 })
 await page.route('**/transcript-optimization/topics?*',route=>route.fulfill({json:{run_id:run.id,result:null,outline:null}}))
 await page.goto('/#asr')
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(page.getByRole('button',{name:'议题与画面目录',exact:true})).toHaveCount(0)
 const generate=page.getByRole('button',{name:'补生成议题',exact:true})
 await expect(generate).toBeVisible()
 expect((await generate.boundingBox())!.height).toBeGreaterThanOrEqual(44)
 await generate.click()
 const modal=page.getByRole('dialog',{name:'议题目录',exact:true})
 await expect(modal.getByRole('spinbutton')).toHaveValue('100')
 await modal.getByRole('button',{name:'开始识别',exact:true}).click()
 await expect(modal).toHaveCount(0)
 await expect(page.getByRole('button',{name:'议题与画面目录',exact:true})).toBeVisible()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 expect(errors).toEqual([])
})
