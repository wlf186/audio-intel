import {test,expect,systemFor} from './fixtures'
import {readingFixture} from './reading-fixture'

// Refresh references only in the Linux rendering environment documented in UI_DESIGN.
for(const [width,height] of [[390,844],[1024,720],[1440,900]]){
 test(`design references at ${width}px`,async({page},info)=>{
  await page.setViewportSize({width,height})
  await page.emulateMedia({reducedMotion:'reduce'})
  const {errors}=await readingFixture(page)
  const system=await systemFor(page)
  await page.route('**/api/v1/system',route=>route.fulfill({json:{...system,bind:'127.0.0.1:20810',offline:true,services:['asr','tts']}}))
  for(const surface of ['reader','editor','library']){
   if(surface==='reader'){
    await page.goto('/#asr')
    await page.locator('#asr-tab-results').click()
    await page.locator('.reading-blocks img').first().waitFor()
    await expect(page.locator('.media-loading')).toHaveCount(0)
   }else if(surface==='editor'){
    await page.goto('/#tts')
    await page.locator('.text-editor textarea').fill('声音在时间轴上流动。')
    await page.locator('.text-editor textarea').blur()
   }else{
    await page.goto('/#library/voiceprints')
    await expect(page.locator('.person-row')).toHaveCount(45)
   }
   await page.evaluate(()=>document.fonts.ready)
   const options={animations:'disabled' as const}
   const name=`${surface}-${width}.png`
   if(process.env.AUDIO_INTEL_VISUAL_BASELINE==='1'){
    await expect(page).toHaveScreenshot(name,{...options,maxDiffPixelRatio:.002})
   }else{
    await page.screenshot({...options,path:info.outputPath(name)})
   }
  }
  expect(errors).toEqual([])
 })
}
