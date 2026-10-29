import {test,expect} from './fixtures'
import {uxFixture} from './ux-fixtures'

function group(index:number,count=1){
 const sources=Array.from({length:count},(_,offset)=>({segment_id:index*8+offset,text:`原始片段${index}-${offset+1}，那个那个需要一起整理。`,start_offset:0,end_offset:24,start:index*16+offset*2,end:index*16+offset*2+2}))
 return{id:`g${index}`,ordinal:index,speaker:'s0',speaker_label:'Mike',start:sources[0].start,end:sources.at(-1)!.end,original_text:sources.map(source=>source.text).join(''),text:`第${index}组整理后的连贯正文。`,sources,segment_ids:sources.map(source=>source.segment_id),merged_source_count:count,scene_ids:[] as string[],review_outcome:'pending',review_status:null as string|null,review_details:null as unknown,generation_method:'model'}
}
for(const width of [1280,1440,1920,390])test(`grouped reading and source search ${width}`,async({page},info)=>{
 await page.setViewportSize({width,height:900})
 const {state,errors}=await uxFixture(page)
 const paragraphs=[{...group(0,8),scene_ids:['scene-0','scene-1']},group(1)]
 const run={id:'v6',state:'succeeded',published:true,model:'qwen3.5:4b',rewrite_mode:'editorial',total_units:2,completed_units:2,progress:1,revision:1,review_state:'running',reviewed_units:0,review_total_units:2}
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 await page.route('**/transcript-optimization/result?*',route=>route.fulfill({json:{prompt_version:6,run_id:'v6',complete:true,model:run.model,review_state:run.review_state,paragraphs,scenes:state.job.result!.video!.scenes.slice(0,2).map(scene=>({...scene,paragraphs:[]}))}}))
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.locator('.optimization-modes').getByRole('button',{name:'对照',exact:true}).click()
 const first=page.locator('.optimization-paragraph[data-reader-id="g0"]')
 await expect(first).toHaveCount(1)
 await expect(first).toContainText('合并 8 段')
 await expect(first.locator('.optimization-source')).toHaveCount(3)
 await expect(first.locator('del,ins')).toHaveCount(0)
 await first.getByRole('button',{name:'画面 2',exact:true}).click()
 const linked=page.locator('.optimization-scene[data-scene-id="scene-1"] .optimization-source-link')
 await expect(linked).toBeInViewport()
 await linked.click()
 await expect(first).toBeInViewport()
 await expect(page.locator('.optimization-paragraph[data-reader-id="g0"]')).toHaveCount(1)
 await first.getByRole('button',{name:'展开全部 8 条原文'}).click()
 await expect(first.locator('.optimization-source')).toHaveCount(8)
 await first.getByRole('button',{name:'收起原文',exact:true}).click()
 const search=page.getByPlaceholder('搜索转写内容')
 for(const query of ['整理。原始片段0-4','整理。原始片段']){
  await search.fill(query)
  await expect(first.locator('.optimization-source')).toHaveCount(8)
 }
 await search.fill('')
 await expect(first.locator('.optimization-source')).toHaveCount(3)
 await page.getByLabel('显示改动',{exact:true}).check()
 await expect(first.locator('del')).not.toHaveCount(0)
 await search.fill('原始片段0-7')
 await expect(first.locator('.optimization-source')).toHaveCount(8)
 await expect(page.locator('.optimization-paragraph')).toHaveCount(1)
 await page.locator('.optimization-modes').getByRole('button',{name:'优化稿',exact:true}).click()
 await expect(page.locator('.optimization-paragraph')).toHaveCount(1)
 await expect(page.locator('.optimization-reader')).toContainText('第0组整理后的连贯正文。')
 await search.fill('')
 await expect(page.locator('.optimization-scene[data-scene-id="scene-1"]')).not.toContainText('此画面没有可保留的优化正文')
 await expect(linked).toHaveCount(1)
 await page.locator('.optimization-modes').getByRole('button',{name:'对照',exact:true}).click()
 await expect(page.getByLabel('显示改动',{exact:true})).toBeChecked()
 await search.fill('')
 await page.getByLabel('显示改动',{exact:true}).uncheck()
 await page.getByRole('button',{name:'文本阅读',exact:true}).click()
 await expect(page.locator('.optimization-reader .video-frame')).toHaveCount(0)
 await page.screenshot({path:info.outputPath('grouped-comparison.png'),fullPage:false})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 expect(errors).toEqual([])
})

