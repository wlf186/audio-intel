import {expect,test,type Page} from '@playwright/test'

async function documentWorkspace(page:Page,allModels=false){
 const now=new Date().toISOString()
 const sections=Array.from({length:2000},(_,i)=>({id:`section-${i}`,index:i+1,title:`第 ${i+1} 章 长篇文档的结构说明、实施细则与维护注意事项 Long document section`,start:i*2000,end:(i+1)*2000,char_count:2000,basis:'heading'}))
 const record={id:'workbench',name:'年度项目实施与运行维护管理规范（完整版本与补充材料）.docx',size_bytes:120000,storage_bytes:160000,created_at:now,updated_at:now,state:'ready',metadata:{total_chars:4000000}}
 const preview={preview_revision:'workbench-revision',segmentation_mode:'auto',target_section_chars:10000,sections,total_chars:4000000,title:record.name,warnings:['部分表格以逐行文字提取，请预览关键段落。']}
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname
  if(path==='/api/v1/jobs')return route.fulfill({json:{items:[],count:0,total:0,offset:0,limit:100,has_more:false}})
  if(path==='/api/v1/tts/document-imports/workbench')return route.fulfill({json:record})
  if(path.endsWith('/workbench/preview'))return route.fulfill({json:preview})
  if(path.endsWith('/workbench/text'))return route.fulfill({json:{text:'可分页阅读的文档正文。\n'.repeat(100)}})
  if(path==='/api/v1/tts/document-imports')return route.fulfill({json:[record]})
  if(path==='/api/v1/capabilities'){
   const response=await route.fetch();const body=await response.json();body.events={...body.events,sse:false};if(allModels)for(const model of body.tts.model_capabilities)model.installed=true
   return route.fulfill({response,json:body})
  }
  return route.continue()
 })
 await page.addInitScript(()=>{
  sessionStorage.setItem('audio-intel:document-draft',JSON.stringify({version:2,importId:'workbench',preview_revision:'workbench-revision',segmentation_mode:'auto',target_section_chars:10000,section_ids:['section-0']}))
 })
 await page.goto('/#tts')
 await page.getByRole('tab',{name:'文档合成',exact:true}).click()
 await expect(page.locator('.document-sections li').first()).toBeVisible()
}

