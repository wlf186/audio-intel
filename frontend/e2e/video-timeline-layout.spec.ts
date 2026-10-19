import {test,expect,type Page} from './fixtures'
import {uxFixture} from './ux-fixtures'

test.use({launchOptions:{ignoreDefaultArgs:['--hide-scrollbars'],args:[...(process.platform==='linux'?['--no-sandbox']:[]),'--disable-features=OverlayScrollbar,OverlayScrollbars']}})

async function observeTimeline(page:Page){
 return page.evaluate(()=>new Promise<{frames:number;widths:number[];readerTops:number[];unreadyFrames:number}>(resolve=>{
  const widths=new Set<number>(),readerTops=new Set<number>()
  let frames=0,unreadyFrames=0
  const started=performance.now()
  function sample(){
   widths.add(Math.round(document.querySelector('.timeline-tracks')!.getBoundingClientRect().width))
   readerTops.add(Math.round(document.querySelector('.video-transcript')!.getBoundingClientRect().top))
   if(document.querySelector('.timeline-load-state')||!document.querySelector<HTMLCanvasElement>('.timeline-waveform')!.dataset.windowEnd)unreadyFrames++
   frames++
   // The defect oscillates every animation frame without sending new requests.
   // Observe real elapsed time; an immediate ready assertion misses the loop.
   if(performance.now()-started<2100)requestAnimationFrame(sample)
   else resolve({frames,widths:[...widths],readerTops:[...readerTops],unreadyFrames})
  }
  requestAnimationFrame(sample)
 }))
}

test.describe('video timeline with classic scrollbars',()=>{
 for(const width of [1920,1440])test(`loading near the overflow boundary stays stable at ${width}px @timing`,async({page},info)=>{
  await page.setViewportSize({width,height:1080})
  const {state,errors}=await uxFixture(page)
  state.job.result!.audio_enhancement={mode:'auto',status:'bypassed',reason:'clean'}
  const requests:number[]=[]
  page.on('request',request=>{const url=new URL(request.url());if(url.pathname.endsWith('/waveform'))requests.push(Number(url.searchParams.get('points')))})
  await page.goto('/#asr')
  await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
  const waveform=page.locator('.timeline-waveform')
  await expect(waveform).toHaveAttribute('data-window-end','120')
  await expect(page.locator('.video-section')).toHaveCount(20)
  const geometry=await page.evaluate(()=>{
   const panel=document.querySelector<HTMLElement>('.video-result')!,reader=document.querySelector<HTMLElement>('.video-transcript')!
   const spareReaderHeight=Math.max(0,reader.clientHeight-280)
   return{criticalHeight:Math.ceil(innerHeight+panel.scrollHeight-panel.clientHeight-spareReaderHeight+12),width:Math.round(document.querySelector('.timeline-tracks')!.getBoundingClientRect().width)}
  })
  // Derive the boundary from the synthetic content, independent of OS font metrics.
  expect(geometry.criticalHeight).toBeGreaterThan(600)
  await page.setViewportSize({width,height:geometry.criticalHeight-40})
  const narrowWidth=await page.locator('.timeline-tracks').evaluate(node=>Math.round(node.getBoundingClientRect().width))
  if(narrowWidth!==geometry.width)await expect.poll(()=>requests.at(-1)).toBe(narrowWidth)
  await expect(waveform).toHaveAttribute('data-window-end','120')
  const before=requests.length
  await page.setViewportSize({width,height:geometry.criticalHeight})
  const observed=await observeTimeline(page)
  expect(observed.frames).toBeGreaterThan(20)
  expect(observed.widths).toHaveLength(1)
  expect(observed.readerTops).toHaveLength(1)
  expect(observed.unreadyFrames).toBe(0)
  expect(requests).toHaveLength(before)
  await waveform.focus();await page.keyboard.press('Home');await page.keyboard.press('ArrowRight')
  await expect(waveform).toHaveAttribute('aria-valuenow','5')
  await expect.poll(()=>page.locator('.result-panel audio').evaluate((node:HTMLAudioElement)=>node.currentTime)).toBeCloseTo(5,0)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  await page.screenshot({path:info.outputPath(`stable-video-timeline-${width}.png`),fullPage:true})
  expect(errors).toEqual([])
 })

 for(const width of [1440,390])test(`waveform failure retries without blocking playback at ${width}px @timing`,async({page,responseGate})=>{
  await page.setViewportSize({width,height:940})
  const {state,errors}=await uxFixture(page)
  state.job.result!.audio_enhancement={mode:'auto',status:'bypassed',reason:'clean'}
  const pending=responseGate();let reads=0
  await page.route('**/api/v1/jobs/ux-video/waveform?*',async route=>{
   if(++reads===1){await pending.hold();return route.fulfill({status:503,json:{detail:'Waveform temporarily unavailable'}})}
   const params=new URL(route.request().url()).searchParams
   return route.fulfill({json:{track:'original',duration:120,start:Number(params.get('start')),end:Number(params.get('end')),waveform:Array(Number(params.get('points'))).fill(.3)}})
  })
  await page.goto('/#asr')
  await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
  await pending.arrived
  await expect(page.locator('.timeline-load-state[role=status]')).toBeVisible()
  pending.release()
  const failure=page.locator('.timeline-load-state[role=alert]')
  await expect(failure).toBeVisible()
  const waveform=page.locator('.timeline-waveform')
  await waveform.focus();await page.keyboard.press('Home');await page.keyboard.press('ArrowRight')
  await expect.poll(()=>page.locator('.result-panel audio').evaluate((node:HTMLAudioElement)=>node.currentTime)).toBeCloseTo(5,0)
  const response=page.waitForResponse(response=>new URL(response.url()).pathname.endsWith('/waveform')&&response.status()===200)
  await failure.getByRole('button',{name:'重试',exact:true}).click()
  await(await response).finished()
  await expect(waveform).toHaveAttribute('data-window-end','120')
  await expect(page.locator('.timeline-load-state')).toHaveCount(0)
  expect(reads).toBe(2)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  expect(errors).toEqual([])
 })
})