test('review phase actions keep a completed draft exportable',async({page})=>{
 const {state,errors}=await uxFixture(page)
 const run={id:'v6',state:'succeeded',published:true,model:'qwen3.5:9b',total_units:1,completed_units:1,progress:1,revision:1,review_state:'running',reviewed_units:0,review_total_units:1}
 const requests:unknown[]=[]
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 for(const action of ['pause','resume'])await page.route(`**/transcript-optimization/${action}`,route=>{requests.push(route.request().postDataJSON());run.review_state=action==='pause'?'paused':'running';run.revision++;return route.fulfill({json:{enabled:true,published:run,candidate:null}})})
 await page.route('**/transcript-optimization/result?*',route=>route.fulfill({json:{prompt_version:6,run_id:'v6',complete:true,model:run.model,review_state:run.review_state,paragraphs:[group(0)],scenes:[]}}))
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.locator('.optimization-modes').getByRole('button',{name:'优化稿',exact:true}).click()
 await page.getByRole('button',{name:'暂停复核',exact:true}).click()
 await expect(page.locator('.optimization-review-progress')).toContainText('复核已暂停')
 await page.getByRole('button',{name:'继续复核',exact:true}).click()
 expect(requests).toEqual([{run_id:'v6',phase:'review'},{run_id:'v6',phase:'review'}])
 await page.getByRole('button',{name:'导出转写',exact:true}).click()
 await page.getByRole('dialog').getByRole('button',{name:'下载',exact:true}).click()
 await expect.poll(()=>state.exports.at(-1)).toMatchObject({text_version:'optimized',optimization_id:'v6'})
 expect(errors).toEqual([])
})

test('review revision preserves grouped reading and expanded sources',async({page})=>{
 await page.clock.install()
 const {errors}=await uxFixture(page)
 const paragraphs=Array.from({length:50},(_,index)=>group(index,4))
 const run={id:'v6',state:'succeeded',published:true,model:'qwen3.5:4b',total_units:50,completed_units:50,progress:1,revision:1,review_state:'running',reviewed_units:0,review_total_units:50}
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 await page.route('**/transcript-optimization/result?*',route=>route.fulfill({json:{prompt_version:6,run_id:'v6',complete:true,model:run.model,review_state:run.review_state,paragraphs,scenes:[]}}))
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.locator('.optimization-modes').getByRole('button',{name:'对照',exact:true}).click()
 const target=page.locator('.optimization-paragraph[data-reader-id="g35"]'),reader=page.locator('.optimization-reader')
 await target.scrollIntoViewIfNeeded();await target.getByRole('button',{name:'展开全部 4 条原文'}).click()
 await page.clock.runFor(200)
 const before=await reader.evaluate(node=>node.scrollTop)
 const beforeTop=(await target.boundingBox())!.y
 paragraphs[2].review_outcome='fact';paragraphs[2].review_status='needs_review';paragraphs[2].review_details={stage:'review',issues:[{source_quote:'原始',candidate_quote:'连贯',reason:'请核对具体含义'}]}
 run.revision++;run.reviewed_units++
 await page.clock.runFor(2100)
 await expect(page.locator('.optimization-paragraph[data-reader-id="g2"]')).toContainText('事实疑点')
 await expect(target.locator('.optimization-source')).toHaveCount(4)
 await expect(target).toBeInViewport()
 expect(Math.abs(await reader.evaluate(node=>node.scrollTop)-before)).toBeLessThanOrEqual(45)
 expect(Math.abs((await target.boundingBox())!.y-beforeTop)).toBeLessThanOrEqual(1)
 expect(errors).toEqual([])
})

