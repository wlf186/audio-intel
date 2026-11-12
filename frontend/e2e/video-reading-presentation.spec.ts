import {test,expect} from './fixtures'
import {uxFixture} from './ux-fixtures'
import {readFileSync} from 'node:fs'
const fixture=JSON.parse(readFileSync(new URL('../../tests/fixtures/video_presentation.json',import.meta.url),'utf8'))
import {hasReadingContent,pictureNumbers} from '../src/lib/videoPresentation'

test('shared picture presentation contract',()=>{
 const scenes=fixture.scenes as Parameters<typeof hasReadingContent>[0][]&{id:string}[]
 const numbers=pictureNumbers(scenes as Parameters<typeof pictureNumbers>[0])
 expect(scenes.filter(hasReadingContent).map(scene=>[scene.id,numbers.get(scene.id)??null])).toEqual(fixture.expected)
})

for(const width of [1440,390])test(`independent prose, directory, empty blocks and click-only preview ${width}`,async({page},info)=>{
 await page.setViewportSize({width,height:width===390?844:900})
 const {state,errors}=await uxFixture(page)
 state.job.result!.audio_enhancement={mode:'auto',status:'applied',reason:'noise_and_gain',playback_url:'/api/v1/jobs/ux-video/artifacts/enhanced.wav'}
 const scenes=state.job.result!.video!.scenes
 scenes.splice(4)
 scenes[0].image=null;scenes[0].frame_time=null;scenes[0].paragraphs=[]
 scenes[1].image=null;scenes[1].frame_time=null
 scenes[1].paragraphs![0].text='独立话题：此处与前一张图无关。'
 let previews=0
 await page.route('**/video-preview?*',route=>{previews++;if(previews===1)return route.fulfill({status:503,json:{detail:'Frame temporarily unavailable'}});return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="white"/></svg>'})})
 await page.addInitScript(()=>{const create=URL.createObjectURL.bind(URL),revoke=URL.revokeObjectURL.bind(URL);const counts={created:0,revoked:0};(window as unknown as {blobCounts:typeof counts}).blobCounts=counts;URL.createObjectURL=blob=>{counts.created++;return create(blob)};URL.revokeObjectURL=url=>{counts.revoked++;revoke(url)}})
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(page.locator('.timeline-scene-picker option')).toHaveCount(3)
 await expect(page.locator('.timeline-scene-picker')).toContainText('画面目录（2）')
 await expect(page.locator('[data-reader-id="scene-0"]')).toHaveCount(0)
 const text=page.locator('[data-reader-id="scene-1"]')
 await expect(text).toContainText('独立话题')
 await expect(text.locator('h3,img,.video-no-frame')).toHaveCount(0)
 await expect(page.locator('[data-reader-id="scene-2"] h3')).toHaveText('画面 1')
 expect(previews).toBe(0)
 if(width===390){await expect(page.getByRole('button',{name:'展开时间轴',exact:true})).toBeVisible();expect((await text.boundingBox())!.y).toBeLessThan(744)}
 await text.getByRole('button',{name:'查看画面',exact:true}).click()
 const modal=page.getByRole('dialog')
 await expect(modal).toContainText('不保存到截图目录或导出')
 await expect(modal.getByRole('alert')).toContainText('Frame temporarily unavailable')
 expect(previews).toBe(1)
 await modal.getByRole('button',{name:'重试',exact:true}).click()
 await expect(modal.locator('img')).toBeVisible()
 expect(previews).toBe(2)
 await page.keyboard.press('Escape');await expect(modal).toHaveCount(0)
 expect(await page.evaluate(()=>(window as unknown as {blobCounts:{created:number;revoked:number}}).blobCounts)).toEqual({created:1,revoked:1})
 await page.getByRole('button',{name:'画面检测',exact:true}).click()
 await expect(page.getByRole('dialog').locator('.scene-detection-grid figure')).toHaveCount(2)
 await page.getByRole('button',{name:'全部检测分段',exact:true}).click()
 await expect(page.getByRole('dialog').locator('.scene-detection-grid figure')).toHaveCount(4)
 await page.keyboard.press('Escape')
 await text.scrollIntoViewIfNeeded()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 expect(await text.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true)
 expect(errors).toEqual([])
 await page.screenshot({path:info.outputPath('independent-reading.png')})
})

for(const width of [1440,390])test(`optimized and comparison prose without pictures stays independent ${width}`,async({page})=>{
 await page.setViewportSize({width,height:900})
 const {state,errors}=await uxFixture(page)
 const scenes=state.job.result!.video!.scenes.slice(0,3)
 scenes[1].image=null;scenes[1].frame_time=null
 const run={id:'e'.repeat(32),state:'succeeded',phase:'complete',published:true,model:'fixture',rewrite_mode:'rewrite',total_units:2,completed_units:2,progress:1,revision:1,review_state:'completed'}
 const paragraphs=[0,1].map(index=>({id:`reading-${index}`,ordinal:index,speaker:'s0',speaker_label:'Alice',start:index*2,end:index*2+2,original_text:'原始独立内容',text:'优化独立内容',sources:[],segment_ids:[index],scene_ids:[`scene-${index}`],merged_source_count:1}))
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 await page.route('**/transcript-optimization/summaries?*',route=>route.fulfill({json:{run_id:run.id,summary_status:{version:null,state:'not_requested',revision:0,counts:{}},groups:{},scenes:{}}}))
 await page.route('**/transcript-optimization/result?*',route=>route.fulfill({json:{run_id:run.id,complete:true,model:run.model,paragraphs,scenes}}))
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 for(const mode of ['优化稿','对照']){
  await page.getByRole('button',{name:mode,exact:true}).click()
  const text=page.locator('.optimization-scene[data-scene-id="scene-1"]')
  await expect(text).toContainText('优化独立内容')
  await expect(text.locator('.video-frame,.video-no-frame')).toHaveCount(0)
  await expect(text.locator('.optimization-scene-meta')).toContainText('未配图内容')
  await expect(page.locator('.optimization-scene[data-scene-id="scene-2"] .optimization-scene-meta')).toContainText('画面 2')
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 }
 expect(errors).toEqual([])
})
