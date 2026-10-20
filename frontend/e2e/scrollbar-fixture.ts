import {test,expect,type Page} from './fixtures'
import {uxFixture} from './ux-fixtures'

export async function openTimeline(page:Page){
 const fixture=await uxFixture(page)
 await page.goto('/#asr')
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(page.locator('.timeline-waveform')).toHaveAttribute('data-window-end','120')
 return fixture
}

export function scrollbarCases(classic:boolean){test.describe(classic?'classic OS scrollbars':'hidden OS scrollbars',()=>{
 for(const width of [1440,390])test(`timeline bar remains usable after idle at ${width}px @timing`,async({page},info)=>{
  await page.setViewportSize({width,height:900})
  const {errors}=await openTimeline(page)
  // Removing OS scrollbar visuals must never remove the app's navigation control.
  if(!classic)await page.addStyleTag({content:'*{scrollbar-width:none!important}*::-webkit-scrollbar{display:none!important}'})
  const bar=page.getByRole('scrollbar',{name:'横向浏览时间轴'}),thumb=page.locator('.timeline-scrollbar-thumb'),wave=page.locator('.timeline-waveform')
  await expect(bar).toHaveAttribute('aria-disabled','true')
  await expect(bar).toHaveAttribute('aria-valuetext',/放大后/)
  const full=(await bar.boundingBox())!
  expect((await thumb.boundingBox())!.width).toBeCloseTo(full.width,0)
  await page.getByRole('button',{name:'放大时间轴',exact:true}).click()
  await expect(wave).toHaveAttribute('data-window-end','60')
  await expect(bar).toHaveAttribute('aria-disabled','false')
  await bar.scrollIntoViewIfNeeded()
  const readerTop=await page.locator('.video-transcript').evaluate(node=>node.scrollTop)
  const rail=(await bar.boundingBox())!,handle=(await thumb.boundingBox())!
  expect(handle.width).toBeCloseTo(rail.width/2,0)
  if(width===390)expect(rail.height).toBeGreaterThanOrEqual(44)
  await page.mouse.move(0,0)
  await page.waitForTimeout(5200)
  await expect(thumb).toBeVisible()
  expect(await thumb.evaluate(node=>getComputedStyle(node).opacity)).toBe('1')
  // Use the actual bar after the system auto-hide interval, including capture outside it.
  await page.mouse.move(handle.x+handle.width/2,rail.y+rail.height/2);await page.mouse.down()
  await page.mouse.move(rail.x+rail.width-1,rail.y+rail.height+25,{steps:5});await page.mouse.up()
  await expect(wave).toHaveAttribute('data-window-start','60')
  await expect(wave).toHaveAttribute('aria-valuenow','0')
  expect(await page.locator('.video-transcript').evaluate(node=>node.scrollTop)).toBe(readerTop)
  // Clicking the empty track pages the window, never the audio.
  await page.mouse.click(rail.x+2,rail.y+rail.height/2)
  await expect(bar).toHaveAttribute('aria-valuenow','6')
  await bar.press('Home');await expect(bar).toHaveAttribute('aria-valuenow','0')
  await bar.press('ArrowRight');await expect(bar).toHaveAttribute('aria-valuenow','6')
  await bar.press('PageDown');await expect(bar).toHaveAttribute('aria-valuenow','60')
  await bar.press('PageUp');await expect(bar).toHaveAttribute('aria-valuenow','6')
  await bar.press('ArrowLeft');await expect(bar).toHaveAttribute('aria-valuenow','0')
  await bar.press('End');await expect(bar).toHaveAttribute('aria-valuenow','60')
  await expect(wave).toHaveAttribute('data-window-start','60')
  await page.getByRole('button',{name:'定位播放点',exact:true}).click()
  await expect(wave).toHaveAttribute('data-window-start','0')
  await expect(bar).toHaveAttribute('aria-valuenow','0')
  await page.getByRole('button',{name:'显示全片',exact:true}).click()
  await expect(bar).toHaveAttribute('aria-disabled','true')
  await bar.press('End');await expect(wave).toHaveAttribute('data-window-end','120')
  await expect(wave).toHaveAttribute('aria-valuenow','0')
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  await page.screenshot({path:info.outputPath('persistent-scrollbar.png'),fullPage:true})
  expect(errors).toEqual([])
 })
})}
