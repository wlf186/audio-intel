import {test,expect} from './fixtures'
import {uxFixture} from './ux-fixtures'

for(const width of [1440,390])test(`Chat Completions manual model and private key ${width}`,async({page},info)=>{
 await page.setViewportSize({width,height:900})
 const {errors}=await uxFixture(page)
 let config={base_url:'http://localhost:11434/v1',model:'',use_proxy:false,enabled:false,context_length:16384,has_api_key:false,extra_body:{} as Record<string,unknown>,verified_at:''}
 const writes:Record<string,unknown>[]=[]
 const root='**/api/v1/settings/transcript-optimization'
 await page.route(root,async route=>{
  if(route.request().method()==='PUT'){
   const body=route.request().postDataJSON();writes.push(body)
   config={...config,...body,has_api_key:body.api_key?true:config.has_api_key,verified_at:body.verification_token?'today':''}
   delete (config as Record<string,unknown>).api_key
  }
  await route.fulfill({json:config})
 })
 await page.route(root+'/discover',route=>route.fulfill({json:{models:[]}}))
 await page.route(root+'/verify',route=>{
  const body=route.request().postDataJSON()
  expect(body.api_key).toBe('synthetic-model-secret')
  expect(body.extra_body).toEqual({reasoning_effort:'none'})
  const {api_key,...safe}=body
  expect(api_key).toBeTruthy()
  return route.fulfill({json:{...config,...safe,has_api_key:true,verified_at:'today',verification_token:'verified'}})
 })
 await page.goto('/#system')
 const panel=page.locator('.optimization-settings')
 await panel.getByRole('button',{name:'获取模型',exact:true}).click()
 await expect(panel).toContainText('没有可用模型')
 await panel.getByLabel('优化模型',{exact:true}).fill('qwen3.5:9b')
 await panel.getByLabel('模型服务 API Key',{exact:true}).fill('synthetic-model-secret')
 await panel.getByText('高级参数',{exact:true}).click()
 const extra=panel.getByLabel('额外请求参数（JSON 对象）',{exact:true})
 await extra.fill('invalid')
 await expect(panel.getByRole('button',{name:'重新验证',exact:true})).toBeDisabled()
 await extra.fill('{"reasoning_effort":"none"}')
 await panel.getByRole('button',{name:'重新验证',exact:true}).click()
 await expect(panel).toContainText('模型验证通过')
 await panel.getByLabel('启用表达优化').check()
 await panel.getByRole('button',{name:'保存并启用',exact:true}).click()
 await expect(panel).toContainText('配置已保存')
 await expect(panel.getByLabel('模型服务 API Key',{exact:true})).toHaveValue('')
 expect(writes[0].model).toBe('qwen3.5:9b')
 expect(await page.evaluate(()=>JSON.stringify({...localStorage,...sessionStorage}))).not.toContain('synthetic-model-secret')
 await page.reload()
 await expect(panel.getByLabel('优化模型',{exact:true})).toHaveValue('qwen3.5:9b')
 await expect(panel.getByLabel('模型服务 API Key',{exact:true})).toHaveAttribute('placeholder','已保存；留空保留')
 await panel.getByText('高级参数',{exact:true}).click()
 // Expand the scroll container and hide shell chrome only while capturing this documentation panel.
 await panel.screenshot({
  path:info.outputPath('chat-completions-settings-saved.png'),
  animations:'disabled',
  style:'.app-shell{height:auto!important;overflow:visible!important}.app-shell>main{overflow:visible!important}.app-shell>header,.app-shell>footer,.skip-link{visibility:hidden!important}'
 })
 await panel.getByText('高级参数',{exact:true}).click()
 await panel.getByRole('button',{name:'清除密钥',exact:true}).click()
 await expect(panel.getByRole('button',{name:'保存并启用',exact:true})).toBeDisabled()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 for(const field of await panel.locator('input:visible,button:visible').all()){
  if(await field.getAttribute('type')!=='checkbox')expect((await field.boundingBox())!.height).toBeGreaterThanOrEqual(44)
 }
 await panel.getByText('高级参数',{exact:true}).click()
 await expect(extra).toHaveValue(JSON.stringify(config.extra_body,null,2))
 await panel.scrollIntoViewIfNeeded()
 await page.screenshot({path:info.outputPath('chat-completions-settings.png'),fullPage:true})
 expect(errors).toEqual([])
})
