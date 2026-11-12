import {test,expect} from './fixtures'
import {uxFixture} from './ux-fixtures'

for(const width of [1440,390])test(`call budget preflight override and usage ${width}`,async({page},info)=>{
 await page.setViewportSize({width,height:900})
 const {errors}=await uxFixture(page)
 let published:Record<string,unknown>|null=null
 const submissions:Record<string,unknown>[]=[]
 await page.route('**/settings/transcript-optimization',route=>route.fulfill({json:{enabled:true,base_url:'http://localhost:11434/v1',model:'test',max_llm_calls:100}}))
 await page.route('**/transcript-optimization/estimate',route=>route.fulfill({json:{estimated_calls:120,used_calls:0,minimum_limit:120,recommended_limit:180,max_llm_calls:100,sufficient:false,can_start:false,breakdown:{generation:80,review:40},fingerprint:'b'.repeat(64)}}))
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
 await expect(limit).toHaveValue('180')
 await limit.fill('110');await expect(dialog.getByRole('button',{name:'开始优化',exact:true})).toBeDisabled()
 await limit.fill('200');await dialog.getByRole('button',{name:'开始优化',exact:true}).click()
 await expect(dialog).toHaveCount(0)
 expect(submissions[0].max_llm_calls).toBe(200)
 expect(submissions[0].estimate_fingerprint).toBe('b'.repeat(64))
 await page.getByRole('button',{name:'表达优化',exact:true}).click()
 await dialog.getByText('本次用量详情',{exact:true}).click()
 await expect(dialog).toContainText('已使用 86／200 次，剩余 114 次')
 await expect(dialog.getByLabel('本次总调用上限')).toHaveValue('180')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 for(const input of await dialog.locator('input[type=number]').all())expect((await input.boundingBox())!.height).toBeGreaterThanOrEqual(44)
 await page.screenshot({path:info.outputPath('call-budget.png'),fullPage:true})
 expect(errors).toEqual([])
})

for(const width of [1440,390])test(`K token settings and independent call limit ${width}`,async({page})=>{
 await page.setViewportSize({width,height:900})
 const {errors}=await uxFixture(page)
 let config={base_url:'http://localhost:11434/v1',model:'test',enabled:true,use_proxy:false,context_length:32768,max_output_tokens:4096,max_llm_calls:300,verified_at:'today'}
 await page.route('**/settings/transcript-optimization',async route=>{
  if(route.request().method()==='PUT')config={...config,...route.request().postDataJSON()}
  await route.fulfill({json:config})
 })
 await page.goto('/#system')
 const panel=page.locator('.optimization-settings')
 await expect(panel.getByLabel('总上下文预算（K tokens）')).toHaveValue('32')
 await expect(panel.getByLabel('单次最大输出（K tokens）')).toHaveValue('4')
 await panel.getByLabel('每次优化最多调用 LLM 次数').fill('500')
 await expect(panel.getByRole('button',{name:'保存并启用',exact:true})).toBeEnabled()
 await panel.getByRole('button',{name:'保存并启用',exact:true}).click()
 await expect(panel).toContainText('配置已保存')
 expect(config.max_llm_calls).toBe(500);expect(config.context_length).toBe(32768);expect(config.max_output_tokens).toBe(4096)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 expect(errors).toEqual([])
})
