import {test,expect} from './fixtures'
import {mockTimelineWaveform} from './timeline-fixture'
import {uxFixture} from './ux-fixtures'
import type {Job} from '../src/lib/types'

for(const width of [1440,390])test(`cached ASR task switching removes previous media at ${width}px @timing`,async({page},info)=>{
 await page.setViewportSize({width,height:900})
 const {state,errors}=await uxFixture(page)
 const video=state.job
 video.display_name='WorkBuddy与AI-Coding融合方案-用户旅程版.mp4'
 const audio=structuredClone(video)
 audio.id='switch-audio';audio.purpose='transcription';audio.display_name='Recording_10.m4a'
 delete audio.result!.video
 audio.result!.waveform=[.1,.7,.15,.8,.2]
 const other=structuredClone(video)
 other.id='switch-video';other.display_name='第二个视频.mp4'
 const jobs=[audio,video,other]
 const summaries=jobs.map(({request:_,result:__,...summary})=>summary)
 await page.route(url=>url.pathname==='/api/v1/jobs',route=>route.fulfill({json:{items:summaries,count:3,total:3,has_more:false}}))
 for(const job of [audio,other])await page.route(`**/api/v1/jobs/${job.id}`,route=>route.fulfill({json:job}))
 await page.route(`**/api/v1/jobs/${other.id}/video-scenes`,route=>route.fulfill({json:{revision:1,scenes:other.result!.video!.scenes}}))
 await page.route(`**/api/v1/jobs/${other.id}/video-frames/*`,route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#007782"/></svg>'}))
 await mockTimelineWaveform(page,other.id,120)
 const detailReads=new Map<string,number>()
 page.on('request',request=>{
  const id=new URL(request.url()).pathname.split('/').at(-1)!
  if(jobs.some(job=>job.id===id))detailReads.set(id,(detailReads.get(id)||0)+1)
 })
 await page.goto('/#asr')
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 async function select(job:Job){
  if(width<1024)await page.locator('.asr-task-picker select').selectOption(job.id)
  else await page.locator('.asr-recent-jobs .job-mini').filter({hasText:job.display_name}).click()
  await expect(page.locator('.result-identity')).toContainText(job.display_name)
  await expect(page.locator('.result-panel audio')).toHaveCount(1)
  if(job.result!.video){
   await expect(page.locator('.video-transcript')).toHaveCount(1)
   await expect(page.locator('.video-timeline')).toHaveCount(1)
   await expect(page.locator('.timeline-scenes')).toHaveCount(1)
   await expect(page.locator('.timeline-waveform')).toHaveCount(1)
   await expect(page.locator('.timeline-waveform')).toHaveAttribute('data-window-end','120')
   await expect(page.locator('.wave-area')).toHaveCount(0)
   const frames=page.locator('.timeline-tiles img')
   await expect(frames.first()).toBeVisible()
   await expect.poll(()=>frames.evaluateAll(nodes=>nodes.every(node=>(node as HTMLImageElement).complete&&(node as HTMLImageElement).naturalWidth>0))).toBe(true)
   for(const frame of await frames.all())await expect(frame).toHaveAttribute('src',new RegExp(`/jobs/${job.id}/video-frames/`))
  }else{
   await expect(page.locator('.wave-area canvas')).toHaveCount(1)
   await expect(page.locator('.video-timeline')).toHaveCount(0)
   await expect(page.locator('.timeline-scenes')).toHaveCount(0)
   await expect(page.locator('.timeline-waveform')).toHaveCount(0)
   await expect(page.locator('.video-transcript')).toHaveCount(0)
   await expect(page.locator('.timeline-preview')).toHaveCount(0)
  }
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 }
 // Returning to cached details updates the existing result tree without a loading unmount.
 for(const job of [audio,video,audio,video,other,video,audio,other,video,audio,video])await select(job)
 for(const job of jobs)expect(detailReads.get(job.id)).toBe(1)
 const waveform=page.locator('.timeline-waveform')
 await waveform.focus();await page.keyboard.press('Home');await page.keyboard.press('ArrowRight')
 await expect(waveform).toHaveAttribute('aria-valuenow','5')
 await expect.poll(()=>page.locator('.result-panel audio').evaluate((node:HTMLAudioElement)=>node.currentTime)).toBeCloseTo(5,0)
 await page.getByRole('button',{name:'文本阅读',exact:true}).click()
 await expect(page.locator('.segments')).toBeVisible()
 await expect(page.locator('.video-transcript')).toHaveCount(0)
 await expect(page.locator('.video-timeline')).toHaveCount(1)
 await page.getByRole('button',{name:'图文阅读',exact:true}).click()
 await expect(page.locator('.video-transcript')).toBeVisible()
 await expect(page.locator('.video-timeline')).toHaveCount(1)
 await page.screenshot({path:info.outputPath(`switch-video-${width}.png`),fullPage:true})
 await page.locator('.timeline-scenes').focus()
 await expect(page.locator('.timeline-preview')).toBeVisible()
 await select(audio)
 await page.screenshot({path:info.outputPath(`switch-audio-${width}.png`),fullPage:true})
 expect(errors).toEqual([])
})
