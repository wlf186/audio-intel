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
   Object.assign(state.job.result!.video!,{scenes:proposed,automatic_scenes:proposed,scene_detection:controls,revision:2,applied_preview_id:'preview'})
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
 for(const hover of [false,true]){
  if(hover)await apply.hover()
  const contrast=await apply.evaluate(element=>{
   const style=getComputedStyle(element)
   const luminance=(color:string)=>{
    const rgb=color.match(/[\d.]+/g)!.slice(0,3).map(Number).map(value=>{const channel=value/255;return channel<=.04045?channel/12.92:((channel+.055)/1.055)**2.4})
    return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722
   }
   const foreground=luminance(style.color),background=luminance(style.backgroundColor)
   return (Math.max(foreground,background)+.05)/(Math.min(foreground,background)+.05)
  })
  expect(contrast).toBeGreaterThanOrEqual(4.5)
 }
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
 await expect(modal).toBeVisible()
 await expect(modal.getByRole('checkbox',{name:zh?'选择画面 1':'Select picture 1',exact:true})).toBeVisible()
 await page.keyboard.press('Escape')
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

for(const width of [1440,390])test(`current pictures auto-load and persist export-only visibility ${width}`,async({page},info)=>{
 await page.setViewportSize({width,height:844})
 const {state,errors}=await uxFixture(page)
 let previews=0
 await page.route('**/api/v1/jobs/ux-video/video-scene-previews',route=>{previews++;return route.fulfill({status:500,json:{detail:'Unexpected detection'}})})
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(page.getByRole('button',{name:'编辑分段',exact:true})).toHaveCount(0)
 await page.getByRole('button',{name:'画面检测',exact:true}).click()
 const modal=page.getByRole('dialog'),gallery=modal.locator('.scene-detection-grid figure'),body=modal.locator('.scene-detection-body')
 await expect(gallery.first()).toBeVisible()
 expect(previews).toBe(0)
 expect(await gallery.count()).toBeLessThan(state.job.result!.video!.scenes.length)
 await expect(modal.getByRole('button',{name:'加载更多',exact:true})).toHaveCount(0)
 for(let i=0;i<6&&await gallery.count()<state.job.result!.video!.scenes.length;i++){
  const before=await gallery.count()
  await body.evaluate(element=>element.scrollTop=element.scrollHeight)
  await expect.poll(()=>gallery.count()).toBeGreaterThan(before)
 }
 await expect(gallery).toHaveCount(state.job.result!.video!.scenes.length)
 await modal.getByRole('checkbox',{name:'选择画面 1',exact:true}).check()
 await modal.getByRole('checkbox',{name:'选择画面 2',exact:true}).check()
 await modal.getByRole('button',{name:'导出时隐藏',exact:true}).click()
 await expect(modal.locator('.scene-hidden-badge')).toHaveCount(2)
 expect(state.job.result!.video!.export_hidden_scene_ids).toEqual(state.job.result!.video!.scenes.slice(0,2).map(scene=>scene.id))
 await expect(modal.getByRole('status')).toContainText('已保存')
 await page.keyboard.press('Escape')
 await expect(page.locator('.video-section .video-frame img').first()).toBeVisible()
 await expect(page.locator('.video-section .scene-hidden-badge')).toHaveCount(2)
 await expect(page.locator('.video-section').first().getByText('导出时隐藏',{exact:true})).toBeVisible()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 await page.screenshot({path:info.outputPath('reader-hidden.png'),fullPage:true})
 await page.reload();await page.getByRole('button',{name:'画面检测',exact:true}).click()
 await expect(modal.locator('.scene-hidden-badge')).toHaveCount(2)
 await modal.getByRole('spinbutton').first().fill('5')
 await expect(modal).toContainText('应用修改后的检测参数将恢复所有已隐藏画面')
 await modal.getByRole('button',{name:'恢复默认值',exact:true}).click()
 await expect(modal.locator('.scene-hidden-badge')).toHaveCount(2)
 await modal.getByRole('checkbox',{name:'选择画面 1',exact:true}).check()
 await modal.getByRole('button',{name:'恢复导出',exact:true}).click()
 await expect(modal.locator('.scene-hidden-badge')).toHaveCount(1)
 await expect(page.locator('.video-section .scene-hidden-badge')).toHaveCount(1)
 await modal.getByRole('button',{name:'全部恢复',exact:true}).click()
 await expect(modal.locator('.scene-hidden-badge')).toHaveCount(0)
 await expect(page.locator('.video-section .scene-hidden-badge')).toHaveCount(0)
 expect(previews).toBe(0)
 const apply=modal.getByRole('button',{name:'应用预览',exact:true})
 await expect(apply).toBeInViewport()
 expect(await apply.getAttribute('class')).toBe('primary')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 await page.screenshot({path:info.outputPath('visibility.png'),fullPage:true})
 expect(errors).toEqual([])
})

test('failed visibility save keeps selection and supports retry',async({page})=>{
 const {state,errors}=await uxFixture(page)
 let fail=true
 await page.route('**/api/v1/jobs/ux-video/video-scenes',route=>{
  if(route.request().method()!=='PATCH')return route.fulfill({json:{revision:state.job.result!.video!.revision,scenes:state.job.result!.video!.scenes}})
  if(fail)return route.fulfill({status:503,json:{detail:'Visibility temporarily unavailable'}})
  state.job.result!.video!.revision++
  state.job.result!.video!.export_hidden_scene_ids=route.request().postDataJSON().export_hidden_scene_ids
  return route.fulfill({json:state.job.result})
 })
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.getByRole('button',{name:'画面检测',exact:true}).click()
 const modal=page.getByRole('dialog'),check=modal.getByRole('checkbox',{name:'选择画面 1',exact:true})
 await check.check();await modal.getByRole('button',{name:'导出时隐藏',exact:true}).click()
 await expect(modal.getByRole('alert')).toContainText('Visibility temporarily unavailable')
 await expect(check).toBeChecked();await expect(modal.locator('.scene-hidden-badge')).toHaveCount(0)
 fail=false;await modal.getByRole('button',{name:'导出时隐藏',exact:true}).click()
 await expect(modal.locator('.scene-hidden-badge')).toHaveCount(1)
 await expect(modal.getByRole('alert')).toHaveCount(0)
 expect(errors).toEqual([])
})
