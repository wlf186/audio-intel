import {test,expect} from './fixtures'
import {uxFixture} from './ux-fixtures'

for(const width of [1440,390])for(const locale of ['zh-CN','en-US'])test(`picture detection preview apply and preferences ${width} ${locale}`,async({page},info)=>{
 await page.setViewportSize({width,height:900})
 await page.addInitScript(locale=>localStorage.setItem('audio-intel:ui-locale:v1',locale),locale)
 const {state,errors}=await uxFixture(page)
 const zh=locale==='zh-CN'
 let controls={min_change_percent:3,min_stable_seconds:1}
 let reads=0,applies=0
 const proposed=[{...state.job.result!.video!.scenes[0],end:60},{...state.job.result!.video!.scenes[30],end:120}]
 await page.route('**/api/v1/jobs/ux-video/video-scene-previews',route=>{
  controls=route.request().postDataJSON().settings
  reads=0
  return route.fulfill({status:202,json:{id:'preview',state:'running',settings:controls}})
 })
 await page.route('**/api/v1/jobs/ux-video/video-scene-previews/preview',route=>{
  if(route.request().method()==='DELETE')return route.fulfill({status:204})
  reads++
  return route.fulfill({json:{id:'preview',state:reads>1?'ready':'running',settings:controls,progress:{phase:'detecting',completed:reads>1?120:60,total:120},...(reads>1?{scenes:proposed.map(s=>({...s,preview_url:`/api/v1/jobs/ux-video/video-frames/${s.image}`}))}:{})}})
 })
 await page.route('**/api/v1/jobs/ux-video/video-scenes',route=>{
  if(route.request().method()==='PATCH'){
   expect(route.request().postDataJSON().action).toBe('apply_detection')
   applies++
   Object.assign(state.job.result!.video!,{scenes:proposed,automatic_scenes:proposed,scene_detection:controls,revision:2})
   return route.fulfill({json:state.job.result})
  }
  return route.fulfill({json:{revision:state.job.result!.video!.revision,scenes:state.job.result!.video!.scenes}})
 })
 await page.goto('/#asr')
 await page.getByRole('tab',{name:zh?'任务与结果':'Tasks & results',exact:true}).click()
 await page.getByRole('button',{name:zh?'画面检测':'Picture detection',exact:true}).click()
 const modal=page.getByRole('dialog')
 const change=modal.getByRole('spinbutton',{name:zh?'最小画面变化比例（%）':'Minimum picture change (%)'})
 const hold=modal.getByRole('spinbutton',{name:zh?'新画面最短保持时间（秒）':'Minimum new-picture duration (seconds)'})
 await change.fill('4');await hold.fill('1.5')
 await modal.getByRole('button',{name:zh?'预览检测':'Preview detection',exact:true}).click()
 await expect(modal.locator('.scene-detection-count')).toContainText(zh?'预览 2 段':'Preview: 2')
 expect(applies).toBe(0)
 expect(state.job.result!.video!.revision).toBe(1)
 await change.fill('5')
 const apply=modal.getByRole('button',{name:zh?'应用预览':'Apply preview',exact:true})
 await expect(apply).toBeDisabled()
 await change.fill('4')
 await expect(apply).toBeEnabled()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 for(const button of await modal.locator('button:visible').all()){
  const box=(await button.boundingBox())!
  expect(box.width).toBeGreaterThanOrEqual(44)
  expect(box.height).toBeGreaterThanOrEqual(44)
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x+box.width).toBeLessThanOrEqual(width)
 }
 await page.screenshot({path:info.outputPath('preview.png'),fullPage:true})
 await apply.click()
 await expect(modal).not.toBeVisible()
 expect(applies).toBe(1)
 await expect.poll(()=>page.evaluate(()=>JSON.parse(localStorage.getItem('audio-intel:video-scene-detection:v1')||'null'))).toEqual({min_change_percent:4,min_stable_seconds:1.5})
 await page.getByRole('button',{name:zh?'画面检测':'Picture detection',exact:true}).click()
 await expect(change).toHaveValue('4')
 await modal.getByRole('button',{name:zh?'恢复默认值':'Restore defaults',exact:true}).click()
 await expect(change).toHaveValue('3');await expect(hold).toHaveValue('1')
 expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('audio-intel:video-scene-detection:v1')||'null'))).toEqual({min_change_percent:4,min_stable_seconds:1.5})
 await page.keyboard.press('Escape')
 await page.reload()
 await page.getByRole('button',{name:zh?'画面检测':'Picture detection',exact:true}).click()
 await expect(change).toHaveValue('4')
 expect(errors).toEqual([])
})

test('manual section replacement requires confirmation and cancellation keeps the result',async({page})=>{
 const {state,errors}=await uxFixture(page)
 state.job.result!.video!.scenes[0].frame_time=1
 let cancelled=0,applies=0
 let ready=false
 await page.route('**/api/v1/jobs/ux-video/video-scene-previews',route=>route.fulfill({status:202,json:{id:'preview',state:'running',settings:{min_change_percent:3,min_stable_seconds:1}}}))
 await page.route('**/api/v1/jobs/ux-video/video-scene-previews/preview',route=>{
  if(route.request().method()==='DELETE'){cancelled++;return route.fulfill({status:204})}
  return route.fulfill({json:{id:'preview',state:ready?'ready':'running',settings:{min_change_percent:3,min_stable_seconds:1},...(ready?{scenes:[{...state.job.result!.video!.scenes[0],preview_url:'/api/v1/jobs/ux-video/video-frames/frame-0.jpg'}]}:{})}})
 })
 await page.route('**/api/v1/jobs/ux-video/video-scenes',route=>{
  if(route.request().method()==='PATCH'){applies++;return route.fulfill({json:state.job.result})}
  return route.fulfill({json:{revision:1,scenes:state.job.result!.video!.scenes}})
 })
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.getByRole('button',{name:'画面检测',exact:true}).click()
 await page.getByRole('button',{name:'预览检测',exact:true}).click()
 await page.getByRole('button',{name:'取消检测',exact:true}).click()
 await expect.poll(()=>cancelled).toBe(1)
 expect(state.job.result!.video!.revision).toBe(1)
 ready=true
 await page.getByRole('button',{name:'预览检测',exact:true}).click()
 await expect(page.locator('.scene-detection-count')).toBeVisible()
 await expect(page.getByRole('button',{name:'应用预览',exact:true})).toBeDisabled()
 await page.getByRole('checkbox',{name:/我确认替换/}).check()
 await expect(page.getByRole('button',{name:'应用预览',exact:true})).toBeEnabled()
 await page.keyboard.press('Escape')
 await expect.poll(()=>cancelled).toBe(2)
 expect(applies).toBe(0)
 expect(errors).toEqual([])
})
