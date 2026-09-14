import {expect,test} from '@playwright/test'
import type {Job} from '../src/lib/types'

for(const width of [1440,390]){
 test(`generation recovery and historical coverage at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:900})
  const errors:string[]=[]
  page.on('pageerror',error=>errors.push(error.message))
  page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
  const base={kind:'tts' as const,state:'succeeded' as const,stage:'completed',progress:1,created_at:'2026-09-14T00:00:00Z',request:{voice_mode:'preset',speaker:'Vivian',model:'qwen3-tts-0.6b'},result:{duration:5,speaker:'Vivian',artifacts:[]}}
  const jobs:Job[]=[
   {...base,id:'recovered',display_name:'已恢复语音',result:{...base.result,generation_guard:{version:1,checked_chunks:8,retried_chunks:1,retry_attempts:2,recovered_chunks:1}}},
   {...base,id:'historical',display_name:'历史语音'},
   {...base,id:'retrying',display_name:'重试中的语音',state:'running',stage:'tts_chunk_retry',progress:.6,result:undefined,progress_detail:{stage_code:'tts_chunk_retry',basis:'observed',current:1,total:3,unit:'attempt',activity:{sequence:3,current:42,unit:'codec_frame',basis:'observed',updated_at:'2026-09-14T00:00:01Z'}}},
  ]
  await page.route('**/api/v1/capabilities',async route=>{const response=await route.fetch();const body=await response.json();body.events.sse=false;await route.fulfill({response,json:body})})
  await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({json:{items:[]}}))
  await page.route(/\/api\/v1\/jobs(?:\?.*)?$/,route=>route.fulfill({json:{items:jobs.map(({request,result,...summary})=>summary),count:3,total:3,has_more:false}}))
  await page.route(/\/api\/v1\/jobs\/(recovered|historical|retrying)(?:\?.*)?$/,route=>{
   const id=new URL(route.request().url()).pathname.split('/').at(-1)
   return route.fulfill({json:jobs.find(job=>job.id===id)})
  })
  await page.goto('/#tts')
  await expect(page).toHaveURL(/#tts$/)
  await expect(page).not.toHaveTitle('')
  await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
  const recent=page.locator('.tts-recent-jobs')
  async function select(id:string,name:string){
   if(width===390)await page.locator('.tts-task-picker select').selectOption(id)
   else await recent.getByRole('button').filter({hasText:name}).click()
  }
  await select('recovered','已恢复语音')
  await expect(page.locator('.audio-card')).toContainText('已自动恢复 1 处')
  await select('historical','历史语音')
  await expect(page.locator('.audio-card')).toContainText('历史语音')
  await expect(page.locator('.audio-card')).not.toContainText('自动恢复')
  await select('retrying','重试中的语音')
  await expect(page.locator('.tts-task-status')).toContainText('正在重试异常语音')
  await expect(page.locator('.tts-task-status')).toContainText('重试 1/3')
  await page.locator('.tts-task-status').scrollIntoViewIfNeeded()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  await page.screenshot({path:`/tmp/tts-generation-guard-${width}.png`,fullPage:true})
  expect(errors).toEqual([])
  await page.unrouteAll({behavior:'wait'})
 })
}
