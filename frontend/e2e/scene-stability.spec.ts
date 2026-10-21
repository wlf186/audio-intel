import {test,expect} from './fixtures'
import {uxFixture} from './ux-fixtures'

for(const width of [1440,390])for(const locale of ['zh-CN','en-US'])test(`stable picture placeholders and decimal duration ${width} ${locale}`,async({page},info)=>{
 await page.setViewportSize({width,height:900})
 await page.addInitScript(locale=>localStorage.setItem('audio-intel:ui-locale:v1',locale),locale)
 const {state,errors}=await uxFixture(page)
 const zh=locale==='zh-CN',empty=zh?'无稳定画面':'No stable picture'
 const range=zh?'对应时段':'Scene interval',captured=zh?'截图时刻':'Frame captured at'
 const invalidRequests:string[]=[]
 page.on('request',request=>{if(/video-frames\/(null|undefined)(?:[?\/]|$)/.test(request.url()))invalidRequests.push(request.url())})
 const proposed=[{...state.job.result!.video!.scenes[0],end:60,image:null,frame_time:null},{...state.job.result!.video!.scenes[30],end:120}]
 let controls={min_change_percent:3,min_stable_seconds:1}
 await page.route('**/api/v1/jobs/ux-video/video-scene-previews',route=>{
  controls=route.request().postDataJSON().settings
  return route.fulfill({status:202,json:{id:'stable',contract_version:2,state:'running',settings:controls}})
 })
 await page.route('**/api/v1/jobs/ux-video/video-scene-previews/stable',route=>route.request().method()==='DELETE'?route.fulfill({status:204}):route.fulfill({json:{id:'stable',contract_version:2,state:'ready',settings:controls,scenes:proposed.map(scene=>({...scene,preview_url:scene.image?`/api/v1/jobs/ux-video/video-frames/${scene.image}`:null}))}}))
 await page.route('**/api/v1/jobs/ux-video/video-scenes',route=>{
  if(route.request().method()==='PATCH'){
   Object.assign(state.job.result!.video!,{contract_version:2,scenes:proposed,automatic_scenes:proposed,scene_detection:controls,revision:2,applied_preview_id:'stable'})
   return route.fulfill({json:state.job.result})
  }
  return route.fulfill({json:{revision:state.job.result!.video!.revision,scenes:state.job.result!.video!.scenes}})
 })
 await page.goto('/#asr')
 await page.getByRole('tab',{name:zh?'任务与结果':'Tasks & results',exact:true}).click()
 await page.getByRole('button',{name:zh?'画面检测':'Picture detection',exact:true}).click()
 const modal=page.getByRole('dialog'),hold=modal.getByRole('spinbutton').nth(1)
 await expect(hold).toHaveAttribute('step','0.1')
 await hold.fill('0.6')
 await modal.getByRole('button',{name:zh?'预览检测':'Preview detection',exact:true}).click()
 const first=modal.locator('.scene-detection-grid figure').first()
 await expect(first.getByText(empty,{exact:true})).toBeVisible()
 await expect(first.locator('img')).toHaveCount(0)
 await expect(first.locator('figcaption')).toContainText(range)
 await expect(first.locator('figcaption')).not.toContainText(captured)
 await expect(modal.locator('.scene-detection-grid figure').nth(1).locator('figcaption')).toContainText(captured)
 expect(controls.min_stable_seconds).toBe(.6)
 await page.screenshot({path:info.outputPath('stable-preview.png'),fullPage:true})
 await modal.getByRole('button',{name:zh?'应用预览':'Apply preview',exact:true}).click()
 await expect(first.getByRole('checkbox')).toBeDisabled()
 await expect(first.getByText(empty,{exact:true})).toBeVisible()
 await page.keyboard.press('Escape')
 const section=page.locator('.video-section').first()
 await expect(section.getByText(empty,{exact:true})).toBeVisible()
 await expect(section.locator('img')).toHaveCount(0)
 await expect(section.locator('.video-paragraph')).toContainText('第 1 段')
 await section.locator('.video-scene-time').click()
 const track=page.locator('.timeline-scenes')
 await track.focus()
 await track.press('Home')
 await expect(page.getByRole('tooltip')).toContainText(empty)
 await expect(page.getByRole('tooltip').locator('img')).toHaveCount(0)
 await expect(page.getByRole('tooltip')).not.toContainText(captured)
 await expect(page.locator('.timeline-waveform')).toHaveAttribute('aria-valuenow','0')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 expect(invalidRequests).toEqual([])
 expect(errors).toEqual([])
 await page.screenshot({path:info.outputPath('stable-reader.png'),fullPage:true})
 await page.keyboard.press('Escape')
 await page.reload()
 await expect(page.locator('.video-section').first().getByText(empty,{exact:true})).toBeVisible()
 await page.getByRole('button',{name:zh?'画面检测':'Picture detection',exact:true}).click()
 await expect(hold).toHaveValue('0.6')
 expect(invalidRequests).toEqual([])
 expect(errors).toEqual([])
})
