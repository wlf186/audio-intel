import {test,expect} from './fixtures'
import {uxFixture} from './ux-fixtures'

for(const [width,height] of [[1440,900],[1280,720],[390,844]])test(`local automatic preview followed by a whole preview at ${width}px`,async({page},info)=>{
 await page.setViewportSize({width,height})
 const {state,errors}=await uxFixture(page)
 const video=state.job.result!.video!
 video.scenes.forEach((scene,i)=>Object.assign(scene,{start:i*45,end:(i+1)*45,frame_time:i*45+1}))
 Object.assign(video,{original_scenes:structuredClone(video.scenes),duration:2700,detector:'presentation-v4',contract_version:2})
 const requests:Array<{mode:string;time_range?:number[]}>=[]
 await page.route('**/video-scene-previews',async route=>{
  const body=route.request().postDataJSON()
  requests.push(body)
  await route.fulfill({status:202,json:{id:'auto',state:'ready',mode:'auto',video_scene_detail:body.video_scene_detail,settings:null,time_range:body.time_range||null,
   scenes:video.scenes.slice(0,body.time_range?3:60).map(scene=>({...scene,preview_url:'/api/v1/jobs/ux-video/video-frames/'+scene.image}))}})
 })
 await page.route('**/video-scene-previews/auto',route=>route.fulfill({status:204}))
 await page.goto('/#asr')
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.getByRole('button',{name:'画面检测',exact:true}).click()
 const modal=page.getByRole('dialog')
 await expect(modal.getByRole('button',{name:'自动检测',exact:true})).toHaveAttribute('aria-pressed','true')
 await modal.getByRole('group',{name:'截图详略'}).getByRole('button',{name:'精简'}).click()
 const start=modal.getByRole('button',{name:'预览此时段',exact:true})
 await expect(start).toBeInViewport({ratio:1})
 await modal.getByRole('button',{name:/调整时段/}).click()
 await modal.getByLabel('开始时间').fill('00:04:00')
 await expect(modal.getByRole('alert')).toBeVisible()
 await expect(start).toBeDisabled()
 await modal.getByLabel('开始时间').fill('00:00:00')
 await start.click()
 await expect(modal.getByRole('button',{name:'应用到整片',exact:true})).toHaveCount(0)
 await expect(modal.locator('.scene-detection-count')).toContainText('3 张截图')
 await expect(modal.locator('.scene-thumb img').first()).toBeInViewport({ratio:1})
 await page.screenshot({path:info.outputPath('preview.png')})
 await modal.locator('.scene-thumb button').first().click()
 await expect(modal.locator('.scene-focus img')).toBeVisible()
 await page.keyboard.press('ArrowRight')
 await expect(modal.locator('.scene-focus')).toContainText('2 / 3')
 await modal.getByRole('button',{name:'原始尺寸'}).click()
 await page.keyboard.press('Escape')
 await expect(modal.locator('.scene-thumb button').first()).toBeFocused()
 await modal.getByRole('button',{name:'已应用画面'}).click()
 await expect(modal.locator('.scene-detection-count')).toContainText('4 张截图')
 await modal.getByRole('button',{name:'本次预览'}).click()
 expect(requests.length).toBe(1)
 await modal.getByRole('button',{name:'按此设置预览整片'}).click()
 await expect(modal.getByRole('button',{name:'应用到整片',exact:true})).toBeEnabled()
 expect(requests).toEqual([{revision:1,mode:'auto',video_scene_detail:'concise',time_range:[0,180]},{revision:1,mode:'auto',video_scene_detail:'concise'}])
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 expect(errors).toEqual([])
})

for (const locale of ['zh-CN','en-US']) for (const width of [1440,390]) {
 test(`scene choices align and fill their groups in ${locale} at ${width}px`,async({page},info)=>{
  const {state,errors}=await uxFixture(page)
  Object.assign(state.job.result!.video!,{detector:'presentation-v6',video_scene_detail:'balanced'})
  await page.setViewportSize({width,height:900})
  await page.addInitScript(lang=>localStorage.setItem('audio-intel:ui-locale:v1',lang),locale)
  await page.goto('/#asr')
  await page.locator('#asr-tab-video').click()
  const setup=page.locator('fieldset.upload-followup').filter({has:page.locator('.scene-choice-field')})
  async function checkChoices(root:typeof setup){
   const groups=root.locator('.scene-choice-field .scene-choice')
   const dimensions=await groups.evaluateAll(nodes=>nodes.map(group=>{
    const {x,y,width}=group.getBoundingClientRect()
    const buttons=Array.from(group.querySelectorAll('button'),b=>b.getBoundingClientRect())
    return {x,y,width,filled:buttons.reduce((n,b)=>n+b.width,0),heights:buttons.map(b=>b.height)}
   }))
   expect(dimensions.length).toBeGreaterThanOrEqual(2)
   for(const item of dimensions){
    expect(Math.round(Math.abs(item.width-item.filled))).toBeLessThanOrEqual(2)
    expect(Math.min(...item.heights)).toBeGreaterThanOrEqual(44)
   }
   const choice=groups.nth(1).getByRole('button').last()
   await choice.click()
   await expect(choice).toHaveAttribute('aria-pressed','true')
   return dimensions
  }
  await checkChoices(setup)
  await setup.screenshot({path:info.outputPath('upload.png')})
  await page.locator('#asr-tab-results').click()
  await page.getByRole('button',{name:locale==='zh-CN'?'画面检测':'Picture detection',exact:true}).click()
  const controls=page.locator('.scene-setup')
  const dims=await checkChoices(controls)
  const starts=dims.map(item=>width===390?item.x:item.y)
  expect(Math.max(...starts)-Math.min(...starts)).toBeLessThanOrEqual(1)
  await controls.screenshot({path:info.outputPath('result.png')})
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  expect(errors).toEqual([])
 })
}
