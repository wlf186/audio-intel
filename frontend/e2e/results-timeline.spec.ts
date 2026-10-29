import {test,expect} from './fixtures'
import {scrollTimelineTo} from './timeline-fixture'
import {uxFixture} from './ux-fixtures'

for(const width of [1440,1024,390])for(const locale of ['zh-CN','en-US'])test(`unified video timeline navigation ${width} ${locale}`,async({page},info)=>{
 await page.setViewportSize({width,height:1000})
 await page.addInitScript(locale=>localStorage.setItem('audio-intel:ui-locale:v1',locale),locale)
 const {state,errors}=await uxFixture(page)
 const zh=locale==='zh-CN'
 state.job.result!.audio_enhancement={mode:'auto',status:'applied',reason:'noise_and_gain',playback_url:'/api/v1/jobs/ux-video/playback',speech_enhancement:{enabled:true,status:'applied',reason:'quiet_speech',strength:1.6},background_denoise:{enabled:true,status:'applied',reason:'noise',strength:.8}}
 const windows:string[]=[]
 page.on('request',request=>{if(new URL(request.url()).pathname.endsWith('/waveform'))windows.push(request.url())})
 await page.goto('/#asr');await page.getByRole('tab',{name:zh?'任务与结果':'Tasks & results',exact:true}).click()
 await expect(page.locator('.video-section')).toHaveCount(20)
 await expect(page.locator('.timeline-waveform')).toBeVisible()
 await expect.poll(()=>windows.length).toBeGreaterThan(0)
 await expect(page.locator('.timeline-load-state')).toHaveCount(0)
 await expect(page.locator('.enhancement-summary')).not.toContainText('160%')
 expect((await page.locator('.enhancement-summary').boundingBox())!.height).toBeLessThanOrEqual(width===390?100:60)
 await page.locator('.enhancement-summary button').click()
 await expect(page.locator('.enhancement-details-panel')).toContainText(zh?'人声 160% · 降噪 80%':'Voice 160% · Denoise 80%')
 await expect(page.locator('.enhancement-details-panel')).toContainText(zh?'已应用':'Applied')
 await page.keyboard.press('Escape');await expect(page.locator('.enhancement-details-panel')).toHaveCount(0)
 const before=windows.length
 const waveform=page.locator('.timeline-waveform')
 await waveform.focus();await page.keyboard.press('Home');await page.keyboard.press('ArrowRight')
 await expect.poll(()=>page.locator('.result-panel audio').evaluate((node:HTMLAudioElement)=>node.currentTime)).toBeCloseTo(5,0)
 // Playback position updates do not refetch the same visible window.
 await expect(waveform).toHaveAttribute('aria-valuenow','5')
 expect(windows.length).toBe(before)
 await page.getByRole('button',{name:zh?'放大时间轴':'Zoom in timeline',exact:true}).click()
 await expect.poll(()=>windows.some(url=>Number(new URL(url).searchParams.get('end'))-Number(new URL(url).searchParams.get('start'))===60)).toBe(true)
 await scrollTimelineTo(page,40)
 await expect.poll(()=>windows.some(url=>Math.abs(Number(new URL(url).searchParams.get('start'))-40)<.2)).toBe(true)
 await expect(page.locator('.timeline-load-state')).toHaveCount(0)
 const bounds=(await waveform.boundingBox())!
 await page.mouse.move(bounds.x+bounds.width*.25,bounds.y+bounds.height/2);await page.mouse.down();await page.mouse.move(bounds.x+bounds.width*.5,bounds.y+bounds.height/2,{steps:4});await page.mouse.up()
 await expect.poll(()=>page.locator('.result-panel audio').evaluate((node:HTMLAudioElement)=>node.currentTime)).toBeCloseTo(70,0)
 const marker=page.locator('.timeline-scenes')
 await marker.focus();await expect(page.locator('.timeline-preview')).toBeVisible()
 const preview=(await page.locator('.timeline-preview').boundingBox())!
 expect(preview.x).toBeGreaterThanOrEqual(0);expect(preview.x+preview.width).toBeLessThanOrEqual(width)
 await page.keyboard.press('Escape');await expect(page.locator('.timeline-preview')).toHaveCount(0)
 await page.getByRole('combobox',{name:zh?'画面目录':'Scene directory'}).selectOption('scene-40')
 await expect.poll(()=>page.locator('.result-panel audio').evaluate((node:HTMLAudioElement)=>node.currentTime)).toBeCloseTo(80,0)
 await expect(page.locator('.video-section[data-reader-id="scene-40"]')).toBeInViewport()
 await expect.poll(()=>page.locator('.result-panel audio').evaluate((node:HTMLAudioElement)=>node.paused)).toBe(true)
 await page.getByRole('button',{name:zh?'文本阅读':'Text reading',exact:true}).click()
 await page.getByRole('combobox',{name:zh?'画面目录':'Scene directory'}).selectOption('scene-45')
 await expect(page.locator('.segments [data-reader-id="45"]')).toBeInViewport()
 await scrollTimelineTo(page,0)
 const locate=page.getByRole('button',{name:zh?'定位播放点':'Locate playhead',exact:true})
 await expect(locate).toBeEnabled();await locate.click();await expect(locate).toBeDisabled()
 await expect(waveform).toHaveAttribute('aria-valuenow','90')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 await page.screenshot({path:info.outputPath(`timeline-${width}-${locale}.png`),fullPage:true})
 expect(errors).toEqual([])
})

