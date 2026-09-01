import {expect,test} from '@playwright/test'

test.use({locale:'en-US'})

test('detects, switches, and persists the interface language without reloading protected data',async({page})=>{
 const errors:string[]=[]
 const protectedRequests:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 page.on('request',request=>{const path=new URL(request.url()).pathname;if(['/api/v1/jobs','/api/v1/system','/api/v1/capabilities','/api/v1/voiceprints/people','/api/v1/asr/hotword-lists'].includes(path))protectedRequests.push(path)})
 await page.route('**/api/v1/**',route=>{
  const path=new URL(route.request().url()).pathname
  if(path==='/api/v1/auth/session')return route.fulfill({json:{required:false,authenticated:true}})
  if(path==='/api/v1/health')return route.fulfill({json:{status:'ok'}})
  if(path==='/api/v1/system')return route.fulfill({json:{status:'ok',offline:true,bind:'127.0.0.1:20810',services:['asr','tts'],workers:[],hardware:{},models:[],storage:{data:'/tmp/data'}}})
  if(path==='/api/v1/jobs')return route.fulfill({json:{items:[],count:0,total:0,limit:100,offset:0,has_more:false}})
  if(path==='/api/v1/voiceprints/people'||path==='/api/v1/asr/hotword-lists')return route.fulfill({json:{items:[]}})
  if(path==='/api/v1/tts/voices')return route.fulfill({json:{preset_speakers:['Vivian']}})
  if(path==='/api/v1/capabilities')return route.fulfill({json:{asr:{models:[],languages:['Auto','Chinese','English'],aligner_languages:['Chinese','English'],speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true},tts:{model_capabilities:[],languages:['Auto','Chinese','English']},limits:{},events:{sse:false}}})
  return route.fulfill({status:404,json:{detail:'not found'}})
 })

 await page.goto('/#tts')
 await expect(page.locator('html')).toHaveAttribute('lang','en-US')
 await expect(page.getByRole('heading',{name:'Speech synthesis'})).toBeVisible()
 const editor=page.locator('.text-editor textarea')
 await editor.fill('Keep this draft while switching languages')
 const requestCount=protectedRequests.length
 await page.getByRole('combobox',{name:'Interface language'}).selectOption('zh-CN')
 await expect(page.locator('html')).toHaveAttribute('lang','zh-CN')
 await expect(page.getByRole('heading',{name:'语音合成'})).toBeVisible()
 await expect(editor).toHaveValue('Keep this draft while switching languages')
 await page.waitForTimeout(300)
 expect(protectedRequests).toHaveLength(requestCount)
 await page.reload()
 await expect(page.locator('html')).toHaveAttribute('lang','zh-CN')
 await expect(page.getByRole('heading',{name:'语音合成'})).toBeVisible()
 await page.getByRole('combobox',{name:'界面语言'}).selectOption('en-US')
 await expect(page.getByRole('heading',{name:'Speech synthesis'})).toBeVisible()
 await page.setViewportSize({width:390,height:844})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 const switcher=page.getByRole('combobox',{name:'Interface language'})
 await expect(switcher).toBeVisible()
 expect((await switcher.boundingBox())!.height).toBeGreaterThanOrEqual(44)
 await page.screenshot({path:'/tmp/audio-intel-i18n-mobile.png',fullPage:false})
 expect(errors).toEqual([])
})
