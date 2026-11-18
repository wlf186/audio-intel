import {test,expect} from '@playwright/test'

for(const width of [1440,390])for(const emptyBoundaries of [false,true])test(`narration controls and retained annotations at ${width}px, empty boundaries ${emptyBoundaries}`,async({page})=>{
 await page.setViewportSize({width,height:900})
 const errors:string[]=[]
 page.on('pageerror',e=>errors.push(e.message))
 page.on('console',m=>{if(m.type()==='error')errors.push(m.text())})
 const text='🙂我认为，最\n85\n\n聪明的人能讲清楚。[11]\n\n脚注正文[22]仍然可见。\n\n下一段。'
 const offset=(s:string)=>Array.from(text.slice(0,text.indexOf(s))).length
 const length=Array.from(text).length
 const kinds=['page_number','note_reference','note_body']
 const annotations=[{start:offset('85'),end:offset('85')+2,kind:'page_number',reason:'footer'},
 {start:offset('[11]'),end:offset('[11]')+4,kind:'note_reference',reason:'citation'},
 {start:offset('脚注'),end:offset('下一段')-2,kind:'note_body',reason:'note'},
 {start:offset('[22]'),end:offset('[22]')+4,kind:'note_reference',reason:'nested citation'}]
 let revision='analysis1',reanalyzed=0
 const defaults={skip_enabled:true,skip_kinds:kinds,natural_pauses:true}
 await page.route('**/api/v1/tts/document-imports/**',async route=>{
  const url=new URL(route.request().url()),action=url.pathname.split('/')[6]
  if(action==='preview'){
   const body=route.request().postDataJSON(),options=body.narration_options||defaults
   return route.fulfill({json:{preview_revision:JSON.stringify(options)+revision,analysis_revision:revision,narration_options:options,title:'朗读测试',warnings:[],segmentation_mode:'auto',target_section_chars:10000,total_chars:length,sections:[{id:'s1',index:1,title:'朗读测试',start:0,end:length,char_count:length,basis:'heading',speakable:true}]}})
  }
  if(action==='text')return route.fulfill({json:{text,start:0,total_chars:length,analysis_revision:revision,source_format:'pdf',annotations,boundaries:emptyBoundaries?[]:[{offset:offset('下一段'),kind:'paragraph'}]}})
  if(action==='reanalyze'){reanalyzed++;revision='analysis2'}
  return route.fulfill({status:action==='reanalyze'?202:200,json:{id:'narration',name:'朗读测试.pdf',state:'ready',size_bytes:100,storage_bytes:100,created_at:new Date().toISOString(),updated_at:new Date().toISOString(),metadata:{narration_version:1}}})
 })
 await page.addInitScript(()=>sessionStorage.setItem('audio-intel:document-draft',JSON.stringify({version:3,importId:'narration',section_ids:['s1']})))
 await page.goto('/#tts')
 await page.getByRole('tab',{name:'文档合成',exact:true}).click()
 await expect(page.locator('.document-sections li')).toHaveCount(1)
 await page.getByRole('button',{name:/^朗读处理 ·/}).click()
 await expect(page.getByLabel('跳过辅助文本',{exact:true})).toBeChecked()
 await expect(page.getByLabel('自然段落与章节停顿',{exact:true})).toBeChecked()
 await page.getByLabel('脚注正文',{exact:true}).uncheck()
 await expect.poll(()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('audio-intel:document-draft')!).narration_options.skip_kinds.sort())).toEqual(['note_reference','page_number'])
 await page.keyboard.press('Escape')
 await page.getByRole('button',{name:'预览正文',exact:true}).click()
 await expect(page.locator('.document-reader-body pre')).toHaveText(text)
 await expect(page.locator('.document-annotation-note_body').filter({hasText:/^脚注正文$/})).not.toHaveClass(/is-skipped/)
 await expect(page.locator('.document-annotation').filter({hasText:/^\[22\]$/})).toHaveClass(/is-skipped/)
 await expect(page.locator('.document-annotation-page_number')).toHaveClass(/is-skipped/)
 await page.getByRole('tab',{name:'连续阅读',exact:true}).click()
 await expect(page.locator('.document-flow-paragraph')).toHaveCount(emptyBoundaries?1:2)
 await expect(page.locator('.document-flow-paragraph p').first()).toContainText('最聪明的人')
 await expect(page.locator('.document-reader-notes')).toContainText('85')
 await expect(page.locator('.document-reader-notes')).toContainText('脚注正文')
 await expect(page.locator('.document-reader-notes .document-annotation').filter({hasText:/^\[22\]$/})).toHaveClass(/is-skipped/)
 const geometry=await page.locator('.document-reader-body').evaluate(el=>{
  let top=0,bottom=innerHeight,node:HTMLElement|null=el as HTMLElement
  while(node){const r=node.getBoundingClientRect();if(node===el||/(auto|scroll|hidden|clip)/.test(getComputedStyle(node).overflowY)){top=Math.max(top,r.top);bottom=Math.min(bottom,r.bottom)}node=node.parentElement}
  return {visible:Math.max(0,bottom-top),panel:el.closest('.document-text-preview')!.getBoundingClientRect().height,overflow:document.documentElement.scrollWidth>innerWidth}
 })
 expect(geometry.overflow).toBe(false)
 if(width===1440){expect(geometry.visible).toBeGreaterThanOrEqual(360);expect(geometry.visible/geometry.panel).toBeGreaterThanOrEqual(.6)}
 await page.screenshot({path:`../logs/narration-reader-${width}.png`})
 await page.getByRole('button',{name:'朗读处理',exact:true}).click()
 const settings=page.getByRole('dialog',{name:'朗读处理',exact:true})
 await settings.getByLabel('自然段落与章节停顿',{exact:true}).uncheck()
 await expect.poll(()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('audio-intel:document-draft')!).narration_options.natural_pauses)).toBe(false)
 await page.keyboard.press('Escape')
 await expect(page.locator('.document-reader-body')).toBeVisible()
 await page.keyboard.press('Escape')
 await page.getByRole('button',{name:/^朗读处理 ·/}).click()
 await page.getByRole('button',{name:'重新分析文档',exact:true}).click()
 await expect.poll(()=>reanalyzed).toBe(1)
 await expect(page.locator('.document-sections li')).toHaveCount(1)
 expect(errors).toEqual([])
})

