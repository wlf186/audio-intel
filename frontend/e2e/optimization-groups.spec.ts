import {test,expect} from './fixtures'
import {uxFixture} from './ux-fixtures'

function group(index:number,count=1){
 const sources=Array.from({length:count},(_,offset)=>({segment_id:index*8+offset,text:`原始片段${index}-${offset+1}，那个那个需要一起整理。`,start_offset:0,end_offset:24,start:index*16+offset*2,end:index*16+offset*2+2}))
 return{id:`g${index}`,ordinal:index,speaker:'s0',speaker_label:'Mike',start:sources[0].start,end:sources.at(-1)!.end,original_text:sources.map(source=>source.text).join(''),text:`第${index}组整理后的连贯正文。`,sources,segment_ids:sources.map(source=>source.segment_id),merged_source_count:count,scene_ids:[] as string[],review_outcome:'pass',review_status:null as string|null,review_details:null as unknown,generation_method:'model'}
}
for(const width of [1280,1440,1920,390])test(`grouped reading and source search ${width}`,async({page},info)=>{
 await page.setViewportSize({width,height:900})
 const {state,errors}=await uxFixture(page)
 const paragraphs=[{...group(0,8),scene_ids:['scene-0']},{...group(1),scene_ids:['scene-1']}]
 const run={id:'v7',state:'succeeded',phase:'complete',published:true,model:'qwen3.5:4b',rewrite_mode:'editorial',total_units:2,completed_units:2,progress:1,revision:1,review_state:'completed'}
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 await page.route('**/transcript-optimization/result?*',route=>route.fulfill({json:{prompt_version:7,run_id:run.id,complete:true,model:run.model,paragraphs,scenes:state.job.result!.video!.scenes.slice(0,2)}}))
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.getByRole('button',{name:'对照',exact:true}).click()
 const first=page.locator('[data-reader-id="g0"]')
 await expect(first).toContainText('来自 8 条原文')
 await expect(first).toContainText('原始片段0-8')
 await expect(first.locator('.optimization-source')).toHaveCount(0)
 await page.getByRole('checkbox',{name:'来源明细',exact:true}).check()
 await expect(first.locator('.optimization-source')).toHaveCount(8)
 const search=page.getByPlaceholder('搜索两侧内容')
 await search.fill('整理。原始片段0-4')
 await expect(first.locator('mark')).toHaveText('整理。原始片段0-4')
 await search.fill('原始片段0-7')
 await expect(page.locator('.optimization-paragraph')).toHaveCount(1)
 await page.getByRole('button',{name:'优化稿',exact:true}).click()
 // Source-only matches must not produce invisible matches in draft-only reading.
 await expect(page.locator('.optimization-paragraph')).toHaveCount(0)
 await page.getByRole('button',{name:'清除筛选',exact:true}).click()
 await expect(page.locator('.optimization-paragraph')).toHaveCount(2)
 await page.getByRole('button',{name:'对照',exact:true}).click()
 await expect(page.getByLabel('显示改动',{exact:true})).toBeChecked()
 await expect(page.locator('[data-scene-id="scene-1"] .optimization-paragraph')).toHaveCount(1)
 await page.getByRole('button',{name:'文本阅读',exact:true}).click()
 await expect(page.locator('.optimization-reader .video-frame')).toHaveCount(0)
 await page.screenshot({path:info.outputPath('grouped-comparison.png')})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 expect(errors).toEqual([])
})

test('unified checking pauses and resumes without publishing a draft',async({page})=>{
 const {errors}=await uxFixture(page)
 const run={id:'v7',state:'running',phase:'review',published:false,model:'qwen3.5:9b',total_units:1,completed_units:1,total_chars:100,completed_chars:100,progress:1,revision:1,review_state:'running',reviewed_parts:0,total_parts:1}
 const requests:unknown[]=[]
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:null,candidate:run}}))
 for(const action of ['pause','resume'])await page.route(`**/transcript-optimization/${action}`,route=>{requests.push(route.request().postDataJSON());run.state=action==='pause'?'paused':'running';run.revision++;return route.fulfill({json:{enabled:true,published:null,candidate:run}})})
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(page.getByRole('button',{name:'优化稿',exact:true})).toBeDisabled()
 await page.getByRole('button',{name:'暂停',exact:true}).click()
 await page.getByRole('button',{name:'继续优化',exact:true}).click()
 expect(requests).toEqual([{run_id:'v7',phase:'workflow'},{run_id:'v7',phase:'workflow'}])
 await expect(page.getByRole('button',{name:'优化稿',exact:true})).toBeDisabled()
 expect(errors).toEqual([])
})

