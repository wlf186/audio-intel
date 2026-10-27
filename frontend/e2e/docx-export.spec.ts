import {expect,test,capabilitiesFor} from './fixtures'
import {uxFixture} from './ux-fixtures'
import {assertReachable,downloadConcise,openTranscriptExport} from './ux-helpers'

for(const width of [1440,390])for(const locale of ['zh-CN','en-US'])for(const video of [false,true])test(`concise DOCX download ${width} ${locale} video=${video}`,async({page},info)=>{
 await page.setViewportSize({width,height:844})
 await page.addInitScript(locale=>localStorage.setItem('audio-intel:ui-locale:v1',locale),locale)
 const {state,errors}=await uxFixture(page)
 if(!video){delete state.job.result!.video;state.job.display_name='audio.wav';state.job.purpose=undefined}
 let failed=true
 const bodies:unknown[]=[]
 await page.route('**/api/v1/jobs/ux-video/transcript-export',route=>{
  bodies.push(route.request().postDataJSON())
  if(failed)return route.fulfill({status:503,json:{detail:'temporary failure'}})
  return route.fulfill({contentType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers:{'Content-Disposition':"attachment; filename*=UTF-8''%E8%BD%AC%E5%86%99.docx"},body:Buffer.from('PK-DOCX-browser-fixture')})
 })
 await page.goto('/#asr')
 await page.getByRole('tab',{name:locale==='zh-CN'?'任务与结果':'Tasks & results',exact:true}).click()
 await openTranscriptExport(page)
 await page.locator('.transcript-export-modal .export-format').selectOption('docx')
 await assertReachable(page,width)
 await page.screenshot({path:info.outputPath('word-export.png')})
 await downloadConcise(page,'docx')
 await expect(page.locator('.transcript-export-modal [role=alert]')).toContainText('503')
 failed=false
 const waiting=page.waitForEvent('download')
 await downloadConcise(page,'docx')
 const download=await waiting
 expect(download.suggestedFilename()).toBe('转写.docx')
 expect(await download.failure()).toBeNull()
 expect(bodies).toEqual([{format:'docx',redact:true,replacements:[]},{format:'docx',redact:true,replacements:[]}])
 await expect(page.locator('.transcript-export-modal [role=alert]')).toHaveCount(0)
 await assertReachable(page,width)
 expect(errors).toEqual([])
})

for(const width of [1440,390])test(`older capabilities do not offer DOCX ${width}`,async({page})=>{
 await page.setViewportSize({width,height:844})
 const {errors}=await uxFixture(page)
 const caps=await capabilitiesFor(page);delete caps.asr.transcript_export;caps.events.sse=false
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:caps}))
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await openTranscriptExport(page)
 await expect(page.locator('.transcript-export-modal option[value=docx]')).toHaveCount(0)
 expect(errors).toEqual([])
})