for(const [width,height,minRows] of [[1440,900,4],[1280,720,2]])test(`document settings preserve visible selection space at ${width}px`,async({page})=>{
 await page.setViewportSize({width,height})
 let failNext=true
 const sections=Array.from({length:24},(_,i)=>({id:String(i),index:i+1,title:`第 ${i+1} 章`,start:300*i,end:300*(i+1),char_count:300,spoken_chars:280,basis:'heading',speakable:true}))
 const defaults={skip_enabled:true,skip_kinds:['page_number','note_reference','note_body'],natural_pauses:true}
 await page.route('**/api/v1/tts/document-imports/layout**',route=>{
  if(!route.request().url().endsWith('/preview'))return route.fulfill({json:{id:'layout',name:'排版走查.pdf',state:'ready',metadata:{narration_version:1}}})
  const options=route.request().postDataJSON().narration_options||defaults
  if(!options.natural_pauses&&failNext){failNext=false;return route.fulfill({status:503,json:{detail:'Preview temporarily unavailable'}})}
  return route.fulfill({json:{preview_revision:JSON.stringify(options),analysis_revision:'a',narration_options:options,annotation_counts:{page_number:12},segmentation_mode:'auto',target_section_chars:10000,title:'排版走查',total_chars:7200,warnings:[],sections}})
 })
 await page.addInitScript(()=>sessionStorage.setItem('audio-intel:document-draft',JSON.stringify({version:3,importId:'layout'})))
 await page.goto('/#tts')
 await page.getByRole('tab',{name:'文档合成',exact:true}).click()
 const list=page.locator('.document-sections')
 await expect(list.locator('li')).toHaveCount(20)
 const rows=await list.evaluate(el=>{
  let top=0,bottom=innerHeight
  for(let node:HTMLElement|null=el;node;node=node.parentElement){
   if(node===el||/(auto|scroll|hidden|clip)/.test(getComputedStyle(node).overflowY)){
    const box=node.getBoundingClientRect();top=Math.max(top,box.top);bottom=Math.min(bottom,box.bottom)
   }
  }
  return [...el.children].filter(row=>{const box=row.getBoundingClientRect();return box.top>=top&&box.bottom<=bottom}).length
 })
 expect(rows).toBeGreaterThanOrEqual(minRows)
 await list.locator('input').first().uncheck()
 const box=await list.boundingBox()
 const scroll=await list.evaluate(el=>{el.scrollTop=120;return el.scrollTop})
 const settings=page.getByRole('button',{name:/^朗读处理 ·/})
 await settings.click()
 const dialog=page.getByRole('dialog',{name:'朗读处理'})
 await expect(dialog).toContainText('全文 12 处')
 await dialog.getByLabel('自然段落与章节停顿').uncheck()
 await expect(dialog.getByRole('alert')).toContainText('temporarily unavailable')
 await dialog.getByRole('button',{name:'重试'}).click()
 await expect(dialog.getByRole('alert')).toHaveCount(0)
 await page.keyboard.press('Escape')
 await expect(settings).toBeFocused()
 expect(await list.boundingBox()).toEqual(box)
 expect(await list.evaluate(el=>el.scrollTop)).toBe(scroll)
 await expect(list.locator('input').first()).not.toBeChecked()
 await expect(page.locator('.document-selection-bar')).toContainText('6,440')
})