for(const sourceAction of ['listen','scroll'])test(`source interval navigation restores the containing merged group by ${sourceAction}`,async({page})=>{
 const {errors}=await uxFixture(page)
 const paragraphs=[group(0,8),group(1,8),group(2,8)]
 const run={id:'v6',state:'succeeded',published:true,model:'qwen3.5:4b',total_units:3,completed_units:3,progress:1,revision:1,review_state:'completed',reviewed_units:3,review_total_units:3}
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 await page.route('**/transcript-optimization/result?*',route=>route.fulfill({json:{prompt_version:6,run_id:'v6',complete:true,model:run.model,review_state:run.review_state,paragraphs,scenes:[]}}))
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.getByRole('button',{name:'文本阅读',exact:true}).click()
 const source=page.locator('.segments [data-reader-id="6"]')
 await source.scrollIntoViewIfNeeded()
 await page.locator('.segments').evaluate(root=>{const row=root.querySelector<HTMLElement>('[data-reader-id="6"]')!;root.scrollTop+=row.getBoundingClientRect().top-root.getBoundingClientRect().top})
 await page.locator('.optimization-modes').getByRole('button',{name:'对照',exact:true}).click()
 const merged=page.locator('.optimization-paragraph[data-reader-id="g0"]')
 await expect(merged).toBeInViewport()
 await expect(merged.locator('.optimization-source')).toHaveCount(8)
 if(sourceAction==='listen')await merged.getByRole('button',{name:'回听原文 00:00:12',exact:true}).click()
 else await page.locator('.optimization-reader').evaluate(root=>{const row=root.querySelector<HTMLElement>('[data-reader-id="g0"] [data-source-id="6"]')!;root.scrollTop+=row.getBoundingClientRect().top-root.getBoundingClientRect().top})
 await page.locator('.optimization-modes').getByRole('button',{name:'原文',exact:true}).click()
 await expect(source).toBeInViewport()
 expect(errors).toEqual([])
})

test('inconclusive review keeps prose readable without a fact warning',async({page})=>{
 const {errors}=await uxFixture(page)
 const paragraphs=[{...group(0),review_outcome:'uncertain'},{...group(1),review_outcome:'unavailable'}, {...group(2),review_outcome:'fact',review_status:'needs_review',review_details:{stage:'review',issues:[{source_quote:'原始片段',candidate_quote:'连贯正文',reason:'请核对含义'}]}}]
 const run={id:'v6',state:'succeeded',published:true,model:'qwen3.5:4b',total_units:3,completed_units:3,progress:1,revision:1,review_state:'unavailable',reviewed_units:3,review_total_units:3,uncertain_units:1,unavailable_units:1,needs_review_units:1}
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 await page.route('**/transcript-optimization/result?*',route=>route.fulfill({json:{prompt_version:6,run_id:'v6',complete:true,model:run.model,review_state:run.review_state,paragraphs,scenes:[]}}))
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.locator('.optimization-modes').getByRole('button',{name:'对照',exact:true}).click()
 await expect(page.locator('.optimization-paragraph')).toHaveCount(3)
 await expect(page.locator('.optimization-review-badge')).toHaveCount(1)
 await page.getByText('自动复核未确定',{exact:true}).click()
 await expect(page.getByText('自动复核未能得出明确结论，当前稿件仍可阅读和导出。',{exact:true})).toBeVisible()
 await page.getByText('自动复核暂不可用',{exact:true}).click()
 await expect(page.getByText('自动复核暂未完成，当前稿件仍可阅读和导出，可稍后继续复核。',{exact:true})).toBeVisible()
 await page.getByLabel('段落筛选',{exact:true}).selectOption('review')
 await expect(page.locator('.optimization-paragraph')).toHaveCount(1)
 await expect(page.locator('.optimization-paragraph')).toContainText('第2组整理后的连贯正文。')
 expect(errors).toEqual([])
})

test('completed reviews can retry unavailable groups without blocking the draft',async({page})=>{
 const {state,errors}=await uxFixture(page)
 const run={id:'v6',state:'succeeded',published:true,model:'qwen3.5:4b',total_units:2,completed_units:2,progress:1,revision:1,review_state:'completed',reviewed_units:2,review_total_units:2,unavailable_units:1}
 const paragraphs=[{...group(0),review_outcome:'pass'},{...group(1),review_outcome:'unavailable'}]
 let request:unknown
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 await page.route('**/transcript-optimization/result?*',route=>route.fulfill({json:{prompt_version:6,run_id:'v6',complete:true,model:run.model,review_state:run.review_state,paragraphs,scenes:[]}}))
 await page.route('**/transcript-optimization/resume',route=>{request=route.request().postDataJSON();Object.assign(run,{review_state:'queued',reviewed_units:1,unavailable_units:0,revision:2});paragraphs[1].review_outcome='pending';return route.fulfill({json:{enabled:true,published:run,candidate:null}})})
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.locator('.optimization-modes').getByRole('button',{name:'优化稿',exact:true}).click()
 await page.getByRole('button',{name:'重试未能复核的段落',exact:true}).click()
 expect(request).toEqual({run_id:'v6',phase:'review'})
 await expect(page.locator('.optimization-review-progress')).toContainText('等待后台复核')
 await expect(page.locator('.optimization-reader')).toContainText('第1组整理后的连贯正文。')
 await page.getByRole('button',{name:'导出转写',exact:true}).click()
 await page.getByRole('dialog').getByRole('button',{name:'下载',exact:true}).click()
 await expect.poll(()=>state.exports.at(-1)).toMatchObject({text_version:'optimized',optimization_id:'v6'})
 expect(errors).toEqual([])
})

