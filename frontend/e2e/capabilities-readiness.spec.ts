import {capabilitiesFor,expect,test} from './fixtures'
import {expandAsrAdvanced} from './asr-helpers'
import {expandTtsSettings} from './tts-helpers'

test.afterEach(async({page})=>{await page.unrouteAll({behavior:'wait'})})

function asrDevice(body:Awaited<ReturnType<typeof capabilitiesFor>>){
 const gpu=body.asr.models.find(model=>model.default)?.compute_devices.find(device=>device.id==='gpu')
 return body.deployment.default_compute_device==='gpu'&&!gpu?.available?'cpu':body.deployment.default_compute_device
}

for(const width of [1440,390]){
 test(`capabilities loading waits for readiness and survives reload at ${width}px`,async({page},info)=>{
  await page.setViewportSize({width,height:900})
  const errors:string[]=[]
  page.on('pageerror',error=>errors.push(error.message))
  page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
  const body=await capabilitiesFor(page);body.events.sse=false
  let release:()=>void=()=>undefined
  let reads=0
  const gate=new Promise<void>(resolve=>{release=resolve})
  await page.route('**/api/v1/capabilities',async route=>{reads++;await gate;await route.fulfill({json:body})})
  try{
   await page.goto('/#asr')
   await expect(page.locator('.resource-state.loading')).toContainText('正在读取部署能力')
   await expect(page.locator('.asr-workspace')).toHaveCount(0)
   await expect(page.getByText('还没有转写结果',{exact:true})).toHaveCount(0)
   release()
   await expandAsrAdvanced(page)
   await expect(page.getByLabel('ASR 计算设备')).toHaveValue(asrDevice(body))
   await page.reload()
   await expandAsrAdvanced(page)
   await expect(page.getByLabel('ASR 计算设备')).toHaveValue(asrDevice(body))
   expect(reads).toBe(2)
   expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
   await page.screenshot({path:info.outputPath('capabilities-ready.png')})
   expect(errors).toEqual([])
  }finally{release()}
 })

 for(const failure of ['http','disconnect'] as const)test(`capabilities ${failure} shows an error and recovers on retry at ${width}px`,async({page},info)=>{
  await page.setViewportSize({width,height:900})
  const errors:string[]=[]
  page.on('pageerror',error=>errors.push(error.message))
  page.on('console',message=>{
   if(message.type()==='error'&&!message.text().includes(failure==='http'?'503 (Service Unavailable)':'net::ERR_CONNECTION_RESET'))errors.push(message.text())
  })
  const body=await capabilitiesFor(page);body.events.sse=false
  let reads=0
  await page.route('**/api/v1/capabilities',async route=>{
   reads++
   if(reads===1)return failure==='http'?route.fulfill({status:503,json:{detail:'Capabilities temporarily unavailable'}}):route.abort('connectionreset')
   return route.fulfill({json:body})
  })
  await page.goto('/#asr')
  const error=page.locator('.resource-state.error')
  await expect(error).toBeVisible()
  if(failure==='http')await expect(error).toContainText('Capabilities temporarily unavailable')
  await expect(page.locator('.asr-workspace')).toHaveCount(0)
  await expect(page.getByText('还没有转写结果',{exact:true})).toHaveCount(0)
  const retry=error.getByRole('button',{name:'重试',exact:true})
  if(width===390)expect((await retry.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  await page.screenshot({path:info.outputPath('capabilities-error.png')})
  await retry.click()
  await expandAsrAdvanced(page)
  await expect(page.getByLabel('ASR 计算设备')).toHaveValue(asrDevice(body))
  await expect(error).toHaveCount(0)
  expect(reads).toBe(2)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  expect(errors).toEqual([])
 })

 test(`live capabilities populate ASR and TTS after reload at ${width}px`,async({page},info)=>{
  await page.setViewportSize({width,height:900})
  const errors:string[]=[]
  page.on('pageerror',error=>errors.push(error.message))
  page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
  // Intentionally leave capabilities unmocked: this covers the real browser path.
  for(const navigate of [()=>page.goto('/#asr'),()=>page.reload()]){
   const ready=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/v1/capabilities')
   await navigate()
   const response=await ready
   expect(response.ok()).toBe(true)
   expect(await response.finished()).toBeNull()
   const body=await response.json()
   expect(body.asr.models.length).toBeGreaterThan(0)
   expect(body.tts.model_capabilities.length).toBeGreaterThan(0)
   await expandAsrAdvanced(page)
   await expect(page.getByLabel('ASR 计算设备')).toHaveValue(asrDevice(body))
  }
  await page.locator('nav').getByRole('button',{name:/语音合成/}).click()
  await expandTtsSettings(page)
  await expect(page.getByLabel('TTS 计算设备')).toBeVisible()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  await page.screenshot({path:info.outputPath('live-capabilities.png')})
  expect(errors).toEqual([])
 })
}