test('2000 scene overview only loads visible thumbnails and shares one scenes resource',async({page})=>{
 const {state,errors}=await uxFixture(page)
 const scenes=Array.from({length:2000},(_,index)=>({id:`scene-${index}`,start:index*.06,end:(index+1)*.06,frame_time:index*.06+.01,image:`frame-${index}.jpg`,paragraphs:[]}))
 state.job.result!.video!.scenes.splice(0,60,...scenes)
 let sceneReads=0;const images=new Set<string>()
 page.on('request',request=>{const url=new URL(request.url());if(url.pathname.endsWith('/video-scenes'))sceneReads++;if(url.pathname.includes('/video-frames/'))images.add(url.pathname)})
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(page.locator('.timeline-scene')).toHaveCount(2000)
 await expect(page.locator('.timeline-load-state')).toHaveCount(0)
 expect(images.size).toBeLessThan(64)
 expect(sceneReads).toBe(1)
 await page.getByRole('button',{name:'文本阅读',exact:true}).click();await page.getByRole('button',{name:'图文阅读',exact:true}).click()
 await expect(page.locator('.video-section')).toHaveCount(20)
 expect(sceneReads).toBe(1)
 expect(errors).toEqual([])
})

for(const width of [1440,1000,390])test(`storage loading, real zero, unknown subtotal and clear retry at ${width}px`,async({page,responseGate})=>{
 await page.setViewportSize({width,height:1000})
 const {state,errors}=await uxFixture(page)
 const base=state.job
 await page.route(url=>url.pathname==='/api/v1/jobs',route=>route.fulfill({json:{items:[base,{...base,id:'zero',display_name:'Zero task'},{...base,id:'unknown',display_name:'Unknown task'}],count:3,total:3,limit:25,offset:0,has_more:false}}))
 const gate=responseGate();let calls=0
 await page.route('**/api/v1/jobs/storage',async route=>{calls++;if(calls===1)await gate.hold();return route.fulfill({json:{items:[{id:base.id,status:'ready',bytes:1048576,checked_at:'2026-10-19T00:00:00Z',basis:'logical'},{id:'zero',status:'ready',bytes:0,checked_at:'2026-10-19T00:00:00Z',basis:'logical'},{id:'unknown',status:calls===1?'error':'ready',bytes:calls===1?null:2097152,checked_at:'2026-10-19T00:00:00Z',basis:'logical'}]}})})
 await page.goto('/#jobs');await gate.arrived
 await expect(page.getByRole('columnheader')).toHaveCount(width===1440?9:width===1000?8:0)
 await expect(page.locator('.job-storage').first()).toContainText('统计中')
 if(width===390){for(const checkbox of await page.locator('.table-row input[type=checkbox]').all())await checkbox.check()}
 else await page.getByRole('checkbox',{name:'全选当前页可操作任务',exact:true}).check()
 await expect(page.locator('.selection-bar')).toContainText('占用待确认')
 gate.release()
 await expect(page.locator('.job-storage').nth(1)).toContainText('0 B')
 await expect(page.locator('.selection-bar')).toContainText('1.0 MB')
 await expect(page.locator('.selection-bar')).toContainText('1 项未知')
 await page.getByRole('button',{name:'永久删除所选任务',exact:true}).click()
 await expect(page.getByRole('dialog')).toContainText('实际释放以删除结果为准')
 await page.getByRole('dialog').getByRole('button',{name:'取消',exact:true}).click()
 await page.getByRole('button',{name:'刷新占用',exact:true}).click()
 await expect(page.locator('.selection-bar')).toContainText('3.0 MB')
 await expect(page.locator('.selection-bar')).not.toContainText('未知')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 expect(errors).toEqual([])
})

