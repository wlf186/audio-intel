import {test,expect} from './fixtures'
import {uxFixture} from './ux-fixtures'

for(const width of [1440,390])test(`call budget preflight override and usage ${width}`,async({page},info)=>{
 await page.setViewportSize({width,height:900})
 const {errors}=await uxFixture(page)
 let published:Record<string,unknown>|null=null
 const submissions:Record<string,unknown>[]=[]
 await page.route('**/settings/transcript-optimization',route=>route.fulfill({json:{enabled:true,base_url:'http://localhost:11434/v1',model:'test',max_llm_calls:100}}))
 await page.route('**/transcript-optimization/estimate',route=>route.fulfill({json:{estimated_calls:120,used_calls:0,minimum_limit:120,recommended_limit:180,max_llm_calls:180,sufficient:true,can_start:true,breakdown:{generation:80,review:40},fingerprint:'b'.repeat(64)}}))
 await page.route('**/api/v1/jobs/*/transcript-optimization',async route=>{
  if(route.request().method()==='POST'){
   const request=route.request().postDataJSON();submissions.push(request)
   published={id:'a'.repeat(32),state:'succeeded',published:true,rewrite_mode:'rewrite',model:'test',calls_limit:request.max_llm_calls,calls_used:86,completed_units:1,total_units:1,revision:1}
  }
  await route.fulfill({json:{enabled:true,published,candidate:null}})
 })
 await page.route('**/transcript-optimization/usage?*',route=>route.fulfill({json:{run_id:'a'.repeat(32),recorded:true,calls_used:86,max_llm_calls:200,remaining_calls:114,estimate:{estimated_calls:120,recommended_limit:180},stages:{generation:40,review:36,group:10},failed_or_interrupted:2}}))
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.getByRole('button',{name:'表达优化',exact:true}).click()
 const dialog=page.getByRole('dialog'),limit=dialog.getByLabel('本次总调用上限')
 await dialog.getByRole('button',{name:'调整上限',exact:true}).click()
 await expect(limit).toHaveValue('180')
 await limit.fill('110');await expect(dialog.getByRole('button',{name:'开始优化',exact:true})).toBeDisabled()
 await limit.fill('150')
 await expect(dialog.getByRole('button',{name:'开始优化',exact:true})).toBeEnabled()
 await expect(dialog).toContainText('低于建议预算')
 await limit.fill('200');await dialog.getByRole('button',{name:'开始优化',exact:true}).click()
 await expect(dialog).toHaveCount(0)
 expect(submissions[0].max_llm_calls).toBe(200)
 expect(submissions[0].estimate_fingerprint).toBe('b'.repeat(64))
 await page.getByRole('button',{name:'表达优化',exact:true}).click()
 await expect(dialog.locator('.optimization-metrics')).toContainText('86 次')
 await dialog.getByText('用量明细',{exact:true}).click()
 await expect(dialog).toContainText('已使用 86／200 次，剩余 114 次')
 await dialog.getByRole('button',{name:'重新优化',exact:true}).click()
 await dialog.getByRole('button',{name:'调整上限',exact:true}).click()
 await expect(dialog.getByLabel('本次总调用上限')).toHaveValue('180')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 for(const input of await dialog.locator('input[type=number]').all())expect((await input.boundingBox())!.height).toBeGreaterThanOrEqual(44)
 await page.screenshot({path:info.outputPath('call-budget.png'),fullPage:true})
 expect(errors).toEqual([])
})

for(const width of [1440,390])test(`settings omit the legacy cap and preserve token controls ${width}`,async({page},info)=>{
 await page.setViewportSize({width,height:900})
 const {errors}=await uxFixture(page)
 let config={base_url:'http://localhost:11434/v1',model:'test',enabled:true,use_proxy:false,context_length:32768,max_output_tokens:4096,verified_at:'today',...(width===1440?{max_llm_calls:300}:{})}
 let saved:Record<string,unknown>|undefined
 await page.route('**/settings/transcript-optimization',async route=>{
  if(route.request().method()==='PUT'){
   saved=route.request().postDataJSON()
   config={...config,...saved}
  }
  await route.fulfill({json:config})
 })
 await page.goto('/#system')
 const panel=page.locator('.optimization-settings')
 await expect(panel.getByLabel('总上下文预算（K tokens）')).toHaveValue('32')
 await expect(panel.getByLabel('单次最大输出（K tokens）')).toHaveValue('4')
 await expect(panel.getByText('每次优化最多调用 LLM 次数')).toHaveCount(0)
 await expect(panel.getByRole('button',{name:'保存并启用',exact:true})).toBeDisabled()
 await panel.getByRole('switch',{name:'启用表达优化'}).uncheck()
 await panel.getByRole('button',{name:'保存配置',exact:true}).click()
 await expect(panel).toContainText('配置已保存')
 expect(saved).toMatchObject({context_length:32768,max_output_tokens:4096,enabled:false})
 expect(saved).not.toHaveProperty('max_llm_calls')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 await panel.getByLabel('单次最大输出（K tokens）').scrollIntoViewIfNeeded()
 await page.screenshot({path:info.outputPath('optimization-settings.png')})
 expect(errors).toEqual([])
})

for(const width of [1440,390])test(`automatic budget follows mode and topic selection ${width}`,async({page},info)=>{
 await page.setViewportSize({width,height:900})
 const {errors}=await uxFixture(page)
 let submission:Record<string,unknown>|undefined
 await page.route('**/settings/transcript-optimization',route=>route.fulfill({json:{enabled:true,base_url:'http://localhost:11434/v1',model:'test',max_llm_calls:300}}))
 await page.route('**/transcript-optimization/estimate',route=>{
  const {rewrite_mode,analyze_topics}=route.request().postDataJSON()
  const count=analyze_topics?120:rewrite_mode==='editorial'?40:31
  const cap=analyze_topics?180:rewrite_mode==='editorial'?60:50
  return route.fulfill({json:{estimated_calls:count,used_calls:0,minimum_limit:count,recommended_limit:cap,max_llm_calls:cap,sufficient:true,can_start:true,breakdown:{generation:count},fingerprint:'c'.repeat(64)}})
 })
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>{
  if(route.request().method()==='POST')submission=route.request().postDataJSON()
  return route.fulfill({json:{enabled:true,published:null,candidate:null}})
 })
 await page.goto('/#asr')
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.getByRole('button',{name:'表达优化',exact:true}).click()
 const dialog=page.getByRole('dialog')
 await expect(dialog).toContainText('预计调用 31 次 · 本次上限 50 次')
 await dialog.getByRole('button',{name:'调整上限'}).click()
 await dialog.getByLabel('本次总调用上限').fill('80')
 await dialog.getByRole('radio',{name:/深度成稿/}).check()
 await expect(dialog).toContainText('预计调用 40 次 · 本次上限 60 次')
 await dialog.getByRole('switch',{name:'识别全片主要议题'}).check()
 await expect(dialog).toContainText('预计调用 120 次 · 本次上限 180 次')
 const button=dialog.getByRole('button',{name:'调整上限'})
 const lines=await button.evaluate(el=>{const range=document.createRange();range.selectNodeContents(el);return range.getClientRects().length})
 expect(lines).toBe(1)
 await dialog.screenshot({path:info.outputPath('automatic-call-budget.png')})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 await dialog.getByRole('button',{name:'开始优化',exact:true}).click()
 await expect(dialog).toHaveCount(0)
 expect(submission).toEqual({rewrite_mode:'editorial',analyze_topics:true,regenerate:false,estimate_fingerprint:'c'.repeat(64)})
 expect(errors).toEqual([])
})