test('legacy scene ownership follows the server after picture boundaries change',async({page})=>{
 const {state,errors}=await uxFixture(page)
 const paragraph={...group(0),start:1,end:4,scene_ids:['scene-0','scene-1'],review_outcome:null}
 const run={id:'legacy',state:'succeeded',published:true,model:'qwen3.5:9b',total_units:1,completed_units:1,progress:1,revision:1,review_state:null}
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 await page.route('**/transcript-optimization/result?*',route=>route.fulfill({json:{prompt_version:5,run_id:'legacy',complete:true,model:run.model,paragraphs:[paragraph],scenes:state.job.result!.video!.scenes.slice(0,2).map((scene,index)=>({...scene,paragraphs:index===1?[paragraph]:[]}))}}))
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.locator('.optimization-modes').getByRole('button',{name:'对照',exact:true}).click()
 await expect(page.locator('.optimization-scene[data-scene-id="scene-1"] .optimization-paragraph')).toHaveCount(1)
 await expect(page.locator('.optimization-scene[data-scene-id="scene-0"] .optimization-paragraph')).toHaveCount(0)
 await expect(page.locator('.optimization-source-link')).toHaveCount(0)
 expect(errors).toEqual([])
})

test('a partial regeneration keeps the published draft available for reading and export',async({page})=>{
 const {state,errors}=await uxFixture(page)
 const published={id:'published',state:'succeeded',published:true,model:'qwen3.5:9b',total_units:1,completed_units:1,progress:1,revision:1,review_state:'paused',reviewed_units:0,review_total_units:1}
 const candidate={id:'candidate',state:'running',published:false,model:'qwen3.5:4b',total_units:2,completed_units:1,total_chars:100,completed_chars:50,progress:.5,revision:1,review_state:null}
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published,candidate}}))
 await page.route('**/transcript-optimization/result?*',route=>{
  const partial=new URL(route.request().url()).searchParams.get('run_id')==='candidate'
  return route.fulfill({json:{prompt_version:6,run_id:partial?'candidate':'published',complete:!partial,model:partial?candidate.model:published.model,review_state:partial?null:published.review_state,paragraphs:[{...group(0),text:partial?'本次生成中的文字。':'上一版完整正文。'}],scenes:[]}})
 })
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.locator('.optimization-modes').getByRole('button',{name:'优化稿',exact:true}).click()
 await expect(page.locator('.optimization-reader')).toContainText('上一版完整正文。')
 await page.getByRole('button',{name:'预览本次生成',exact:true}).click()
 await expect(page.locator('.optimization-reader')).toContainText('本次生成中的文字。')
 await page.getByRole('button',{name:'导出转写',exact:true}).click()
 const dialog=page.getByRole('dialog')
 await expect(dialog).toContainText('当前稿尚未完成，可先导出原文')
 await expect(dialog.getByLabel('导出内容',{exact:true})).toHaveCount(0)
 await dialog.locator('.modal-actions').getByRole('button',{name:'关闭',exact:true}).click()
 await page.getByRole('button',{name:'查看已完成稿',exact:true}).click()
 await expect(page.locator('.optimization-reader')).toContainText('上一版完整正文。')
 await page.getByRole('button',{name:'导出转写',exact:true}).click()
 await dialog.getByRole('button',{name:'下载',exact:true}).click()
 await expect.poll(()=>state.exports.at(-1)).toMatchObject({text_version:'optimized',optimization_id:'published'})
 expect(errors).toEqual([])
})