for(const sourceAction of ['listen','scroll'])test(`source interval navigation restores the containing merged group by ${sourceAction}`,async({page})=>{
 const {errors}=await uxFixture(page)
 const paragraphs=[group(0,8),group(1,8),group(2,8)]
 const run={id:'v7',state:'succeeded',published:true,model:'qwen3.5:4b',total_units:3,completed_units:3,progress:1,revision:1,review_state:'completed'}
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 await page.route('**/transcript-optimization/result?*',route=>route.fulfill({json:{prompt_version:7,run_id:run.id,complete:true,model:run.model,paragraphs,scenes:[]}}))
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.getByRole('button',{name:'文本阅读',exact:true}).click()
 const source=page.locator('.segments [data-reader-id="6"]')
 await source.scrollIntoViewIfNeeded()
 await page.locator('.segments').evaluate(root=>{const row=root.querySelector<HTMLElement>('[data-reader-id="6"]')!;root.scrollTop+=row.getBoundingClientRect().top-root.getBoundingClientRect().top})
 await page.getByRole('button',{name:'对照',exact:true}).click()
 const merged=page.locator('[data-reader-id="g0"]')
 await expect(merged).toBeInViewport()
 await page.getByRole('checkbox',{name:'来源明细',exact:true}).check()
 await expect(merged.locator('.optimization-source')).toHaveCount(8)
 if(sourceAction==='listen')await merged.getByRole('button',{name:'回听原文 00:00:12',exact:true}).click()
 else await page.locator('.optimization-reader').evaluate(root=>{const row=root.querySelector<HTMLElement>('[data-reader-id="g0"] [data-source-id="6"]')!;root.scrollTop+=row.getBoundingClientRect().top-root.getBoundingClientRect().top})
 await page.getByRole('button',{name:'原文',exact:true}).click()
 await expect(source).toBeInViewport()
 expect(errors).toEqual([])
})

test('check notices preserve prose with no separate review action',async({page})=>{
 const {errors}=await uxFixture(page)
 const paragraphs=[{...group(0),review_outcome:'uncertain'},{...group(1),review_outcome:'unavailable'},{...group(2),review_outcome:'fact',review_details:{stage:'review',issues:[{source_quote:'原始',candidate_quote:'连贯',reason:'请核对含义'}]}}]
 const run={id:'v7',state:'succeeded',published:true,model:'qwen3.5:4b',progress:1,revision:1,review_state:'completed',uncertain_units:1,unavailable_units:1,needs_review_units:1}
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 await page.route('**/transcript-optimization/result?*',route=>route.fulfill({json:{prompt_version:7,run_id:run.id,complete:true,model:run.model,paragraphs,scenes:[]}}))
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.getByRole('button',{name:'对照',exact:true}).click()
 await expect(page.locator('.optimization-paragraph')).toHaveCount(3)
 await expect(page.locator('.optimization-review-badge')).toHaveCount(1)
 await page.locator('[data-reader-id="g1"] summary').click()
 await expect(page.locator('[data-reader-id="g1"]')).toContainText('正文已保留')
 await expect(page.getByRole('button',{name:/继续复核|重试未能复核/})).toHaveCount(0)
 await page.getByLabel('段落筛选',{exact:true}).selectOption('review')
 await expect(page.locator('.optimization-paragraph')).toHaveCount(1)
 await expect(page.locator('.optimization-paragraph')).toContainText('第2组整理后的连贯正文。')
 expect(errors).toEqual([])
})

test('regeneration reads and exports the old published draft until complete',async({page})=>{
 const {state,errors}=await uxFixture(page)
 const published={id:'published',state:'succeeded',published:true,model:'qwen3.5:9b',progress:1,revision:1,review_state:'completed'}
 const candidate={id:'candidate',state:'running',phase:'review',published:false,model:'qwen3.5:4b',total_units:2,completed_units:2,total_chars:100,completed_chars:100,progress:1,revision:1,reviewed_parts:0,total_parts:2}
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published,candidate}}))
 const requested:string[]=[]
 await page.route('**/transcript-optimization/result?*',route=>{requested.push(new URL(route.request().url()).searchParams.get('run_id')!);return route.fulfill({json:{prompt_version:7,run_id:published.id,complete:true,model:published.model,paragraphs:[{...group(0),text:'上一版完整正文。'}],scenes:[]}})})
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.getByRole('button',{name:'优化稿',exact:true}).click()
 await expect(page.locator('.optimization-reader')).toContainText('上一版完整正文。')
 await expect(page.getByRole('button',{name:'预览本次生成',exact:true})).toHaveCount(0)
 await page.getByRole('button',{name:'导出转写',exact:true}).click()
 const dialog=page.getByRole('dialog')
 await expect(dialog).toContainText('上一版成功稿')
 await dialog.getByRole('button',{name:'下载',exact:true}).click()
 await expect.poll(()=>state.exports.at(-1)).toMatchObject({text_version:'optimized',optimization_id:'published'})
 expect(requested.every(id=>id==='published')).toBeTruthy()
 expect(errors).toEqual([])
})
