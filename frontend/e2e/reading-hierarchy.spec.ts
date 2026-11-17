import {test,expect,type Page} from './fixtures'
import {uxFixture} from './ux-fixtures'
import {showAudioOptions} from './asr-helpers'

async function fixture(page:Page,audio=false){
 const f=await uxFixture(page),{state}=f
 if(audio)delete state.job.result!.video
 const run={id:'e'.repeat(32),state:'succeeded',published:true,reading_plan_version:2,model:'test',rewrite_mode:'editorial',revision:1,calls_limit:100,topic_state:'completed',topic_revision:1,summary_status:{version:2,state:'completed',revision:1,counts:{group:{total:3,processed:3,ready:3,failed:0},scene:{total:3,processed:3,ready:3,failed:0}}}}
 const items=[0,1,2].map(i=>({id:`block${i}`,kind:'block',title:`标题${i}`,number:i+1,start:i*2,end:i*2+2,primary_topic_id:i===1?'b':'a',related_topic_ids:[],occurrence_id:`occurrence${i}`,paragraph_id:`p${i}`,scene_ids:audio?[]:[`scene-${i}`],frame_numbers:audio?[]:[i+1],source_refs:[{segment_id:i,start_offset:0,end_offset:state.job.result!.segments![i].text.length,start:i*2,end:i*2+2}]}))
 const groups=items.map((item,i)=>({topic_id:item.primary_topic_id,occurrence_id:item.occurrence_id,title:i===1?'发布流程':'产品设计',summary:'',primary_item_ids:[item.id],related_item_ids:[],recurring:i===2}))
 const outline={version:2,items,groups,merged_groups:[{...groups[0],occurrence_id:'mergeda',primary_item_ids:['block0','block2']},{...groups[1],occurrence_id:'mergedb'}]}
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 await page.route('**/transcript-optimization/summaries?*',route=>route.fulfill({json:{run_id:run.id,summary_status:run.summary_status,groups:{},scenes:{},blocks:{}}}))
 await page.route('**/transcript-optimization/topics?*',route=>route.fulfill({json:{run_id:run.id,version:3,result:{topics:[],unclassified:[]},outline}}))
 await page.route('**/transcript-optimization/result?*',route=>route.fulfill({json:{run_id:run.id,reading_plan_version:2,reading_blocks:items,complete:true,paragraphs:items.map((b,i)=>({id:`p${i}`,ordinal:i,speaker:'s0',speaker_label:'Alice',start:b.start,end:b.end,original_text:'测试原文。',text:`测试原文${i}。`,segment_ids:[i],scene_ids:b.scene_ids,reading_block_id:b.id})),scenes:[]}}))
 return {...f,run}
}
for(const [width,height] of [[1280,720],[1366,768],[1440,900],[1920,1080],[1960,1104],[390,844]])test(`reader layout and navigation ${width}x${height}`,async({page},info)=>{
 await page.setViewportSize({width,height});const {state,errors,run}=await fixture(page)
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click();await page.getByRole('button',{name:'优化稿',exact:true}).click()
 await expect(page.locator('.reading-topic-heading h2').first()).toHaveText('产品设计')
 const reader=page.locator('.reading-blocks')
 await expect(reader.locator('[data-reading-block-id]').nth(1)).toHaveAttribute('data-reading-block-id','block1')
 await expect(page.getByRole('checkbox',{name:'合并重复议题',exact:true})).not.toBeChecked()
 await expect(page.locator('.timeline-toggle')).toHaveAttribute('aria-expanded','true')
 if(await page.locator('.reading-workspace').getAttribute('data-sidebar')!=='true'){
  await page.getByRole('button',{name:'议题与画面目录',exact:true}).click()
  const drawer=page.getByRole('dialog',{name:'议题与画面目录',exact:true})
  for(const summary of await drawer.locator('nav summary').all())expect((await summary.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await drawer.getByRole('button',{name:'关闭',exact:true}).click()
 }

 const controls=['.asr-enhancement-row','.optimization-filter-row','.reading-workspace-tools','.video-timeline']
 const boxes=await Promise.all(controls.map(s=>page.locator(s).boundingBox()))
 for(let a=0;a<boxes.length;a++)for(let b=a+1;b<boxes.length;b++){const x=boxes[a]!,y=boxes[b]!;expect(Math.min(x.x+x.width,y.x+y.width)-Math.max(x.x,y.x)<=1||Math.min(x.y+x.height,y.y+y.height)-Math.max(x.y,y.y)<=1).toBeTruthy()}
 await reader.scrollIntoViewIfNeeded()
 if(width>=1024){const visible=await reader.evaluate(n=>{const r=n.getBoundingClientRect(),main=n.closest('main')!.getBoundingClientRect();return Math.min(r.bottom,main.bottom)-Math.max(r.top,main.top)});expect(visible).toBeGreaterThanOrEqual(359)}
 const frame=reader.locator('img').first();await expect(frame).toBeVisible()
 if(width>=1024){expect(await frame.evaluate(n=>{const a=n.getBoundingClientRect(),b=n.closest('.reading-blocks')!.getBoundingClientRect();return a.top>=b.top-1&&a.bottom<=b.bottom+1})).toBeTruthy()}
 if(width>=1440){
  const title=page.locator('.reading-directory nav').getByRole('button',{name:'3. 标题2',exact:true});await title.click()
  const position=await reader.locator('[data-reading-block-id="block2"] h3').evaluate(n=>{const a=n.getBoundingClientRect(),b=n.closest('.reading-blocks')!.getBoundingClientRect();return {top:a.top,bottom:a.bottom,reader:b.top}})
  expect(position.top).toBeGreaterThanOrEqual(position.reader-1)
 }
 await page.getByRole('checkbox',{name:'合并重复议题',exact:true}).check()
 await expect(reader.locator('[data-reading-block-id]').nth(1)).toHaveAttribute('data-reading-block-id','block2')
 await page.getByRole('button',{name:'导出转写',exact:true}).click()
 const dialog=page.getByRole('dialog');await expect(dialog.getByRole('checkbox',{name:'合并重复议题',exact:true})).toBeChecked()
 await dialog.getByRole('checkbox',{name:'合并重复议题',exact:true}).uncheck()
 await Promise.all([page.waitForEvent('download'),dialog.getByRole('button',{name:'下载',exact:true}).click()])
 expect(state.exports.at(-1)).toMatchObject({organization:'topic',optimization_id:run.id,merge_repeated_topics:false})
 await page.reload();await expect(page.getByRole('checkbox',{name:'合并重复议题',exact:true})).toBeChecked()
 await showAudioOptions(page);await expect(page.locator('.transport-audio-menu-panel, .action-popover-panel').getByRole('link',{name:'下载原音',exact:true})).toBeVisible()
 await page.keyboard.press('Escape');await expect(page.locator('.transport-audio-menu>button')).toHaveAttribute('aria-expanded','false')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();expect(errors).toEqual([])
 await reader.scrollIntoViewIfNeeded();await page.screenshot({path:info.outputPath('reading.png')})
})
for(const width of [1440,390])test(`source and draft share chronological blocks ${width}`,async({page})=>{
 await page.setViewportSize({width,height:900});const {errors}=await fixture(page,true)
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(page.locator('.reading-blocks [data-reading-block-id]').nth(1)).toHaveAttribute('data-reading-block-id','block1')
 await page.getByRole('checkbox',{name:'合并重复议题',exact:true}).check()
 await expect(page.locator('.reading-blocks [data-reading-block-id]').nth(1)).toHaveAttribute('data-reading-block-id','block2')
 await page.getByRole('button',{name:'优化稿',exact:true}).click()
 await expect(page.locator('.reading-blocks [data-reading-block-id]').nth(1)).toHaveAttribute('data-reading-block-id','block2')
 await page.getByPlaceholder('搜索优化稿内容').fill('测试原文')
 await page.getByRole('button',{name:'下一处',exact:true}).click();await expect(page.locator('.search-current')).toBeVisible()
 expect(errors).toEqual([])
})
