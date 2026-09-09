import {expect,test,type Page} from '@playwright/test'

// Drain async route callbacks before Playwright closes the page.
test.afterEach(async({page})=>{await page.unrouteAll({behavior:'wait'})})

function wave(seconds:number){
 const bytes=16000*2*seconds
 const buffer=Buffer.alloc(44+bytes)
 buffer.write('RIFF');buffer.writeUInt32LE(buffer.length-8,4);buffer.write('WAVEfmt ',8)
 buffer.writeUInt32LE(16,16);buffer.writeUInt16LE(1,20);buffer.writeUInt16LE(1,22)
 buffer.writeUInt32LE(16000,24);buffer.writeUInt32LE(32000,28);buffer.writeUInt16LE(2,32);buffer.writeUInt16LE(16,34)
 buffer.write('data',36);buffer.writeUInt32LE(bytes,40)
 return buffer
}

async function mockWorkspace(page:Page){
 await page.route('**/api/v1/auth/session',route=>route.fulfill({json:{required:false,authenticated:true}}))
 await page.route('**/api/v1/jobs',route=>route.fulfill({json:{items:[],count:0,total:0,limit:100,offset:0,has_more:false}}))
 await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({json:{items:[]}}))
 await page.route('**/api/v1/asr/hotword-lists',route=>route.fulfill({json:{items:[],count:0}}))
 await page.route('**/api/v1/tts/voices',route=>route.fulfill({json:{items:[],preset_speakers:['Ryan','Aiden']}}))
 await page.route('**/api/v1/capabilities',async route=>{
  const response=await route.fetch()
  const body=await response.json()
  body.events={...body.events,sse:false}
  await route.fulfill({response,json:body})
 })
}

test('sequence results play and download every item in order on desktop and mobile',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 await mockWorkspace(page)
 const now=new Date().toISOString()
 const summary={id:'sequence-result',kind:'tts',state:'succeeded',stage:'completed',progress:1,display_name:'双段语音',created_at:now,updated_at:now}
 const items=[{id:'first',artifact_name:'item-0000.wav',duration:2,sample_rate:16000},{id:'second',artifact_name:'item-0001.wav',duration:5,sample_rate:16000}]
 const detail={...summary,request:{voice_mode:'preset',compute_device:'cpu'},result:{duration:7,model:'qwen3-tts-0.6b',format:'wav',sequence:{contract_version:1,items},artifacts:[...items].reverse().map(item=>({name:item.artifact_name,mime_type:'audio/wav',path:item.artifact_name,size_bytes:100}))}}
 await page.route('**/api/v1/jobs',route=>route.fulfill({json:{items:[summary],count:1,total:1,limit:100,offset:0,has_more:false}}))
 await page.route('**/api/v1/jobs/sequence-result',route=>route.fulfill({json:detail}))
 await page.route('**/api/v1/jobs/sequence-result/artifacts/*',route=>{
  const item=items.find(item=>route.request().url().endsWith(item.artifact_name))!
  return route.fulfill({contentType:'audio/wav',headers:{'Content-Disposition':`attachment; filename="${item.artifact_name}"`},body:wave(item.duration)})
 })
 await page.goto('/#tts')
 await expect(page).toHaveTitle(/Sandevistan/)
 await expect(page.locator('.audio-card')).toContainText('多段语音（2 段）')
 const rows=page.locator('.tts-sequence-results li')
 await expect(rows.locator('h3')).toHaveText(['first','second'])
 for(const width of [1440,390]){
  await page.setViewportSize({width,height:width===390?844:900})
  const second=rows.nth(1)
  await second.scrollIntoViewIfNeeded()
  await expect(rows.nth(0).locator('.transport-row')).toContainText('/ 00:00:02')
  await expect(second.locator('.transport-row')).toContainText('/ 00:00:05')
  await second.getByRole('button',{name:'播放当前合成结果'}).click()
  await expect(second.getByRole('button',{name:'暂停当前合成结果'})).toBeVisible()
  await second.getByRole('button',{name:'暂停当前合成结果'}).click()
  const downloadPromise=page.waitForEvent('download')
  await second.getByRole('link',{name:'下载 second WAV'}).click()
  expect((await downloadPromise).suggestedFilename()).toBe('item-0001.wav')
  for(const button of await rows.locator('button,a').all()){
   const box=await button.boundingBox()
   expect(box!.height).toBeGreaterThanOrEqual(44)
   expect(box!.width).toBeGreaterThanOrEqual(44)
  }
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  await page.screenshot({path:`/tmp/audio-intel-sequence-fixed-${width}.png`,fullPage:false})
 }
 expect(errors).toEqual([])
})

test('pending voiceprint polling stops after session expiry and resumes after login',async({page})=>{
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 await mockWorkspace(page)
 await page.clock.install()
 let calls=0
 let renewed=false
 const now=new Date().toISOString()
 await page.route('**/api/v1/auth/session',route=>{
  if(route.request().method()==='POST'){renewed=true;return route.fulfill({status:204})}
  return route.fulfill({json:{required:true,authenticated:true}})
 })
 await page.route('**/api/v1/voiceprints/people',route=>{
  calls++
  if(calls>1&&!renewed)return route.fulfill({status:401,json:{detail:'Session expired'}})
  const state=calls>=4?'ready':'pending'
  return route.fulfill({json:{items:[{id:'person',name:'测试声纹',sample_count:1,include_in_hotword_library:false,created_at:now,updated_at:now,samples:[{id:'sample',person_id:'person',state,language:'Chinese',transcript:'测试',words:[],tts_eligible:state==='ready',embedding_status:'ready',created_at:now,updated_at:now}]}]}})
 })
 await page.goto('/#tts')
 await expect(page.getByRole('heading',{name:'当前合成结果'})).toBeVisible()
 await expect.poll(()=>calls).toBe(1)
 await page.clock.fastForward(2100)
 await expect(page.getByRole('heading',{name:'访问验证'})).toBeVisible()
 await expect.poll(()=>calls).toBe(2)
 await page.clock.fastForward(6000)
 expect(calls).toBe(2)
 await page.setViewportSize({width:390,height:844})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 await page.getByLabel('API Key').fill('test-key')
 await page.getByRole('button',{name:'进入工作台'}).click()
 await expect(page.getByRole('heading',{name:'访问验证'})).toHaveCount(0)
 await expect.poll(()=>calls).toBe(3)
 await page.clock.fastForward(2100)
 await expect.poll(()=>calls).toBe(4)
 await page.clock.fastForward(6000)
 expect(calls).toBe(4)
 await page.screenshot({path:'/tmp/audio-intel-session-recovered-mobile.png',fullPage:false})
 expect(errors.filter(message=>!message.includes('401 (Unauthorized)'))).toEqual([])
 expect(errors.filter(message=>message.includes('401 (Unauthorized)'))).toHaveLength(1)
})
