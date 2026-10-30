import {test,expect} from './fixtures'
import {uxFixture} from './ux-fixtures'

for(const [width,height] of [[1280,900],[1366,768],[1440,900],[1920,1080],[390,844]])test(`automatic v2 insights need no retry ${width}x${height}`,async({page},info)=>{
 await page.setViewportSize({width,height})
 const {state,errors}=await uxFixture(page)
 const counts={group:{total:2,processed:2,ready:2,skipped:0,failed:0,keyword_ready:1,extractive_ready:1,fallback_ready:2},scene:{total:2,processed:2,ready:2,skipped:0,failed:0,keyword_ready:1,extractive_ready:1,fallback_ready:2}}
 const summary={version:2,state:'completed',revision:5,counts}
 const run={id:'d'.repeat(32),state:'succeeded',phase:'complete',published:true,model:'qwen3.5:9b',rewrite_mode:'editorial',total_units:2,completed_units:2,progress:1,revision:5,review_state:'completed',summary_status:summary}
 const paragraphs=[0,1].map(index=>({id:`v2g${index}`,ordinal:index,speaker:'s0',speaker_label:'Mike',start:index*16,end:index*16+10,original_text:`原始第${index}组完整正文。`,text:`优化第${index}组完整正文。`,sources:[],segment_ids:[index],scene_ids:[`scene-${index}`],merged_source_count:1}))
 const values={run_id:run.id,summary_status:summary,groups:{'0':{state:'ready',generation_method:'model_keywords',attempt_count:3,sentences:['财务复核 · 三千元单据']},'1':{state:'ready',generation_method:'source_excerpt',attempt_count:0,sentences:['历史单据保持原审批记录']}},scenes:{'scene-0':{state:'ready',generation_method:'model_keywords',title:'复核门槛与单据'},'scene-1':{state:'ready',generation_method:'source_excerpt',title:'保留原审批记录'}}}
 let retries=0
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 await page.route('**/transcript-optimization/result?*',route=>route.fulfill({json:{run_id:run.id,complete:true,model:run.model,paragraphs,scenes:state.job.result!.video!.scenes.slice(0,2)}}))
 await page.route('**/transcript-optimization/summaries?*',route=>route.fulfill({json:values}))
 await page.route('**/transcript-optimization/summaries/retry',route=>{retries++;return route.fulfill({status:500})})
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(page.locator('.timeline-scene-picker option').filter({hasText:'复核门槛与单据 (关键词)'})).toHaveCount(1)
 await page.getByRole('button',{name:'对照',exact:true}).click()
 await expect(page.locator('[data-reader-id="v2g0"] .optimization-group-summary small')).toHaveText('分组小结 · 关键词')
 await expect(page.locator('[data-reader-id="v2g1"] .optimization-group-summary small')).toHaveText('分组小结 · 原文要点')
 await expect(page.locator('.optimization-scene-title').first()).toContainText('关键词')
 await expect(page.locator('.optimization-status-summary')).toContainText('关键词2 · 原文要点2')
 await expect(page.getByRole('button',{name:'补试未生成小结',exact:true})).toHaveCount(0)
 const search=page.getByPlaceholder('搜索两侧内容')
 await search.fill('三千元')
 await expect(page.locator('.optimization-paragraph')).toHaveCount(1)
 await expect(page.locator('.optimization-group-summary mark')).toHaveText('三千元')
 await search.fill('')
 await page.getByRole('button',{name:'文本阅读',exact:true}).click()
 await expect(page.locator('.optimization-scene-title')).toHaveCount(2)
 await page.locator('.optimization-group-summary').first().scrollIntoViewIfNeeded()
 await expect(page.locator('.optimization-group-summary').first()).toBeVisible()
 await page.screenshot({path:info.outputPath('automatic-insights.png')})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 expect(await page.locator('.asr-workspace').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBeTruthy()
 expect(retries).toBe(0)
 expect(errors).toEqual([])
})

for(const [width,height] of [[1280,900],[1366,768],[1440,900],[1920,1080],[390,844]])test(`group summaries and scene titles ${width}x${height}`,async({page},info)=>{
 await page.setViewportSize({width,height})
 const {state,errors}=await uxFixture(page)
 const counts={group:{total:2,processed:2,ready:1,skipped:0,failed:1},scene:{total:2,processed:2,ready:2,skipped:0,failed:0}}
 const summary={version:1,state:'partial',revision:1,counts}
 const run={id:'a'.repeat(32),state:'succeeded',phase:'complete',published:true,model:'qwen3.5:9b',rewrite_mode:'editorial',total_units:2,completed_units:2,progress:1,revision:1,review_state:'completed',summary_status:summary}
 const paragraphs=[0,1].map(index=>({id:`g${index}`,ordinal:index,speaker:'s0',speaker_label:'Mike',start:index*16,end:index*16+10,original_text:`原始第${index}组完整正文。`,text:`优化第${index}组完整正文。`,sources:[],segment_ids:[index],scene_ids:[`scene-${index}`],merged_source_count:1}))
 const scenes=state.job.result!.video!.scenes.slice(0,2)
 const values={run_id:run.id,summary_status:summary,groups:{'0':{state:'ready',sentences:['营销部下周三前提交方案，否则300万预算撤销。','技术部周五完成隐私脱敏。']},'1':{state:'unavailable',sentences:[]}},scenes:{'scene-0':{state:'ready',title:'方案与预算安排'},'scene-1':{state:'ready',title:'客户数据隐私保护'}}}
 let bodyRequests=0
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 await page.route('**/transcript-optimization/result?*',route=>{bodyRequests++;return route.fulfill({json:{prompt_version:7,run_id:run.id,complete:true,model:run.model,paragraphs,scenes}})})
 await page.route('**/transcript-optimization/summaries?*',route=>route.fulfill({json:values}))
 await page.route('**/transcript-optimization/summaries/retry',route=>{
  expect(route.request().headers()['idempotency-key']).toBeTruthy()
  expect(route.request().postDataJSON().run_id).toBe(run.id)
  summary.state='completed';summary.revision++;run.revision++
  values.groups['1']={state:'ready',sentences:['周五通过测试后再上线。']}
  return route.fulfill({status:202,json:{enabled:true,published:run,candidate:null}})
 })
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 // Original mode only loads auxiliary titles, never the full optimized body.
 await expect(page.locator('.timeline-scene-picker option').filter({hasText:'客户数据隐私保护'})).toHaveCount(1)
 expect(bodyRequests).toBe(0)
 await page.getByRole('button',{name:'对照',exact:true}).click()
 const first=page.locator('[data-reader-id="g0"]')
 await expect(first.locator('.optimization-group-summary')).toHaveCount(1)
 await expect(first.locator('.optimization-pair-columns .optimization-group-summary')).toHaveCount(0)
 await expect(first.locator('.optimization-group-summary p')).toHaveCount(2)
 const search=page.getByPlaceholder('搜索两侧内容')
 await search.fill('300万')
 await expect(page.locator('.optimization-paragraph')).toHaveCount(1)
 await expect(first.locator('mark')).toHaveText('300万')
 await page.getByRole('button',{name:'下一处',exact:true}).click()
 await expect(first.locator('mark.search-current')).toHaveText('300万')
 await search.fill('客户数据隐私保护')
 await expect(page.locator('.optimization-paragraph')).toHaveCount(1)
 await expect(page.locator('.optimization-scene-title mark')).toHaveText('客户数据隐私保护')
 await page.getByRole('button',{name:'下一处',exact:true}).click()
 await expect(page.locator('.optimization-scene-title mark.search-current')).toBeVisible()
 await search.fill('')
 await expect(page.locator('.optimization-paragraph')).toHaveCount(2)
 await expect(page.locator('.optimization-reader mark')).toHaveCount(0)
 await first.scrollIntoViewIfNeeded()
 await page.screenshot({path:info.outputPath('summary-comparison-illustrated.png')})
 await page.getByRole('button',{name:'文本阅读',exact:true}).click()
 await expect(page.locator('.optimization-reader .video-frame')).toHaveCount(0)
 await expect(page.locator('.optimization-scene-title')).toHaveCount(2)
 const previousRequests=bodyRequests
 await page.getByRole('button',{name:'补试未生成小结',exact:true}).click()
 await expect(page.locator('[data-reader-id="g1"] .optimization-group-summary')).toContainText('周五通过测试后再上线。')
 expect(bodyRequests).toBe(previousRequests)
 await expect(page.getByRole('button',{name:'对照',exact:true})).toHaveAttribute('aria-pressed','true')
 await first.locator('.optimization-group-summary').scrollIntoViewIfNeeded()
 await page.screenshot({path:info.outputPath('summary-comparison-text.png')})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 expect(await page.locator('.asr-workspace').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBeTruthy()
 expect(errors).toEqual([])
})

test('summary refresh preserves filters and the scrolled group without reloading prose',async({page})=>{
 const {errors}=await uxFixture(page)
 const summary={version:1,state:'partial',revision:1,counts:{group:{total:100,processed:100,ready:99,skipped:0,failed:1},scene:{total:0,processed:0,ready:0,skipped:0,failed:0}}}
 const run={id:'b'.repeat(32),state:'succeeded',published:true,model:'qwen3.5:9b',total_units:100,completed_units:100,progress:1,revision:1,review_state:'completed',summary_status:summary}
 const paragraphs=Array.from({length:100},(_,i)=>({id:`g${i}`,ordinal:i,speaker:'s0',speaker_label:'Mike',start:i,end:i+1,original_text:`第${i}组原始资料。`,text:`第${i}组完整优化正文。`+'用于滚动阅读的细节。'.repeat(20),sources:[],segment_ids:[i],scene_ids:[]}))
 const groups:Record<string,{state:string;sentences:string[]}>=Object.fromEntries(paragraphs.map(p=>[String(p.ordinal),{state:p.ordinal===99?'unavailable':'ready',sentences:p.ordinal===99?[]:['本组包含关键任务信息。']}]))
 let bodyRequests=0
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 await page.route('**/transcript-optimization/result?*',route=>{bodyRequests++;return route.fulfill({json:{run_id:run.id,complete:true,model:run.model,paragraphs,scenes:[]}})})
 await page.route('**/transcript-optimization/summaries?*',route=>route.fulfill({json:{run_id:run.id,summary_status:summary,groups,scenes:{}}}))
 await page.route('**/transcript-optimization/summaries/retry',route=>{summary.state='completed';summary.revision++;run.revision++;groups['99']={state:'ready',sentences:['新增的小结。']};return route.fulfill({status:202,json:{enabled:true,published:run,candidate:null}})})
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.getByRole('button',{name:'文本阅读',exact:true}).click()
 await page.getByRole('button',{name:'对照',exact:true}).click()
 const search=page.getByPlaceholder('搜索两侧内容')
 await search.fill('完整优化正文')
 await expect(page.locator('[data-reader-id="g30"]')).toContainText('完整优化正文')
 await page.getByLabel('段落筛选',{exact:true}).selectOption('changed')
 const root=page.locator('.optimization-reader')
 await root.evaluate(node=>{node.querySelectorAll<HTMLElement>('[data-reader-id]').forEach(row=>row.style.contentVisibility='visible');const group=node.querySelector<HTMLElement>('[data-reader-id="g30"]')!;node.scrollTop+=group.getBoundingClientRect().top-node.getBoundingClientRect().top})
 const anchor=await root.evaluate(node=>{const top=node.getBoundingClientRect().top;const row=[...node.querySelectorAll<HTMLElement>('[data-reader-id]')].find(row=>row.getBoundingClientRect().bottom>top+1)!;return{id:row.dataset.readerId,offset:row.getBoundingClientRect().top-top}})
 const previousRequests=bodyRequests
 await page.getByRole('button',{name:'补试未生成小结',exact:true}).click()
 await expect(page.getByRole('button',{name:'补试未生成小结',exact:true})).toHaveCount(0)
 await expect(search).toHaveValue('完整优化正文')
 await expect(page.getByLabel('段落筛选',{exact:true})).toHaveValue('changed')
 await expect.poll(()=>root.evaluate((node,anchor)=>{const row=node.querySelector<HTMLElement>(`[data-reader-id="${anchor.id}"]`)!;return Math.abs(row.getBoundingClientRect().top-node.getBoundingClientRect().top-anchor.offset)},anchor)).toBeLessThan(3)
 expect(bodyRequests).toBe(previousRequests)
 expect(errors).toEqual([])
})
