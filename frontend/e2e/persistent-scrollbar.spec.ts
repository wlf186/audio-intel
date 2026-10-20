import {test,expect} from './fixtures'
import {openTimeline,scrollbarCases} from './scrollbar-fixture'

scrollbarCases(false)

test('scrollbar touch drag and cancellation leave playback and subsequent gestures intact',async({page})=>{
 await page.setViewportSize({width:390,height:844})
 const {errors}=await openTimeline(page)
 await page.getByRole('button',{name:'放大时间轴',exact:true}).click()
 const bar=page.getByRole('scrollbar'),wave=page.locator('.timeline-waveform')
 await expect(wave).toHaveAttribute('data-window-end','60')
 await bar.scrollIntoViewIfNeeded()
 const rail=(await bar.boundingBox())!,thumb=(await page.locator('.timeline-scrollbar-thumb').boundingBox())!
 const x=thumb.x+thumb.width/2,y=rail.y+rail.height/2
 const session=await page.context().newCDPSession(page)
 await session.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]})
 await session.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x+60,y}]})
 await session.send('Input.dispatchTouchEvent',{type:'touchCancel',touchPoints:[]})
 await expect.poll(async()=>Number(await bar.getAttribute('aria-valuenow'))).toBeGreaterThan(0)
 const after=await bar.getAttribute('aria-valuenow')
 await page.mouse.move(x+100,y)
 await expect(bar).toHaveAttribute('aria-valuenow',after!)
 await bar.press('Home')
 await session.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]})
 await session.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:rail.x+rail.width-1,y}]})
 await session.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]})
 await session.detach()
 await expect(wave).toHaveAttribute('data-window-start','60')
 await expect(wave).toHaveAttribute('aria-valuenow','0')
 await page.getByRole('button',{name:'显示全片',exact:true}).click()
 await expect(bar).toHaveAttribute('aria-disabled','true')
 expect(errors).toEqual([])
})

test('minimum thumb size, resizing and explicit seeks keep the window synchronized',async({page})=>{
 await page.setViewportSize({width:390,height:844})
 const {errors}=await openTimeline(page)
 const bar=page.getByRole('scrollbar'),wave=page.locator('.timeline-waveform'),thumb=page.locator('.timeline-scrollbar-thumb')
 for(let i=0;i<4;i++)await page.getByRole('button',{name:'放大时间轴',exact:true}).click()
 await expect(wave).toHaveAttribute('data-window-end','10')
 expect((await thumb.boundingBox())!.width).toBe(44)
 await bar.press('End');await expect(wave).toHaveAttribute('data-window-start','110')
 await page.setViewportSize({width:1440,height:800})
 await expect(wave).toHaveAttribute('data-window-start','110')
 await expect.poll(async()=>{const a=(await bar.boundingBox())!,b=(await thumb.boundingBox())!;return Math.abs(a.x+a.width-b.x-b.width)}).toBeLessThan(1)
 await bar.press('Home');await expect(wave).toHaveAttribute('data-window-start','0')
 await wave.press('End');await expect(wave).toHaveAttribute('aria-valuenow','120')
 await expect(bar).toHaveAttribute('aria-valuenow','110')
 // Trackpad input also works over the persistent bar itself.
 await bar.hover();await page.mouse.wheel(-300,0)
 await expect.poll(async()=>Number(await bar.getAttribute('aria-valuenow'))).toBeLessThan(110)
 await expect(wave).toHaveAttribute('aria-valuenow','120')
 expect(errors).toEqual([])
})