test('new waveform window wins over a delayed obsolete response @timing',async({page,responseGate})=>{
 const {errors}=await uxFixture(page)
 const old=responseGate();const next=responseGate()
 await page.route('**/api/v1/jobs/ux-video/waveform?*',async route=>{
  const query=new URL(route.request().url()).searchParams,start=Number(query.get('start')),end=Number(query.get('end'))
  if(end-start===60&&start===0)await old.hold()
  if(Math.abs(start-40)<.2)await next.hold()
  return route.fulfill({json:{track:'original',duration:120,start,end,waveform:Array(200).fill(start===40?.8:.2)}})
 })
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 const waveform=page.locator('.timeline-waveform')
 await expect(waveform).toHaveAttribute('data-window-end','120')
 await page.getByRole('button',{name:'放大时间轴',exact:true}).click();await old.arrived
 await scrollTimelineTo(page,40);await next.arrived
 next.release();await expect(waveform).toHaveAttribute('data-window-start',/^39\.9|^40(?:\.0|$)/)
 old.release();await expect(waveform).toHaveAttribute('data-window-start',/^39\.9|^40(?:\.0|$)/)
 await expect(waveform).toHaveAttribute('data-window-end',/^99\.9|^100(?:\.0|$)/)
 expect(errors).toEqual([])
})

test('scene edits refresh the shared timeline and reader revision @timing',async({page,responseGate})=>{
 const {state,errors}=await uxFixture(page)
 const gate=responseGate();let reads=0
 await page.route('**/api/v1/jobs/ux-video/video-scenes',async route=>{
  if(route.request().method()==='PATCH'){
   const request=route.request().postDataJSON()
   expect(request.revision).toBe(1)
   state.job.result!.video!.export_hidden_scene_ids=request.export_hidden_scene_ids
   state.job.result!.video!.revision=2
   state.job.result!.video!.scenes[0].image='revised-frame.jpg'
   return route.fulfill({json:state.job.result})
  }
  reads++
  if(reads===2)await gate.hold()
  return route.fulfill({json:{revision:state.job.result!.video!.revision,scenes:state.job.result!.video!.scenes}})
 })
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(page.locator('.video-section')).toHaveCount(20)
 await page.getByRole('button',{name:'画面检测',exact:true}).click()
 await page.getByRole('checkbox',{name:'选择画面 1',exact:true}).check()
 await page.getByRole('dialog').getByRole('button',{name:'导出时隐藏',exact:true}).click()
 await expect(page.getByRole('dialog').getByRole('status')).toContainText('已保存')
 await page.keyboard.press('Escape');await gate.arrived
 await expect(page.locator('.video-transcript')).toHaveAttribute('aria-busy','true')
 await expect(page.getByRole('button',{name:'画面检测',exact:true})).toBeDisabled()
 gate.release()
 await expect(page.locator('.video-section .video-frame img').first()).toHaveAttribute('src',/revised-frame.jpg/)
 await expect(page.locator('.timeline-tiles img').first()).toHaveAttribute('src',/revised-frame.jpg/)
 expect(reads).toBe(2)
 expect(errors).toEqual([])
})

test('failed scene refresh retains the reader and retries without stale editing @timing',async({page})=>{
 const {state,errors}=await uxFixture(page)
 let reads=0
 await page.route('**/api/v1/jobs/ux-video/video-scenes',route=>{
  if(route.request().method()==='PATCH'){state.job.result!.video!.revision++;return route.fulfill({json:state.job.result})}
  reads++
  return reads===2?route.fulfill({status:503,json:{detail:'Scenes temporarily unavailable'}}):route.fulfill({json:{revision:state.job.result!.video!.revision,scenes:state.job.result!.video!.scenes}})
 })
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(page.locator('.video-section')).toHaveCount(20)
 await page.getByRole('button',{name:'画面检测',exact:true}).click()
 await page.getByRole('checkbox',{name:'选择画面 1',exact:true}).check()
 await page.getByRole('dialog').getByRole('button',{name:'导出时隐藏',exact:true}).click()
 await expect(page.getByRole('dialog').getByRole('status')).toContainText('已保存')
 await page.keyboard.press('Escape')
 await expect(page.locator('.video-transcript [role=alert]')).toContainText('Scenes temporarily unavailable')
 await expect(page.locator('.video-section')).toHaveCount(20)
 await expect(page.getByRole('button',{name:'画面检测',exact:true})).toBeDisabled()
 await page.locator('.video-transcript [role=alert]').getByRole('button').click()
 await expect(page.locator('.video-transcript [role=alert]')).toHaveCount(0)
 await expect(page.getByRole('button',{name:'画面检测',exact:true})).toBeEnabled()
 expect(reads).toBe(3)
 expect(errors).toEqual([])
})
