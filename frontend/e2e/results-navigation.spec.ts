import {test,expect} from './fixtures'
import {uxFixture} from './ux-fixtures'
import {scrollTimelineTo} from './timeline-fixture'

for(const [width,height,minimum] of [[1280,720,180],[1440,800,260],[1470,830,280],[390,844,180]])for(const locale of ['zh-CN','en-US'])test(`compact video reading ${width}x${height} ${locale}`,async({page},info)=>{
 await page.setViewportSize({width,height})
 await page.addInitScript(locale=>localStorage.setItem('audio-intel:ui-locale:v1',locale),locale)
 const {state,errors}=await uxFixture(page)
 state.job.result!.audio_enhancement={mode:'auto',status:'applied',reason:'noise_and_gain',playback_url:'/api/v1/jobs/ux-video/playback',speech_enhancement:{enabled:true,status:'applied',reason:'quiet_speech',strength:1.6},background_denoise:{enabled:true,status:'applied',reason:'noise',strength:.8}}
 const zh=locale==='zh-CN'
 await page.goto('/#asr');await page.getByRole('tab',{name:zh?'任务与结果':'Tasks & results',exact:true}).click()
 const waveform=page.locator('.timeline-waveform'),reader=page.locator('.video-transcript')
 await expect(waveform).toHaveAttribute('data-window-end','120')
 const initial=(await reader.boundingBox())!
 expect(initial.height).toBeGreaterThanOrEqual(minimum)
 if(width>=1024){
  const panel=(await page.locator('.video-result').boundingBox())!
  expect(initial.y+initial.height).toBeLessThanOrEqual(panel.y+panel.height+1)
  expect(panel.y+panel.height).toBeLessThan(height)
  expect(await page.locator('.video-result').evaluate(node=>node.scrollHeight-node.clientHeight)).toBeLessThanOrEqual(1)
 }
 await page.locator('.enhancement-summary button').click()
 await expect(page.locator('.enhancement-details-panel')).toContainText('160%')
 await page.keyboard.press('Escape')
 await page.getByRole('button',{name:zh?'放大时间轴':'Zoom in timeline',exact:true}).click()
 await expect(waveform).toHaveAttribute('data-window-end','60')
 expect(Math.abs((await reader.boundingBox())!.height-initial.height)).toBeLessThanOrEqual(1)
 await page.getByRole('button',{name:zh?'逐句明细':'Segment details',exact:true}).click()
 await expect(page.locator('.transcript-tools input')).toBeVisible()
 if(width>=1024)expect((await page.locator('.segments').boundingBox())!.height).toBeGreaterThanOrEqual(minimum)
 for(const control of await page.locator('.asr-enhancement-row button:visible,.asr-enhancement-row a:visible,.timeline-toolbar button:visible,.timeline-toolbar select').all()){
  const box=(await control.boundingBox())!
  expect(box.height).toBeGreaterThanOrEqual(44)
  expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(width)
 }
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 await page.screenshot({path:info.outputPath('compact-result.png'),fullPage:true})
 expect(errors).toEqual([])
})

test('waveform commits reveal lazy readers, clear detail filters and preserve paused playback',async({page})=>{
 const {errors}=await uxFixture(page)
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 const waveform=page.locator('.timeline-waveform'),reader=page.locator('.video-transcript'),player=page.locator('.result-panel audio')
 await expect(waveform).toHaveAttribute('data-window-end','120')
 const box=(await waveform.boundingBox())!,y=box.y+box.height/2
 const before=await reader.evaluate(node=>node.scrollTop)
 await page.mouse.move(box.x+box.width*.1,y);await page.mouse.down();await page.mouse.move(box.x+box.width*.8,y,{steps:5})
 expect(await reader.evaluate(node=>node.scrollTop)).toBe(before)
 await page.mouse.up()
 await expect(page.locator('.video-section[data-reader-id="scene-48"]')).toBeInViewport()
 await expect(page.locator('.video-section[data-reader-id="scene-48"]')).toHaveClass(/navigation-active/)
 await expect.poll(()=>player.evaluate((node:HTMLAudioElement)=>node.currentTime)).toBeCloseTo(96,0)
 expect(await player.evaluate((node:HTMLAudioElement)=>node.paused)).toBe(true)
 // Loading more sections after navigation must not force the reader back to the old target.
 await reader.evaluate(node=>{node.scrollTop=0})
 await expect(page.locator('.video-section').first()).toBeInViewport()
 await page.getByRole('button',{name:'逐句明细',exact:true}).click()
 await page.getByRole('button',{name:'原文',exact:true}).click()
 await expect.poll(()=>reader.evaluate(node=>node.scrollTop)).toBe(0)
 await page.getByRole('button',{name:'逐句明细',exact:true}).click()
 await page.getByPlaceholder('搜索转写内容').fill('第 1 段')
 await expect(page.locator('.segments article')).toHaveCount(1)
 await waveform.focus();await page.keyboard.press('End')
 await expect(page.getByPlaceholder('搜索转写内容')).toHaveValue('')
 await expect(page.locator('.segments [data-reader-id="59"]')).toBeInViewport()
 await expect(page.locator('.segments [data-reader-id="59"]')).toHaveClass(/active/)
 await expect(waveform).toHaveAttribute('aria-valuenow','120')
 expect(errors).toEqual([])
})

