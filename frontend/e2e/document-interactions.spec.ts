import {test,expect,type Page} from '@playwright/test'

type ImportItem={id:string;name:string;state:string;size_bytes:number;storage_bytes:number;created_at:string;updated_at:string;metadata:Record<string,unknown>}
async function workspace(page:Page,bases=['heading']){
 const now=new Date().toISOString()
 const source=('第一行中文。\n第二行中文。\n\nAlpha beta\ncontinued words.\n\n<script>plain text only</script>\n\n').repeat(200).slice(0,10000)
 const state={
  imports:Array.from({length:43},(_,i):ImportItem=>({id:`doc${i}`,name:`文档 ${i} — 长文件名与运行维护说明.pdf`,state:i===3?'running':i===4?'queued':'ready',size_bytes:100,storage_bytes:200,created_at:now,updated_at:now,metadata:{}})),
  deleted:[] as string[],failures:new Map<string,{status:number;active?:boolean}>(),uploads:0,uploadFailure:false,listFailure:false,
  gate:undefined as Promise<void>|undefined,uploadGate:undefined as Promise<void>|undefined,
  errors:[] as string[],source,
 }
 page.on('pageerror',error=>state.errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')state.errors.push(message.text())})
 await page.route('**/api/v1/tts/document-imports**',async route=>{
  const request=route.request(),url=new URL(request.url()),parts=url.pathname.split('/').filter(Boolean)
  const id=parts[4],action=parts[5]
  if(!id){
   if(request.method()==='POST'){
    state.uploads++
    if(state.uploadGate)await state.uploadGate
    if(state.uploadFailure)return route.fulfill({status:503,json:{detail:'Upload unavailable'}})
    const item={...state.imports[0],id:'uploaded',name:'new.pdf',state:'ready'}
    state.imports.unshift(item)
    return route.fulfill({status:202,json:item})
   }
   if(state.listFailure)return route.fulfill({status:500,json:{detail:'List refresh unavailable'}})
   const offset=Number(url.searchParams.get('offset')||0),limit=Number(url.searchParams.get('limit')||21)
   return route.fulfill({json:state.imports.slice(offset,offset+limit)})
  }
  const item=state.imports.find(item=>item.id===id)
  if(request.method()==='DELETE'){
   state.deleted.push(id)
   if(state.gate)await state.gate
   const failure=state.failures.get(id)
   state.failures.delete(id)
   if(failure){
    if(failure.active&&item)item.state='running'
    if(failure.status===404)state.imports=state.imports.filter(item=>item.id!==id)
    return route.fulfill({status:failure.status,json:{detail:failure.active?'Document import is active':'Removal unavailable'}})
   }
   state.imports=state.imports.filter(item=>item.id!==id)
   return route.fulfill({status:204})
  }
  if(!item)return route.fulfill({status:404,json:{detail:'Document import not found'}})
  if(action==='preview'){
   const input=request.postDataJSON() as {segmentation_mode:string;target_section_chars:number}
   const sections=Array.from({length:40},(_,i)=>({id:`section${i}`,index:i+1,title:`第 ${i+1} 段：正文与详细说明`,start:i*10000,end:(i+1)*10000,char_count:10000,basis:input.segmentation_mode==='length'?'paragraph':bases[i%bases.length]}))
   return route.fulfill({json:{preview_revision:`${input.segmentation_mode}-${input.target_section_chars}`,segmentation_mode:input.segmentation_mode,target_section_chars:input.target_section_chars,sections,total_chars:400000,title:item.name,warnings:[]}})
  }
  if(action==='text'){
   const start=Number(url.searchParams.get('start'))%10000,limit=Number(url.searchParams.get('limit'))
   return route.fulfill({json:{text:source.slice(start,start+limit),start,total_chars:400000}})
  }
  return route.fulfill({json:item})
 })
 await page.addInitScript(()=>{
  if(!sessionStorage.getItem('audio-intel:document-draft'))sessionStorage.setItem('audio-intel:document-draft',JSON.stringify({version:2,importId:'doc0',preview_revision:'auto-10000',segmentation_mode:'auto',target_section_chars:10000,section_ids:['section0']}))
 })
 await page.goto('/#tts')
 await page.getByRole('tab',{name:'文档合成',exact:true}).click()
 await expect(page.locator('.document-sections li').first()).toBeVisible()
 return state
}
const draft=(page:Page)=>page.evaluate(()=>sessionStorage.getItem('audio-intel:document-draft'))
const library=(page:Page)=>page.getByRole('dialog',{name:'已导入文档',exact:true})
const confirm=(page:Page)=>page.getByRole('dialog',{name:'移除所选文档',exact:true})

test('document sources preserve drafts while browsing and when an upload fails or is cancelled',async({page})=>{
 const state=await workspace(page)
 const original=await draft(page)
 await page.getByRole('button',{name:'已导入文档',exact:true}).click()
 await expect(library(page).getByRole('button',{name:'当前使用中'})).toBeDisabled()
 await page.keyboard.press('Escape')
 expect(await draft(page)).toBe(original)
 state.uploadFailure=true
 await page.locator('.document-upload input').setInputFiles({name:'new.pdf',mimeType:'application/pdf',buffer:Buffer.from('test')})
 await expect(page.locator('.document-error')).toContainText('Upload unavailable')
 expect(await draft(page)).toBe(original)
 await expect(page.locator('.document-sections input:checked')).toHaveCount(1)
 await page.getByRole('button',{name:'关闭提示'}).click()
 state.uploadFailure=false
 let release!:()=>void
 state.uploadGate=new Promise<void>(resolve=>{release=resolve})
 await page.locator('.document-upload input').setInputFiles({name:'new.pdf',mimeType:'application/pdf',buffer:Buffer.from('test')})
 await expect(page.getByRole('button',{name:'取消上传',exact:true})).toBeVisible()
 await page.getByRole('button',{name:'取消上传',exact:true}).click()
 release();state.uploadGate=undefined
 await expect(page.getByRole('button',{name:'关闭提示'})).toBeVisible()
 expect(await draft(page)).toBe(original)
 await page.getByRole('button',{name:'关闭提示'}).click()
 await page.getByRole('button',{name:'取消使用当前文档'}).click()
 expect(await draft(page)).toBeNull()
 await expect(library(page)).toHaveCount(0)
 await page.getByRole('button',{name:'已导入文档',exact:true}).click()
 await library(page).locator('li').filter({hasText:'文档 1 —'}).getByRole('button',{name:'使用此文档'}).click()
 await expect(page.locator('.document-source h2')).toContainText('文档 1 —')
 expect(state.uploads).toBe(2)
 await page.locator('.document-upload input').setInputFiles({name:'replacement.pdf',mimeType:'application/pdf',buffer:Buffer.from('test')})
 await expect(page.locator('.document-source h2')).toHaveText('new.pdf')
 await expect.poll(async()=>JSON.parse((await draft(page))!).importId).toBe('uploaded')
 expect(state.errors.filter(error=>!error.includes('status of 503'))).toEqual([])
})

test('applied split explanations and readable text retain the original source',async({page})=>{
 const state=await workspace(page,['paragraph','toc'])
 await expect(page.locator('.document-split-summary')).toContainText('章前正文按目标 10,000')
 await expect(page.locator('.document-sections li').first()).toContainText('字数分段 · 段落边界')
 await expect(page.locator('.document-sections li').nth(1)).toContainText('结构分段 · 文档目录')
 await page.getByRole('button',{name:/调整分段/}).click()
 await expect(page.getByLabel('备用分段目标字数')).toHaveValue('10000')
 await page.locator('.document-controls select').selectOption('length')
 await page.getByLabel('目标分段字数').fill('2000')
 await expect(page.locator('.document-split-summary')).toContainText('章前正文按目标 10,000')
 await page.getByRole('button',{name:'重新分段',exact:true}).click()
 await expect(page.locator('.document-split-summary')).toHaveText('已按目标 2,000 字符分段。')
 const original=await draft(page)
 await page.locator('.document-sections li').first().getByRole('button',{name:'预览正文'}).click()
 await expect(page.getByRole('tab',{name:'原文换行',exact:true})).toHaveAttribute('aria-selected','true')
 const tabs=page.getByRole('tablist',{name:'正文显示方式'})
 const originalTab=tabs.getByRole('tab',{name:'原文换行'}),flowTab=tabs.getByRole('tab',{name:'连续阅读'})
 await originalTab.focus();await page.keyboard.press('ArrowRight')
 await expect(flowTab).toBeFocused();await expect(flowTab).toHaveAttribute('aria-selected','true')
 await expect(page.getByRole('tabpanel',{name:'连续阅读',exact:true})).toBeVisible()
 await page.keyboard.press('Home');await expect(originalTab).toBeFocused()
 await page.keyboard.press('End');await expect(flowTab).toBeFocused()
 await page.keyboard.press('ArrowLeft');await expect(originalTab).toBeFocused()
 await expect(originalTab).toHaveAttribute('tabindex','0');await expect(flowTab).toHaveAttribute('tabindex','-1')
 const pagination=page.locator('.document-reader-navigation').last()
 await expect(pagination.getByRole('button',{name:'上一页',exact:true})).toBeDisabled()
 expect(await page.locator('.document-reader-body pre').textContent()).toBe(state.source.slice(0,4000))
 await page.getByRole('tab',{name:'连续阅读',exact:true}).click()
 const blocks=await page.locator('.document-reader-flow p').allTextContents()
 expect(blocks).toEqual(state.source.slice(0,4000).split(/\n[^\S\n]*\n+/))
 await expect(page.locator('.document-reader-body script')).toHaveCount(0)
 await page.locator('.document-reader-navigation').last().getByRole('button',{name:'下一页',exact:true}).click()
 await expect(page.locator('.document-reader-navigation').last()).toContainText('正文第 2 / 3 页')
 await pagination.getByRole('button',{name:'下一页',exact:true}).click()
 await expect(pagination).toContainText('正文第 3 / 3 页')
 await expect(pagination.getByRole('button',{name:'下一页',exact:true})).toBeDisabled()
 await pagination.getByRole('button',{name:'上一页',exact:true}).click()
 await expect(pagination).toContainText('正文第 2 / 3 页')
 await originalTab.click();await expect(pagination).toContainText('正文第 2 / 3 页')
 await flowTab.click()
 await expect(page.getByRole('tab',{name:'连续阅读',exact:true})).toHaveAttribute('aria-selected','true')
 await page.getByRole('button',{name:'下一分段',exact:true}).click()
 await expect(page.locator('.document-reader-navigation').first()).toContainText('分段 2 / 40')
 await page.keyboard.press('Escape')
 expect(await draft(page)).toBe(original)
 await page.locator('.document-sections li').first().getByRole('button',{name:'预览正文'}).click()
 await expect(page.getByRole('tab',{name:'原文换行',exact:true})).toHaveAttribute('aria-selected','true')
 expect(state.errors).toEqual([])
})

test('cross-page removal is sequential, recoverable and independent of the current document',async({page})=>{
 const state=await workspace(page)
 const original=await draft(page)
 state.failures.set('doc1',{status:500});state.failures.set('doc22',{status:404})
 await page.getByRole('button',{name:'已导入文档',exact:true}).click()
 await expect(library(page).getByLabel('选择移除 文档 3 — 长文件名与运行维护说明.pdf',{exact:true})).toBeDisabled()
 await library(page).getByLabel('选择移除 文档 1 — 长文件名与运行维护说明.pdf',{exact:true}).check()
 await library(page).getByRole('button',{name:'下一页',exact:true}).click()
 for(const n of [21,22])await library(page).getByLabel(`选择移除 文档 ${n} — 长文件名与运行维护说明.pdf`,{exact:true}).check()
 await expect(library(page)).toContainText('已选 3 个文档')
 let release!:()=>void
 state.gate=new Promise<void>(resolve=>{release=resolve})
 await library(page).getByRole('button',{name:'移除所选文档',exact:true}).click()
 await expect(confirm(page).locator('.document-removal-names li')).toHaveCount(3)
 await confirm(page).getByRole('button',{name:'移除所选文档',exact:true}).click()
 await expect.poll(()=>state.deleted.length).toBe(1)
 await page.keyboard.press('Escape')
 await expect(confirm(page)).toBeVisible()
 release();state.gate=undefined
 await expect(library(page).locator('.document-removal-result')).toContainText('已移除 1 个，已不存在 1 个，失败 1 个')
 expect(state.deleted).toEqual(['doc1','doc21','doc22'])
 expect(await draft(page)).toBe(original)
 await expect(library(page)).toContainText('已选 1 个文档')
 await library(page).getByRole('button',{name:'移除所选文档',exact:true}).click()
 await confirm(page).getByRole('button',{name:'移除所选文档',exact:true}).click()
 await expect(library(page).locator('.document-removal-result')).toContainText('已移除 1 个，已不存在 0 个，失败 0 个')
 expect(state.deleted).toEqual(['doc1','doc21','doc22','doc1'])
 await library(page).getByRole('button',{name:'上一页',exact:true}).click()
 await library(page).getByLabel('选择移除 文档 0 — 长文件名与运行维护说明.pdf',{exact:true}).check()
 await library(page).getByRole('button',{name:'移除所选文档',exact:true}).click()
 await expect(confirm(page)).toContainText('包含当前使用的文档')
 await confirm(page).getByRole('button',{name:'移除所选文档',exact:true}).click()
 await expect.poll(()=>draft(page)).toBeNull()
 expect(state.errors.filter(error=>!/status of (404|500)/.test(error))).toEqual([])
})

test('refresh removes active selections and closing the library clears only bulk selection',async({page})=>{
 const state=await workspace(page,['paragraph'])
 await expect(page.locator('.document-split-summary')).toContainText('未识别到可靠结构')
 const original=await draft(page)
 await page.getByRole('button',{name:'已导入文档',exact:true}).click()
 await library(page).getByLabel('全选本页可移除文档',{exact:true}).check()
 await expect(library(page)).toContainText('已选 18 个文档')
 state.imports[1].state='running'
 await library(page).getByRole('button',{name:'刷新列表'}).click()
 await expect(library(page)).toContainText('已选 17 个文档')
 await expect(library(page)).toContainText('已取消勾选')
 await page.keyboard.press('Escape')
 await page.getByRole('button',{name:'已导入文档',exact:true}).click()
 await expect(library(page)).toContainText('已选 0 个文档')
 expect(await draft(page)).toBe(original)
 expect(state.deleted).toEqual([])
 expect(state.errors).toEqual([])
})

test('off-page active imports are deselected and refresh failures do not hide removal outcomes',async({page})=>{
 const state=await workspace(page)
 state.failures.set('doc1',{status:409,active:true})
 await page.getByRole('button',{name:'已导入文档',exact:true}).click()
 await library(page).getByLabel('选择移除 文档 1 — 长文件名与运行维护说明.pdf',{exact:true}).check()
 await library(page).getByRole('button',{name:'下一页',exact:true}).click()
 await library(page).getByLabel('选择移除 文档 21 — 长文件名与运行维护说明.pdf',{exact:true}).check()
 state.listFailure=true
 await library(page).getByRole('button',{name:'移除所选文档',exact:true}).click()
 await confirm(page).getByRole('button',{name:'移除所选文档',exact:true}).click()
 await expect(library(page).locator('.document-removal-result')).toContainText('已移除 1 个，已不存在 0 个，失败 1 个')
 await expect(library(page)).toContainText('List refresh unavailable')
 await expect(library(page)).toContainText('已选 0 个文档')
 await expect(library(page)).toContainText('已取消勾选')
 state.listFailure=false
 await library(page).getByRole('button',{name:'重试',exact:true}).click()
 await expect(library(page).locator('.document-import-list')).toBeVisible()
 expect(state.errors.filter(error=>!/status of (409|500)/.test(error))).toEqual([])
})

test('removing the final page returns to a valid page',async({page})=>{
 const state=await workspace(page)
 const original=await draft(page)
 await page.getByRole('button',{name:'已导入文档',exact:true}).click()
 for(let i=0;i<2;i++)await library(page).getByRole('button',{name:'下一页',exact:true}).click()
 await library(page).getByLabel('全选本页可移除文档',{exact:true}).check()
 await expect(library(page)).toContainText('已选 3 个文档')
 await library(page).getByRole('button',{name:'移除所选文档',exact:true}).click()
 await confirm(page).getByRole('button',{name:'移除所选文档',exact:true}).click()
 await expect(library(page).locator('.document-pagination span')).toHaveText('2')
 await expect(library(page).getByRole('button',{name:'下一页',exact:true})).toBeDisabled()
 expect(state.deleted).toEqual(['doc40','doc41','doc42'])
 expect(await draft(page)).toBe(original)
 expect(state.errors).toEqual([])
})

test('authentication loss stops the remaining removal requests',async({page})=>{
 const state=await workspace(page)
 state.failures.set('doc1',{status:401})
 await page.getByRole('button',{name:'已导入文档',exact:true}).click()
 for(const i of [1,2])await library(page).getByLabel(`选择移除 文档 ${i} — 长文件名与运行维护说明.pdf`,{exact:true}).check()
 await library(page).getByRole('button',{name:'移除所选文档',exact:true}).click()
 await confirm(page).getByRole('button',{name:'移除所选文档',exact:true}).click()
 await expect(page.locator('.document-input')).toHaveCount(0)
 expect(state.deleted).toEqual(['doc1'])
 expect(state.errors.filter(error=>!error.includes('status of 401'))).toEqual([])
})

for(const [width,height] of [[1440,900],[1920,1080],[1280,720],[390,844]]){
 test(`document layout and reader remain reachable at ${width}x${height}`,async({page})=>{
  await page.setViewportSize({width,height})
  const state=await workspace(page)
  const panel=page.locator('.document-input'),settings=page.locator('.tts-settings')
  if(width>=1200){
   const left=await panel.boundingBox(),right=await settings.boundingBox()
   expect(Math.abs(left!.y+left!.height-right!.y-right!.height)).toBeLessThanOrEqual(2)
   if(height>=1080)expect((await page.locator('.document-sections').boundingBox())!.height).toBeGreaterThan(440)
   expect((await panel.locator('.document-pagination').boundingBox())!.y).toBeLessThan(height-44)
  }
  await page.screenshot({animations:'disabled',path:`/tmp/document-ui-${width}-${height}.png`,fullPage:true})
  await page.locator('.document-sections li').first().getByRole('button',{name:'预览正文'}).click()
  await page.getByRole('tab',{name:'连续阅读',exact:true}).click()
  await expect(page.locator('.document-reader-flow')).toBeVisible()
  await expect(page.locator('.document-reader-flow p').first()).toHaveCSS('font-size','16px')
  await expect(page.getByRole('tab',{name:'连续阅读',exact:true})).toHaveCSS('border-bottom-color','rgb(244, 237, 0)')
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  for(const control of await page.locator('dialog button:visible').all())expect((await control.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await page.screenshot({animations:'disabled',path:`/tmp/document-reader-${width}-${height}.png`})
  await page.keyboard.press('Escape')
  await page.getByRole('button',{name:'已导入文档',exact:true}).click()
  await library(page).getByLabel('全选本页可移除文档',{exact:true}).check()
  await page.screenshot({animations:'disabled',path:`/tmp/document-imports-${width}-${height}.png`})
  expect(await library(page).evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true)
  const navigation=await library(page).locator('.document-pagination').boundingBox()
  expect(navigation!.y+navigation!.height).toBeLessThanOrEqual(height)
  await expect(library(page).getByRole('button',{name:'关闭已导入文档'})).toBeInViewport()
  await page.keyboard.press('Escape')
  await page.locator('.language-switcher select:visible').first().selectOption('en-US')
  await expect(page.getByText('Upload new document',{exact:true})).toBeVisible()
  await page.locator('.document-sections li').first().getByRole('button',{name:'Preview text'}).click()
  const englishTabs=page.getByRole('tablist',{name:'Text display mode'})
  await englishTabs.getByRole('tab',{name:'Continuous reading',exact:true}).click()
  await expect(page.getByRole('tabpanel',{name:'Continuous reading',exact:true})).toBeVisible()
  const englishPagination=page.locator('.document-reader-navigation').last()
  await expect(englishPagination.getByRole('button',{name:'Previous page',exact:true})).toBeDisabled()
  await englishPagination.getByRole('button',{name:'Next page',exact:true}).click()
  await expect(englishPagination).toContainText('Text page 2 / 3')
  expect(await page.locator('dialog').evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true)
  for(const control of await englishTabs.getByRole('tab').all())expect((await control.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await page.screenshot({animations:'disabled',path:`/tmp/document-reader-en-${width}-${height}.png`})
  await page.keyboard.press('Escape')
  await page.getByRole('button',{name:'Imported documents',exact:true}).click()
  await expect(library(page)).toHaveCount(0)
  expect(await page.locator('dialog').evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true)
  await page.getByLabel('Select removable documents on this page',{exact:true}).check()
  await page.getByRole('button',{name:'Remove selected documents',exact:true}).click()
  const removal=page.getByRole('dialog',{name:'Remove selected documents',exact:true})
  expect(await removal.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true)
  for(const control of await removal.locator('button').all())expect((await control.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await page.screenshot({animations:'disabled',path:`/tmp/document-confirm-${width}-${height}.png`})
  await removal.getByRole('button',{name:'Cancel',exact:true}).click()
  expect(state.errors).toEqual([])
 })
}
