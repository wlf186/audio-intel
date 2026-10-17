import {capabilitiesFor,expect,test} from './fixtures'
import {expandTtsSettings} from './tts-helpers'
import path from 'node:path'

for(const width of [1440,1024,390])for(const locale of ['zh-CN','en-US']){
 test(`TTS processing switch shares saved state between text and document at ${width}px in ${locale}`,async({page})=>{
  await page.setViewportSize({width,height:900})
  const errors:string[]=[]
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())})
  if(locale==='en-US')await page.addInitScript(()=>localStorage.setItem('audio-intel:ui-locale:v1','en-US'))
  await page.route('**/api/v1/capabilities',async route=>{const body=await capabilitiesFor(page);body.events={...body.events,sse:false};return route.fulfill({json:body})})
  await page.goto('/#tts');await expandTtsSettings(page)
  await expect(page).toHaveURL(/#tts$/);await expect(page.locator('html')).toHaveAttribute('lang',locale)
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  const control=page.locator('#tts-acceleration')
  await expect(control).toBeChecked()
  await control.focus();await page.keyboard.press('Space');await expect(control).not.toBeChecked()
  await page.getByRole('tab',{name:locale==='zh-CN'?'文档合成':'Document',exact:true}).click()
  await expect(control).not.toBeChecked()
  await page.locator('.setting-toggle-copy').click();await expect(control).toBeChecked()
  const help=page.locator('.setting-toggle-row .info-tooltip button')
  await help.focus();await expect(page.getByRole('tooltip')).toContainText(locale==='zh-CN'?'不改变模型、精度或解码方式':'model')
  await page.keyboard.press('Escape');await expect(page.getByRole('tooltip')).toHaveCount(0)
  await control.uncheck();await page.reload();await expandTtsSettings(page)
  await expect(control).not.toBeChecked()
  await page.getByRole('button',{name:locale==='zh-CN'?'恢复默认配置':'Restore defaults',exact:true}).click()
  await expect(control).toBeChecked()
  await control.scrollIntoViewIfNeeded()
  for(const target of [control,help]){
   const b=(await target.boundingBox())!;expect(b.height).toBeGreaterThanOrEqual(44);expect(b.x).toBeGreaterThanOrEqual(0);expect(b.x+b.width).toBeLessThanOrEqual(width)
  }
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  const layout=await page.locator('.tts-settings').evaluate(element=>{
   const b=element.querySelector('.setting-toggle-row')!.getBoundingClientRect(),clip=element.querySelector('.tts-settings-body')!.getBoundingClientRect(),action=element.querySelector('.submission-actions')!.getBoundingClientRect()
   return Math.min(b.bottom,clip.bottom)<=action.top||Math.max(b.top,clip.top)>=action.bottom
  })
  expect(layout).toBe(true)
  await page.screenshot({path:path.resolve(`../tmp/processing-settings/tts-switch-${width}-${locale}.png`),fullPage:true})
  expect(errors).toEqual([])
 })
}
