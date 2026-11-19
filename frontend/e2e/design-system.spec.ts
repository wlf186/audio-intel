import {test,expect} from './fixtures'
import {readingFixture} from './reading-fixture'

for(const width of [1024,1199,1200,1280])for(const locale of ['zh-CN','en-US']){
 test(`compact desktop keeps the reader visible at ${width}px in ${locale}`,async({page})=>{
  await page.setViewportSize({width,height:720})
  await page.addInitScript(lang=>localStorage.setItem('audio-intel:ui-locale:v1',lang),locale)
  await readingFixture(page)
  await page.goto('/#asr')
  await page.locator('#asr-tab-results').click()
  const reader=page.locator('.reading-blocks')
  await reader.locator('img').first().waitFor()
  await expect(page.locator('.media-loading')).toHaveCount(0)
  for(const selected of [false,true]){
   if(selected)await reader.getByRole('checkbox').first().check()
   const {visible,panel}=await reader.evaluate(node=>{
    const bounds=node.getBoundingClientRect()
    let top=Math.max(0,bounds.top), bottom=Math.min(innerHeight,bounds.bottom)
    for(let parent=node.parentElement;parent;parent=parent.parentElement){
     if(!/auto|scroll|hidden|clip/.test(getComputedStyle(parent).overflowY))continue
     const box=parent.getBoundingClientRect()
     top=Math.max(top,box.top)
     bottom=Math.min(bottom,box.bottom)
    }
    return {visible:bottom-top,panel:node.closest('.result-panel')!.getBoundingClientRect().height}
   })
   expect(visible).toBeGreaterThanOrEqual(360)
   expect(visible/panel).toBeGreaterThanOrEqual(.6)
  }
 })
}

test('history failure retries without claiming an empty library',async({page})=>{
 let failed=true
 await page.route(url=>url.pathname==='/api/v1/jobs'&&url.searchParams.get('limit')==='25',route=>
  failed?route.fulfill({status:503,json:{detail:'History unavailable'}}):route.fulfill({json:{items:[],total:0,count:0,has_more:false}}))
 await page.goto('/#jobs')
 const alert=page.locator('.jobs-page [role=alert]')
 await expect(alert).toContainText('History unavailable')
 await expect(page.locator('.jobs-empty')).toHaveCount(0)
 await expect(page.locator('.jobs-summary')).toBeEmpty()
 failed=false
 await alert.getByRole('button').click()
 await expect(page.locator('.jobs-empty')).toBeVisible()
 await expect(alert).toHaveCount(0)
})
