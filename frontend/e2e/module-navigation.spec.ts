import {test,expect} from './fixtures'
import {uxFixture} from './ux-fixtures'

for(const [width,height] of [[1280,720],[1440,800],[1920,1080],[1470,830],[1280,560],[390,844]])for(const locale of ['zh-CN','en-US'])test(`compact module navigation ${width}x${height} ${locale}`,async({page},info)=>{
 await page.setViewportSize({width,height})
 await page.addInitScript(locale=>localStorage.setItem('audio-intel:ui-locale:v1',locale),locale)
 const {errors}=await uxFixture(page)
 const zh=locale==='zh-CN'
 const labels=zh?['转写工作台','语音合成','资料库','任务记录','系统状态']:['Transcription','Speech synthesis','Library','Task history','System status']
 const keys=['asr','tts','library','jobs','system']
 await page.goto('/#asr')
 for(let index=0;index<keys.length;index++){
  await page.getByRole('navigation',{name:zh?'主导航':'Main navigation'}).getByRole('button',{name:labels[index],exact:true}).click()
  const main=page.getByRole('main',{name:labels[index],exact:true})
  await expect(main).toBeVisible()
  await expect(main.locator('h1')).toHaveCount(1)
  await expect(main.locator('h1')).toHaveClass('sr-only')
  await expect(page.locator('.asr-heading,.tts-editor>.section-title,.library-heading,.jobs-page>.page-heading,.system>.page-heading')).toHaveCount(0)
  await expect(page.getByRole('navigation',{name:zh?'主导航':'Main navigation'}).locator('[aria-current=page]')).toHaveAccessibleName(labels[index])
  await expect(page).toHaveTitle(new RegExp(index===2?(zh?'声纹库':'Voiceprint library'):labels[index]))
  if(index<3){
   const tabs=main.locator('.workspace-tabs'),first=tabs.getByRole('tab').first()
   await expect(first).toBeInViewport()
   const before=(await tabs.boundingBox())!
   await first.focus();await page.keyboard.press('End')
   await expect(tabs.getByRole('tab').last()).toHaveAttribute('aria-selected','true')
   await page.keyboard.press('Home');await expect(first).toHaveAttribute('aria-selected','true')
   expect(Math.abs((await tabs.boundingBox())!.y-before.y)).toBeLessThanOrEqual(1)
   for(const control of await tabs.getByRole('tab').all()){
    expect((await control.boundingBox())!.height).toBeGreaterThanOrEqual(44)
    expect(await control.evaluate(node=>{
     const box=node.getBoundingClientRect(),text=document.createRange()
     text.selectNodeContents(node)
     return [...text.getClientRects()].every(rect=>rect.left>=box.left+3&&rect.right<=box.right-3&&rect.top>=box.top&&rect.bottom<=box.bottom)
    })).toBe(true)
   }
  }
  if(index===2){
   await main.getByRole('button',{name:zh?'资料库工具':'Library tools',exact:true}).click()
   const dialog=page.getByRole('dialog',{name:zh?'资料库工具':'Library tools',exact:true})
   await expect(dialog).toBeVisible()
   await expect(dialog.getByRole('button',{name:zh?'导入完整包':'Import full archive',exact:true})).toBeVisible()
   await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0)
  }
  if(index===3)await expect(main.locator('.jobs-summary')).not.toBeEmpty()
  if(index===4)await expect(main.locator('.resource-grid')).toBeVisible()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  await page.screenshot({path:info.outputPath(`${keys[index]}.png`)})
  await page.screenshot({path:info.outputPath(`${keys[index]}-full.png`),fullPage:true})
 }
 expect(errors).toEqual([])
})