test('horizontal wheel and persistent scrollbar browse without seeking; locate restores following',async({page})=>{
 const {errors}=await uxFixture(page)
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 const waveform=page.locator('.timeline-waveform')
 await expect(waveform).toHaveAttribute('data-window-end','120')
 await page.getByRole('button',{name:'放大时间轴',exact:true}).click()
 await expect(waveform).toHaveAttribute('data-window-end','60')
 await waveform.hover();await page.mouse.wheel(300,0)
 await expect.poll(async()=>Number(await waveform.getAttribute('data-window-start'))).toBeGreaterThan(5)
 await expect(waveform).toHaveAttribute('aria-valuenow','0')
 await expect(page.getByRole('button',{name:'定位播放点',exact:true})).toBeEnabled()
 await page.getByRole('button',{name:'定位播放点',exact:true}).click()
 await expect(waveform).toHaveAttribute('data-window-start','0')
 await expect(waveform).toHaveAttribute('aria-valuenow','0')
 await scrollTimelineTo(page,60)
 await expect(waveform).toHaveAttribute('data-window-start','60')
 await page.getByRole('combobox',{name:'画面目录',exact:true}).selectOption('scene-1')
 await expect.poll(async()=>Number(await waveform.getAttribute('data-window-start'))).toBe(0)
 await expect(waveform).toHaveAttribute('aria-valuenow','2')
 expect(errors).toEqual([])
})

test('silence gaps reveal the next sentence without changing the chosen time',async({page})=>{
 const {state,errors}=await uxFixture(page)
 state.job.result!.segments![1].start=3
 state.job.result!.video!.scenes[1].paragraphs![0].start=3
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 const waveform=page.locator('.timeline-waveform')
 await expect(waveform).toHaveAttribute('data-window-end','120')
 await page.getByRole('button',{name:'逐句明细',exact:true}).click()
 const box=(await waveform.boundingBox())!
 await page.mouse.click(box.x+box.width*2.5/120,box.y+box.height/2)
 await expect(page.locator('.segments [data-reader-id="1"]')).toHaveClass(/active/)
 await expect.poll(()=>page.locator('.result-panel audio').evaluate((node:HTMLAudioElement)=>node.currentTime)).toBeCloseTo(2.5,1)
 expect(errors).toEqual([])
})


test('horizontal touch browsing never seeks and waveform seeking preserves active playback @timing',async({page})=>{
 await page.setViewportSize({width:390,height:844})
 const {errors}=await uxFixture(page)
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 const waveform=page.locator('.timeline-waveform'),player=page.locator('.result-panel audio')
 await expect(waveform).toHaveAttribute('data-window-end','120')
 await page.getByRole('button',{name:'放大时间轴',exact:true}).click()
 await expect(waveform).toHaveAttribute('data-window-end','60')
 await page.locator('.timeline-scenes').scrollIntoViewIfNeeded()
 const box=(await page.locator('.timeline-scenes').boundingBox())!,x=box.x+box.width*.8,y=box.y+box.height/2
 const session=await page.context().newCDPSession(page)
 await session.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]})
 for(let step=1;step<=5;step++)await session.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x-step*24,y}]})
 await session.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]})
 await session.detach()
 await expect.poll(async()=>Number(await waveform.getAttribute('data-window-start'))).toBeGreaterThan(5)
 await expect(waveform).toHaveAttribute('aria-valuenow','0')
 await page.getByRole('button',{name:'播放',exact:true}).click()
 await expect.poll(()=>player.evaluate((node:HTMLAudioElement)=>node.paused)).toBe(false)
 await waveform.focus();await page.keyboard.press('ArrowRight')
 await expect.poll(()=>player.evaluate((node:HTMLAudioElement)=>node.currentTime)).toBeGreaterThanOrEqual(5)
 expect(await player.evaluate((node:HTMLAudioElement)=>node.paused)).toBe(false)
 await expect(page.locator('.video-section.navigation-active')).toBeInViewport()
 await player.evaluate((node:HTMLAudioElement)=>node.pause())
 expect(errors).toEqual([])
})

test('short desktop windows scroll the page without clipping result controls',async({page})=>{
 await page.setViewportSize({width:1280,height:560})
 const {errors}=await uxFixture(page)
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 const waveform=page.locator('.timeline-waveform')
 await expect(waveform).toHaveAttribute('data-window-end','120')
 await page.getByRole('combobox',{name:'画面目录',exact:true}).selectOption('scene-40')
 await expect(page.locator('.video-section[data-reader-id="scene-40"] .video-paragraph')).toBeInViewport()
 await page.getByRole('button',{name:'逐句明细',exact:true}).click()
 await expect(page.locator('.transcript-tools input')).toBeVisible()
 expect(await page.locator('.video-result').evaluate(node=>node.scrollHeight-node.clientHeight)).toBeLessThanOrEqual(1)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1280)
 expect(errors).toEqual([])
})
