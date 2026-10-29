import {test,expect} from './fixtures'
import {uxFixture} from './ux-fixtures'

for(const width of [1440,390])test(`requested context verification and persistence ${width}`,async({page},info)=>{
 await page.setViewportSize({width,height:900})
 const {errors}=await uxFixture(page)
 let config={base_url:'http://iollama:11434',model:'qwen3.5:9b',use_proxy:false,enabled:false,context_length:16384,model_context_length:262144,observed_context_length:null as number|null,verified_at:''}
 const verified:number[]=[]
 await page.route('**/api/v1/settings/transcript-optimization',async route=>{
  if(route.request().method()==='PUT'){
   const body=route.request().postDataJSON()
   config={...config,...body,verified_at:body.verification_token?'2026-10-29T12:00:00Z':'',observed_context_length:body.verification_token?body.context_length:null}
  }
  await route.fulfill({json:config})
 })
 await page.route('**/settings/transcript-optimization/verify',async route=>{
  const body=route.request().postDataJSON();verified.push(body.context_length)
  await route.fulfill({json:{...config,...body,observed_context_length:body.context_length,verified_at:'2026-10-29T12:00:00Z',verification_token:'context-bound-token'}})
 })
 await page.goto('/#system')
 await expect(page).toHaveTitle(/Sandevistan-Audio/)
 const panel=page.locator('.optimization-settings'),field=panel.getByLabel('请求上下文窗口（tokens）',{exact:true})
 await expect(field).toHaveValue('16384')
 await field.fill('8192')
 await expect(field).toHaveAttribute('aria-invalid','true')
 await expect(panel.getByRole('button',{name:'重新验证',exact:true})).toBeDisabled()
 await field.fill('65536')
 await panel.getByRole('button',{name:'重新验证',exact:true}).click()
 await expect(panel).toContainText('模型验证通过')
 await panel.getByText('连接已验证 · 技术详情',{exact:true}).click()
 await expect(panel).toContainText('最近验证实际窗口 64K · 模型原生上限 256K')
 await expect(panel).toContainText('非实时窗口')
 await panel.getByLabel('启用表达优化').check()
 await panel.getByRole('button',{name:'保存并启用',exact:true}).click()
 await expect(panel).toContainText('配置已保存')
 expect(config.context_length).toBe(65536)
 expect(verified).toEqual([65536])
 await page.reload()
 await expect(field).toHaveValue('65536')
 await field.fill('16384')
 await expect(panel.getByRole('button',{name:'保存并启用',exact:true})).toBeDisabled()
 await expect(panel.getByText('连接已验证 · 技术详情',{exact:true})).toHaveCount(0)
 await panel.getByLabel('启用表达优化').uncheck()
 await panel.getByRole('button',{name:'保存配置',exact:true}).click()
 await expect(panel).toContainText('配置已保存')
 expect(config.enabled).toBe(false)
 expect(config.observed_context_length).toBeNull()
 await panel.scrollIntoViewIfNeeded()
 if(width===390)expect((await field.boundingBox())!.height).toBeGreaterThanOrEqual(44)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 await page.screenshot({path:info.outputPath('context-settings.png'),fullPage:true})
 expect(errors).toEqual([])
})

test('legacy allocation is not fabricated and late verification cannot overwrite a new window',{tag:'@timing'},async({page,responseGate})=>{
 const {errors}=await uxFixture(page)
 const gate=responseGate()
 const config={base_url:'http://iollama:11434',model:'qwen3.5:9b',use_proxy:false,enabled:true,context_length:32768,model_context_length:262144,verified_at:'2026-10-26'}
 await page.route('**/api/v1/settings/transcript-optimization',route=>route.fulfill({json:config}))
 await page.route('**/settings/transcript-optimization/verify',async route=>{
  await gate.hold()
  await route.fulfill({json:{...config,observed_context_length:32768,verification_token:'old-window'}})
 })
 await page.goto('/#system')
 const panel=page.locator('.optimization-settings'),field=panel.getByLabel('请求上下文窗口（tokens）',{exact:true})
 await expect(field).toHaveValue('32768')
 await panel.getByText('连接已验证 · 技术详情',{exact:true}).click()
 await expect(panel).toContainText('未记录，请重新验证')
 await panel.getByRole('button',{name:'重新验证',exact:true}).click()
 await gate.arrived
 await field.fill('65536')
 gate.release()
 await expect(field).toHaveValue('65536')
 await expect(panel.getByRole('button',{name:'保存并启用',exact:true})).toBeDisabled()
 expect(errors).toEqual([])
})
