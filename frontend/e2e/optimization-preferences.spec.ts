import {test,expect} from './fixtures'
import {uxFixture} from './ux-fixtures'

for(const width of [1440,390])test(`export preferences persist with safe fallbacks and successful close ${width}`,async({page},info)=>{
 await page.setViewportSize({width,height:900})
 const {state,errors}=await uxFixture(page)
 let available=true,fail=false
 const run={id:'saved',job_id:state.job.id,state:'succeeded',published:true,model:'test',rewrite_mode:'rewrite',total_chars:4,completed_chars:4,completed_units:1,total_units:1,needs_review_units:0,activity_chars:0,progress:1,revision:1,error:null}
 await page.addInitScript(()=>{if(!localStorage.getItem('export-test-seeded')){localStorage.setItem('audio-intel:transcript-export:v1',JSON.stringify({redact:false,rules:'Atlas=项目'}));localStorage.setItem('export-test-seeded','1')}})
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:available?run:null,candidate:null}}))
 await page.route('**/api/v1/jobs/*/transcript-export',async route=>{
  if(fail){await route.fulfill({status:503,json:{detail:'temporary'}});return}
  state.exports.push(route.request().postDataJSON());await route.fulfill({contentType:'application/octet-stream',body:'download'})
 })
 const open=async()=>{await page.getByRole('button',{name:'导出转写',exact:true}).click();await expect(page.getByRole('dialog')).toBeVisible()}
 const download=async()=>{const event=page.waitForEvent('download');await page.getByRole('dialog').getByRole('button',{name:'下载',exact:true}).click();await event;await expect(page.getByRole('dialog')).toHaveCount(0);await expect(page.getByRole('button',{name:'导出转写',exact:true})).toBeFocused()}
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(page.getByRole('button',{name:'优化稿',exact:true})).toBeEnabled()
 await open();await expect(page.getByRole('checkbox',{name:'脱敏导出',exact:true})).not.toBeChecked()
 await page.getByLabel('导出内容',{exact:true}).selectOption('optimized');await page.locator('.export-format').selectOption('docx');await download()
 await page.reload();await expect(page.getByRole('button',{name:'优化稿',exact:true})).toBeEnabled();await open()
 await expect(page.getByLabel('导出内容',{exact:true})).toHaveValue('optimized');await expect(page.locator('.export-format')).toHaveValue('docx')
 await page.getByLabel('导出内容',{exact:true}).selectOption('original');await page.locator('.export-format').selectOption('txt')
 await page.getByLabel('导出内容',{exact:true}).selectOption('comparison');await expect(page.locator('.export-format')).toHaveValue('pdf')
 await page.locator('.export-format').selectOption('docx');await download()
 available=false;await page.reload();await expect(page.getByRole('button',{name:'优化稿',exact:true})).toBeDisabled();await open()
 await expect(page.getByLabel('导出内容',{exact:true})).toHaveCount(0);await expect(page.locator('.export-format')).toHaveValue('txt');await download()
 expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('audio-intel:transcript-export:v1')!).textVersion)).toBe('comparison')
 available=true;await page.reload();await expect(page.getByRole('button',{name:'优化稿',exact:true})).toBeEnabled();await open()
 await expect(page.getByLabel('导出内容',{exact:true})).toHaveValue('comparison');await expect(page.locator('.export-format')).toHaveValue('docx')
 fail=true;await page.getByRole('dialog').getByRole('button',{name:'下载',exact:true}).click();await expect(page.getByRole('dialog').getByRole('alert')).toBeVisible()
 await expect(page.getByRole('dialog')).toBeVisible();fail=false;await download()
 await open();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 await page.screenshot({path:info.outputPath('export-preferences.png')})
 expect(errors.filter(e=>!e.includes('503'))).toEqual([])
})

for(const width of [1440,390])test(`rewrite mode selection remembered and published snapshot shown ${width}`,async({page},info)=>{
 await page.setViewportSize({width,height:900})
 const {state,errors}=await uxFixture(page)
 let published:Record<string,unknown>|null=null
 const requests:Record<string,unknown>[]=[]
 await page.route('**/api/v1/jobs/*/transcript-optimization',async route=>{
  if(route.request().method()==='POST'){
   const body=route.request().postDataJSON();requests.push(body)
   published={id:'mode-'+requests.length,job_id:state.job.id,state:'succeeded',published:true,model:'test',rewrite_mode:body.rewrite_mode,total_chars:4,completed_chars:4,completed_units:1,total_units:1,activity_chars:0,progress:1,revision:1,error:null}
  }
  await route.fulfill({json:{enabled:true,published,candidate:null}})
 })
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.getByRole('button',{name:'优化表达',exact:true}).click()
 const dialog=page.getByRole('dialog')
 await expect(dialog.getByRole('radio',{name:/流畅重写/})).toBeChecked()
 await dialog.getByRole('radio',{name:/自然润色/}).check();await dialog.getByRole('button',{name:'取消',exact:true}).click()
 await page.getByRole('button',{name:'优化表达',exact:true}).click();await expect(dialog.getByRole('radio',{name:/自然润色/})).toBeChecked()
 await dialog.getByRole('radio',{name:/深度成稿/}).check()
 for(const card of await dialog.locator('.optimization-mode-choices label').all()){
  const box=await card.boundingBox();expect(box!.height).toBeGreaterThanOrEqual(44);expect(box!.height).toBeLessThan(180)
  expect(await card.locator('span').evaluate(node=>node.getBoundingClientRect().width)).toBeGreaterThan(200)
 }
 await expect(dialog.getByRole('button',{name:'开始优化',exact:true})).toBeInViewport()
 await page.screenshot({path:info.outputPath('rewrite-modes.png')})
 await dialog.getByRole('button',{name:'开始优化',exact:true}).click()
 await expect(page.locator('.optimization-mode-caption')).toHaveText('改写档位：深度成稿')
 expect(requests[0]).toEqual({regenerate:false,rewrite_mode:'editorial'})
 await page.getByRole('button',{name:'优化选项',exact:true}).click();await page.getByRole('button',{name:'重新优化',exact:true}).click()
 await expect(dialog.getByRole('radio',{name:/深度成稿/})).toBeChecked();await dialog.getByRole('radio',{name:/流畅重写/}).check()
 await dialog.getByRole('button',{name:'重新优化',exact:true}).click()
 await expect(page.locator('.optimization-mode-caption')).toHaveText('改写档位：流畅重写')
 expect(requests[1]).toEqual({regenerate:true,rewrite_mode:'rewrite'})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 expect(errors).toEqual([])
})