for(const [width,height] of [[1440,900],[1280,720],[1024,768],[720,450],[390,844]]){
 test(`document workbench is reachable without overlap at ${width}px`,async({page})=>{
  const errors:string[]=[]
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())})
  await page.setViewportSize({width,height})
  await documentWorkspace(page)
  await expect(page).toHaveTitle('语音合成 · Sandevistan-Audio')
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  await expect(page.locator('.document-sections li')).toHaveCount(width>=1200?20:10)
  await expect(page.locator('.document-select-all input')).toHaveJSProperty('indeterminate',true)
  const main=await page.locator('.document-input').boundingBox()
  const submit=await page.locator('.submission-actions').boundingBox()
  expect(main&&submit).toBeTruthy()
  expect(width>=1200?submit!.x>=main!.x+main!.width:submit!.y>=main!.y+main!.height).toBe(true)
  if(width>=1200){
   expect(submit!.y+submit!.height).toBeLessThanOrEqual(height-44)
   expect((await page.locator('.document-input').boundingBox())!.height).toBeLessThan(900)
  }
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  await page.getByRole('button',{name:/调整分段/}).click()
  if(width>=1200){
   const select=await page.locator('.document-controls select').boundingBox(),button=await page.locator('.document-controls button').boundingBox()
   expect(Math.abs(select!.y+select!.height-button!.y-button!.height)).toBeLessThanOrEqual(2)
  }
  await page.getByRole('button',{name:/调整分段/}).click()
  const trigger=page.locator('.document-sections li').first().getByRole('button',{name:'预览正文'})
  await trigger.scrollIntoViewIfNeeded()
  const scrollBefore=await page.locator('.document-sections').evaluate(e=>e.scrollTop)
  await trigger.click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.locator('.document-text-preview pre')).toContainText('可分页阅读')
  expect(await page.getByRole('dialog').evaluate(dialog=>{const close=dialog.querySelector('.modal-close')!.getBoundingClientRect();const range=document.createRange();range.selectNodeContents(dialog.querySelector('h2')!);return [...range.getClientRects()].some(r=>r.left<close.right&&r.right>close.left&&r.top<close.bottom&&r.bottom>close.top)})).toBe(false)
  if(width===1440||width===390)await page.screenshot({path:`/tmp/document-workbench-reader-${width}.png`,fullPage:false})
  await page.getByRole('button',{name:'下一章节',exact:true}).click()
  await expect(page.getByRole('dialog').getByRole('heading')).toContainText('第 2 章')
  await page.keyboard.press('Escape')
  await expect(trigger).toBeFocused()
  expect(await page.locator('.document-sections').evaluate(e=>e.scrollTop)).toBe(scrollBefore)
  await page.locator('.document-pagination').getByRole('button',{name:'下一页'}).click()
  await page.locator('.document-sections li input').first().check()
  await expect(page.locator('.document-selection-bar')).toContainText('已选 2 段')
  await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
  await expect(page.locator('.document-input')).toBeHidden()
  await page.getByRole('tab',{name:'文档合成',exact:true}).click()
  await expect(page.locator('.document-selection-bar')).toContainText('已选 2 段')
  await expect(page.locator('.document-sections li input').first()).toBeChecked()
  if(width<1200){
   await expect(page.locator('.tts-settings-body')).toBeHidden()
   await page.locator('.document-settings-toggle').click()
   await expect(page.getByLabel('TTS 计算设备')).toBeVisible()
   await page.locator('.document-settings-toggle').click()
  }
  await page.getByRole('tab',{name:'文本合成',exact:true}).click()
  await page.locator('.text-editor textarea').fill('文档与文本草稿应分别保留')
  await page.getByRole('tab',{name:'文档合成',exact:true}).click()
  if(width<1200)await page.locator('.document-settings-toggle').click()
  await page.getByRole('button',{name:'恢复默认配置'}).click()
  await expect(page.getByRole('tab',{name:'文档合成',exact:true})).toHaveAttribute('aria-selected','true')
  await expect(page.locator('.document-selection-bar')).toContainText('已选 2 段')
  await expect(page.locator('.document-sections li input').first()).toBeChecked()
  if(width<1200)await page.locator('.document-settings-toggle').click()
  await page.locator('.document-source').scrollIntoViewIfNeeded()
  if(width===1440||width===390)await page.screenshot({path:`/tmp/tts-unified-document-zh-${width}.png`})
  await page.locator('.language-switcher select:visible').first().selectOption('en-US')
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  for(const control of await page.locator('.document-input button:visible,.document-settings-toggle:visible').all())expect((await control.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await page.locator('.document-source').scrollIntoViewIfNeeded()
  await page.screenshot({path:`/tmp/document-workbench-${width}.png`,fullPage:false})
  expect(errors).toEqual([])
 })
}

for(const width of [1440,390]){
 test(`document voice modes retain settings without sidebar overflow at ${width}px`,async({page})=>{
  const errors:string[]=[]
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())})
  await page.setViewportSize({width,height:900})
  await documentWorkspace(page,true)
  if(width<1200)await page.locator('.document-settings-toggle').click()
  await page.getByRole('tab',{name:'声音克隆',exact:true}).click()
  await expect(page.getByLabel('克隆参考 ASR 模型')).toBeVisible()
  await page.locator('#tts-reference-record').click()
  await expect(page.locator('#tts-reference-record-panel')).toBeVisible()
  const panel=page.locator('.tts-settings-body')
  expect(await panel.evaluate(e=>e.scrollWidth<=e.clientWidth+1)).toBe(true)
  await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
  await page.getByRole('tab',{name:'文档合成',exact:true}).click()
  await page.getByRole('tab',{name:'文本合成',exact:true}).click()
  await expect(page.locator('#tts-reference-record')).toHaveAttribute('aria-selected','true')
  await page.getByRole('tab',{name:'文档合成',exact:true}).click()
  await expect(page.locator('#tts-reference-record')).toHaveAttribute('aria-selected','true')
  await page.getByLabel('TTS 模型').selectOption('qwen3-tts-1.7b')
  await page.getByRole('tab',{name:'音色设计',exact:true}).click()
  await page.getByLabel('音色与表达指令').fill('温暖、自然地朗读。')
  expect(await panel.evaluate(e=>e.scrollWidth<=e.clientWidth+1)).toBe(true)
  await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
  await page.getByRole('tab',{name:'文档合成',exact:true}).click()
  await expect(page.getByLabel('音色与表达指令')).toHaveValue('温暖、自然地朗读。')
  await expect(page.getByLabel('TTS 模型')).toHaveValue('qwen3-tts-1.7b')
  await page.screenshot({path:`/tmp/document-workbench-settings-${width}.png`,fullPage:false})
  expect(errors).toEqual([])
 })
}
