import {test,expect} from './fixtures'
import {uxFixture} from './ux-fixtures'

for(const width of [1440,390])test(`retained fine insights expand and remain searchable at `+width,async({page},info)=>{
 await page.setViewportSize({width,height:900})
 const {errors}=await uxFixture(page)
 const summary={version:2,state:'completed',revision:1,counts:{group:{total:3,processed:3,ready:3,skipped:0,failed:0},scene:{total:0,processed:0,ready:0,skipped:0,failed:0}}}
 const run={id:'f'.repeat(32),state:'succeeded',published:true,model:'qwen3.5:9b',total_units:1,completed_units:1,progress:1,revision:1,review_state:'completed',summary_status:summary}
 const details=[{id:'d:g1',start:0,end:4,state:'ready',sentences:['旧值会被轻量刷新。']},{id:'d:g2',start:4,end:10,state:'ready',sentences:['回滚只恢复代码版本。'],generation_method:'source_excerpt',quality_notice:'source_excerpt'}]
 const paragraph={id:'packet-g1',ordinal:0,speaker:'s0',speaker_label:'Mike',start:0,end:10,original_text:'同一人的连续来源。',text:'整理后的完整正文。',sources:[],segment_ids:[0,1],scene_ids:[],group_summary:{state:'ready',sentences:['整体小结。'],subgroups:details}}
 const groups={'0':{state:'ready',sentences:['整体小结。']},'d:g1':{state:'ready',sentences:['财务复核需要原始凭证。']},'d:g2':details[1]}
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 await page.route('**/transcript-optimization/result?*',route=>route.fulfill({json:{run_id:run.id,complete:true,model:run.model,paragraphs:[paragraph],scenes:[]}}))
 await page.route('**/transcript-optimization/summaries?*',route=>route.fulfill({json:{run_id:run.id,summary_status:summary,groups,scenes:{}}}))
 await page.goto('/#asr')
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.getByRole('button',{name:'对照',exact:true}).click()
 const expand=page.getByRole('button',{name:'分段小结（2）',exact:true})
 await expect(expand).toHaveAttribute('aria-expanded','false')
 expect((await expand.boundingBox())!.height).toBeGreaterThanOrEqual(44)
 await expand.click()
 await expect(page.locator('.optimization-fine-insight')).toHaveCount(2)
 await expect(page.locator('.optimization-fine-insights')).toContainText('财务复核需要原始凭证。')
 await expect(page.locator('.optimization-fine-insights')).toContainText('原文要点')
 await expect(page.locator('.optimization-fine-insight .optimization-time').last()).toHaveText('00:00:04–00:00:10')
 await expand.click()
 await expect(page.locator('.optimization-fine-insight')).toHaveCount(0)
 const search=page.getByPlaceholder('搜索两侧内容')
 await search.fill('原始凭证')
 await expect(page.locator('.optimization-paragraph')).toHaveCount(1)
 await expect(expand).toHaveAttribute('aria-expanded','true')
 await expect(page.locator('.optimization-fine-insight mark')).toHaveText('原始凭证')
 await page.getByRole('button',{name:'下一处',exact:true}).click()
 await expect(page.locator('.optimization-fine-insight mark.search-current')).toBeVisible()
 await page.screenshot({path:info.outputPath('fine-insights-search.png')})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 expect(await page.locator('.asr-workspace').evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBeTruthy()
 expect(errors).toEqual([])
})
