import {expect,test,type Page} from '@playwright/test'

test.use({locale:'en-US'})

async function expectNavigationLayout(page:Page,width:number,labels:string[],mode:'full'|'medium'|'short',stacked=false){
 await page.setViewportSize({width,height:900})
 const navigation=page.getByRole('navigation',{name:'Main navigation'})
 const visibleLabels=navigation.locator(`.nav-label-${mode}`)
 await expect(visibleLabels).toHaveText(labels)
 await expect(visibleLabels.first()).toBeVisible()
 const metrics=await navigation.locator('.nav-label').evaluateAll(elements=>elements.map(element=>{
  const visible=[...element.children].find(child=>getComputedStyle(child).display!=='none')
  return {text:visible?.textContent||'',clientWidth:element.clientWidth,scrollWidth:element.scrollWidth}
 }))
 expect(metrics.map(metric=>metric.text)).toEqual(labels)
 for(const metric of metrics)expect(metric.scrollWidth).toBeLessThanOrEqual(metric.clientWidth+1)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 for(const button of await navigation.getByRole('button').all()){
  const box=await button.boundingBox()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x+box!.width).toBeLessThanOrEqual(width+1)
 }
 const slots=await navigation.evaluate(element=>{
  const navigationBox=element.getBoundingClientRect()
  const buttons=[...element.querySelectorAll('button')]
  const firstBox=buttons[0].getBoundingClientRect()
  const lastBox=buttons.at(-1)!.getBoundingClientRect()
  return {columns:getComputedStyle(element).gridTemplateColumns.split(' ').length,tabWidth:firstBox.width,reservedWidth:navigationBox.right-lastBox.right}
 })
 expect(slots.columns).toBe(width>900?7:6)
 expect(Math.abs(slots.reservedWidth-(width>900?slots.tabWidth:0))).toBeLessThanOrEqual(1)
 const firstButton=navigation.getByRole('button').first()
 expect(await firstButton.evaluate(element=>getComputedStyle(element).display)).toBe(stacked?'flex':'grid')
 if(stacked)expect(await firstButton.evaluate(element=>getComputedStyle(element).flexDirection)).toBe('column')
}

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
  if(path==='/api/v1/voiceprints/people')return route.fulfill({json:{items:[]}})
  if(path==='/api/v1/asr/hotword-lists')return route.fulfill({json:{items:[
   {id:'hotwords_voiceprint_people',name:'声纹库人名（全名）',kind:'system',terms:[],term_count:0},
   {id:'hotwords_voiceprint_people_short',name:'声纹库人名（去姓）',kind:'system',terms:[],term_count:0},
  ]}})
  if(path==='/api/v1/tts/voices')return route.fulfill({json:{preset_speakers:['Vivian']}})
  if(path==='/api/v1/capabilities')return route.fulfill({json:{asr:{models:[],languages:['Auto','Chinese','English'],aligner_languages:['Chinese','English'],speaker_count:{min:1,max:15,default:'auto'},voiceprint_library:true},tts:{model_capabilities:[],languages:['Auto','Chinese','English']},limits:{},events:{sse:false}}})
  return route.fulfill({status:404,json:{detail:'not found'}})
 })

 await page.goto('/#tts')
 await expect(page.locator('html')).toHaveAttribute('lang','en-US')
 await expect(page.getByRole('heading',{name:'Speech synthesis'})).toBeVisible()
 const fullLabels=['Transcription','Speech synthesis','Hotword library','Voiceprint library','Task history','System status']
 const mediumLabels=['Transcribe','Synthesize','Hotwords','Voiceprints','Tasks','System']
 await expectNavigationLayout(page,1920,fullLabels,'full')
 await expectNavigationLayout(page,2200,fullLabels,'full')
 await expectNavigationLayout(page,2400,fullLabels,'full')
 await expectNavigationLayout(page,1919,mediumLabels,'medium')
 await expectNavigationLayout(page,1760,mediumLabels,'medium')
 await expectNavigationLayout(page,1470,mediumLabels,'medium')
 await page.screenshot({path:'/tmp/audio-intel-i18n-header-1470.png',fullPage:false})
 await expectNavigationLayout(page,1280,mediumLabels,'medium',true)
 await page.screenshot({path:'/tmp/audio-intel-i18n-header-1280.png',fullPage:false})
 await page.setViewportSize({width:1440,height:900})
 const desktopSwitcher=page.locator('.language-switcher.header')
 expect((await desktopSwitcher.boundingBox())!.width).toBe(38)
 const desktopSelect=page.getByRole('combobox',{name:'Interface language'})
 expect(await desktopSelect.evaluate(element=>getComputedStyle(element).opacity)).toBe('0')
 await desktopSelect.focus()
 expect(await desktopSwitcher.evaluate(element=>getComputedStyle(element).outlineStyle)).toBe('solid')
 const desktopDocs=page.getByRole('link',{name:'Open API documentation'})
 expect((await desktopDocs.boundingBox())!.width).toBe(38)
 await expect(desktopDocs.locator('.full-label')).toBeHidden()
 await expect(desktopDocs.locator('.compact-label')).toBeHidden()
 const editor=page.locator('.text-editor textarea')
 await editor.fill('Keep this draft while switching languages')
 const requestCount=protectedRequests.length
 await desktopSelect.selectOption('zh-CN')
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
 await expectNavigationLayout(page,390,['ASR','TTS','Hotwords','Voices','Tasks','System'],'short',true)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
 const switcher=page.getByRole('combobox',{name:'Interface language'})
 await expect(switcher).toBeVisible()
 expect((await switcher.boundingBox())!.height).toBeGreaterThanOrEqual(44)
 await page.screenshot({path:'/tmp/audio-intel-i18n-mobile.png',fullPage:false})
 await page.setViewportSize({width:1280,height:900})
 await page.goto('/#asr')
 await expect(page.getByRole('checkbox',{name:/Voiceprint names \(full\)/})).toBeVisible()
 await expect(page.getByRole('checkbox',{name:/Voiceprint names \(without surname\)/})).toBeVisible()
 await page.getByRole('combobox',{name:'Interface language'}).selectOption('zh-CN')
 await expect(page.getByRole('checkbox',{name:/声纹库人名（全名）/})).toBeVisible()
 await expect(page.getByRole('checkbox',{name:/声纹库人名（去姓）/})).toBeVisible()
 expect(errors).toEqual([])
})
