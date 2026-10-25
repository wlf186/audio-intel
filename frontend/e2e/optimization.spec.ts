import {test,expect,capabilitiesFor} from './fixtures'
import {uxFixture} from './ux-fixtures'
for(const width of [1440,390])test(`optimization reading and comparison ${width}`,async({page},testInfo)=>{
 await page.setViewportSize({width,height:900})
 const {state,errors}=await uxFixture(page)
 const run={id:'opt1',job_id:state.job.id,state:'succeeded',published:true,model:'qwen3.5:9b',total_chars:100,completed_chars:100,completed_units:1,total_units:1,activity_chars:0,progress:1,revision:1,error:null,created_at:state.job.created_at,updated_at:state.job.updated_at}
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 const paragraphs=[{id:'p1',ordinal:0,speaker:'s0',speaker_label:'Alice',start:0,end:2,original_text:'那个我们我们周五先测试，预算15万元，不是50万元。',text:'我们周五先测试，预算15万元，不是50万元。',segment_ids:[0]}]
 await page.route('**/transcript-optimization/result?*',route=>route.fulfill({json:{run_id:'opt1',complete:true,model:run.model,paragraphs,scenes:[]}}))
 await page.goto('/#asr')
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.getByRole('button',{name:'优化稿',exact:true}).click()
 await expect(page.locator('.optimization-reader')).toContainText('我们周五先测试，预算15万元，不是50万元。')
 await page.locator('.optimization-actions').getByRole('button',{name:'对照',exact:false}).click()
 await expect(page.getByRole('dialog')).toContainText('那个我们我们')
 await expect(page.getByRole('dialog')).toContainText('不是50万元')
 await page.screenshot({path:testInfo.outputPath('optimization-comparison.png'),fullPage:true})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).click()
 await page.getByRole('button',{name:'原文',exact:true}).click()
 await expect(page.locator('.video-section').first()).toBeVisible()
 expect(errors).toEqual([])
})
for(const width of [1440,390])test(`optimization settings ${width}`,async({page},testInfo)=>{
 await page.setViewportSize({width,height:900})
 const {errors}=await uxFixture(page)
 let config={base_url:'',model:'',use_proxy:false,enabled:false,verified_at:'',context_length:32768,model_context_length:262144}
 await page.route('**/api/v1/settings/transcript-optimization',async route=>{
  if(route.request().method()==='PUT')config={...config,...route.request().postDataJSON()}
  await route.fulfill({json:config})
 })
 await page.route('**/settings/transcript-optimization/discover',route=>route.fulfill({json:{models:[{name:'qwen3.5:9b'}]}}))
 await page.route('**/settings/transcript-optimization/verify',route=>route.fulfill({json:{...config,...route.request().postDataJSON(),verified_at:'2026-10-23',verification_token:'verified'}}))
 await page.goto('/#system')
 const panel=page.locator('.optimization-settings')
 await panel.getByLabel('服务地址').fill('http://iollama:11434')
 await panel.getByRole('button',{name:'获取模型',exact:true}).click()
 await panel.getByLabel('优化模型').selectOption('qwen3.5:9b')
 await expect(panel).toContainText('模型验证通过')
 await panel.getByLabel('启用表达优化').check()
 await panel.getByRole('button',{name:'保存配置',exact:true}).click()
 await expect(panel).toContainText('配置已保存')
 expect(config.enabled).toBeTruthy()
 await panel.scrollIntoViewIfNeeded()
 if(width===390)for(const control of await panel.locator('button:visible,input[type="url"]:visible,select:visible,.optimization-check:visible').all()){
  expect((await control.boundingBox())!.height).toBeGreaterThanOrEqual(44)
 }
 await page.screenshot({path:testInfo.outputPath('optimization-settings.png'),fullPage:true})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 expect(errors).toEqual([])
})
test('optimization create pause resume and delete',async({page})=>{
 const {state,errors}=await uxFixture(page)
 let candidate:Record<string,unknown>|null=null
 const paths:string[]=[]
 await page.route('**/transcript-optimization**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname
  if(request.method()==='POST'){
   paths.push(path)
   if(path.endsWith('/pause'))candidate={...candidate,state:'paused'}
   else if(path.endsWith('/resume'))candidate={...candidate,state:'running'}
   else{expect(request.headers()['idempotency-key']).toBeTruthy();candidate={id:'candidate',job_id:state.job.id,state:'running',published:false,model:'qwen3.5:9b',total_chars:100,completed_chars:0,completed_units:0,total_units:2,activity_chars:0,progress:0,revision:1,error:null}}
  }
  if(request.method()==='DELETE'){candidate=null;await route.fulfill({status:204});return}
  await route.fulfill({json:{enabled:true,published:null,candidate}})
 })
 await page.goto('/#asr')
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.getByRole('button',{name:'优化表达',exact:true}).click()
 await expect(page.locator('.optimization-progress')).toContainText('正在优化')
 await page.locator('.optimization-progress').getByRole('button',{name:'暂停',exact:true}).click()
 await expect(page.locator('.optimization-progress')).toContainText('已暂停')
 await page.getByRole('button',{name:'继续优化',exact:true}).click()
 await expect(page.locator('.optimization-progress')).toContainText('正在优化')
 await page.getByRole('button',{name:'优化选项',exact:true}).click()
 await page.getByRole('button',{name:'删除优化数据',exact:true}).click()
 await page.getByRole('dialog').getByRole('button',{name:'删除优化数据',exact:true}).click()
 await expect(page.locator('.optimization-progress')).toHaveCount(0)
 expect(paths).toHaveLength(3)
 expect(errors).toEqual([])
})

for(const width of [1440,390])for(const action of ['pause','delete'] as const)test(`optimization ${action} survives an older status response at ${width}px`,{tag:'@timing'},async({page,responseGate},testInfo)=>{
 await page.setViewportSize({width,height:900})
 await page.clock.install()
 await page.clock.pauseAt(new Date())
 const {state,errors}=await uxFixture(page)
 const running={id:'candidate',job_id:state.job.id,state:'running',published:false,model:'test-model',total_chars:100,completed_chars:10,completed_units:1,total_units:10,activity_chars:0,progress:.1,revision:1,error:null,created_at:state.job.created_at,updated_at:state.job.updated_at}
 let candidate:Record<string,unknown>|null=running
 let reads=0,oldDelivered=false
 const old=responseGate(),fresh=responseGate()
 await page.route('**/transcript-optimization**',async route=>{
  const request=route.request()
  if(request.method()==='POST'){candidate={...running,state:'paused',revision:2};await route.fulfill({json:{enabled:true,published:null,candidate}});return}
  if(request.method()==='DELETE'){candidate=null;await route.fulfill({status:204});return}
  reads++
  if(reads===2){await old.hold();try{await route.fulfill({json:{enabled:true,published:null,candidate:running}})}finally{oldDelivered=true};return}
  if(reads>2)await fresh.hold()
  await route.fulfill({json:{enabled:true,published:null,candidate}})
 })
 await page.goto('/#asr')
 await expect(page).toHaveTitle(/Sandevistan-Audio/)
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(page.locator('.optimization-progress')).toContainText('正在优化')
 await page.clock.runFor(2000)
 await old.arrived
 if(action==='pause')await page.locator('.optimization-progress').getByRole('button',{name:'暂停',exact:true}).click()
 else{
  await page.getByRole('button',{name:'优化选项',exact:true}).click()
  await page.getByRole('button',{name:'删除优化数据',exact:true}).click()
  await page.getByRole('dialog').getByRole('button',{name:'删除优化数据',exact:true}).click()
 }
 await fresh.arrived
 if(action==='pause')await expect(page.locator('.optimization-progress')).toContainText('已暂停')
 else await expect(page.locator('.optimization-progress')).toHaveCount(0)
 old.release()
 await expect.poll(()=>oldDelivered).toBe(true)
 if(action==='pause')await expect(page.getByRole('button',{name:'继续优化',exact:true})).toBeVisible()
 else await expect(page.getByRole('button',{name:'优化表达',exact:true})).toBeVisible()
 const response=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/transcript-optimization')&&r.request().method()==='GET')
 fresh.release()
 expect(await (await response).finished()).toBeNull()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 await page.screenshot({path:testInfo.outputPath('optimization-status-after-action.png')})
 expect(errors).toEqual([])
})

for(const width of [1440,390])test(`optimization result loading error and retry at ${width}px`,{tag:'@timing'},async({page,responseGate})=>{
 await page.setViewportSize({width,height:900})
 const {state,errors}=await uxFixture(page)
 const run={id:'published',job_id:state.job.id,state:'succeeded',published:true,model:'test-model',total_chars:10,completed_chars:10,completed_units:1,total_units:1,activity_chars:0,progress:1,revision:1,error:null,created_at:state.job.created_at,updated_at:state.job.updated_at}
 const gate=responseGate();let reads=0
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 await page.route('**/transcript-optimization/result?*',async route=>{
  if(++reads===1){await gate.hold();await route.fulfill({status:503,json:{detail:'Temporary optimization read failure'}});return}
  await route.fulfill({json:{run_id:run.id,complete:true,model:run.model,paragraphs:[{id:'p1',ordinal:0,speaker:'s0',speaker_label:'Alice',start:0,end:2,original_text:'预算15万元，不是50万元。',text:'预算15万元，不是50万元。',segment_ids:[0]}],scenes:[]}})
 })
 await page.goto('/#asr')
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.getByRole('button',{name:'优化稿',exact:true}).click()
 await gate.arrived
 const reader=page.locator('.optimization-reader')
 await expect(reader).toHaveAttribute('aria-busy','true')
 await expect(reader.locator('.optimization-paragraph')).toHaveCount(0)
 gate.release()
 await expect(reader.getByRole('alert')).toContainText('Temporary optimization read failure')
 await reader.getByRole('button',{name:'重试',exact:true}).click()
 await expect(reader).toContainText('预算15万元，不是50万元。')
 await expect(reader).toHaveAttribute('aria-busy','false')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 expect(reads).toBe(2)
 expect(errors).toEqual([])
})

for(const width of [1440,390])test(`optimization settings retry and obsolete verification at ${width}px`,{tag:'@timing'},async({page,responseGate})=>{
 await page.setViewportSize({width,height:900})
 const {errors}=await uxFixture(page)
 const first=responseGate(),verify=responseGate();let reads=0,verificationDone=false
 const config={base_url:'http://first.invalid:11434',model:'',use_proxy:false,enabled:false}
 await page.route('**/api/v1/settings/transcript-optimization',async route=>{
  if(++reads===1){await first.hold();await route.fulfill({status:503,json:{detail:'Settings temporarily unavailable'}});return}
  await route.fulfill({json:config})
 })
 await page.route('**/transcript-optimization/discover',route=>route.fulfill({json:{models:[{name:'test-model'}]}}))
 await page.route('**/transcript-optimization/verify',async route=>{
  await verify.hold()
  try{await route.fulfill({json:{...config,model:'test-model',verified_at:'2026-10-25',verification_token:'old-profile',context_length:16384,model_context_length:32768}})}finally{verificationDone=true}
 })
 await page.goto('/#system')
 const panel=page.locator('.optimization-settings')
 await first.arrived
 await expect(panel.getByRole('status')).toBeVisible()
 await expect(panel.getByLabel('服务地址')).toHaveCount(0)
 first.release()
 await expect(panel.getByRole('alert')).toContainText('Settings temporarily unavailable')
 await panel.getByRole('button',{name:'重试',exact:true}).click()
 await expect(panel.getByLabel('服务地址')).toHaveValue(config.base_url)
 await panel.getByRole('button',{name:'获取模型',exact:true}).click()
 await panel.getByLabel('优化模型').selectOption('test-model')
 await verify.arrived
 await panel.getByLabel('服务地址').fill('http://second.invalid:11434')
 verify.release()
 await expect.poll(()=>verificationDone).toBe(true)
 await expect(panel.getByLabel('服务地址')).toHaveValue('http://second.invalid:11434')
 await expect(panel.getByLabel('启用表达优化')).toBeDisabled()
 await expect(panel.getByLabel('优化模型')).toHaveValue('')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 expect(errors).toEqual([])
})

for(const width of [1440,390])test(`optimization task switch rejects an older draft at ${width}px`,{tag:'@timing'},async({page,responseGate},testInfo)=>{
 await page.setViewportSize({width,height:900})
 const {state,errors}=await uxFixture(page)
 const other=structuredClone(state.job)
 other.id='optimization-other';other.display_name='第二任务.wav';delete other.result!.video
 const summary=(job:typeof other)=>{const {request:_,result:__,...value}=job;return value}
 await page.route(url=>url.pathname==='/api/v1/jobs',route=>route.fulfill({json:{items:[summary(state.job),summary(other)],count:2,total:2,has_more:false}}))
 await page.route('**/api/v1/jobs/optimization-other',route=>route.fulfill({json:other}))
 const old=responseGate();let oldDone=false
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>{
  const second=route.request().url().includes(other.id)
  const run={id:second?'second':'first',job_id:second?other.id:state.job.id,state:'succeeded',published:true,model:'test-model',total_chars:10,completed_chars:10,completed_units:1,total_units:1,activity_chars:0,progress:1,revision:1,error:null,created_at:state.job.created_at,updated_at:state.job.updated_at}
  return route.fulfill({json:{enabled:true,published:run,candidate:null}})
 })
 await page.route('**/transcript-optimization/result?*',async route=>{
  const second=route.request().url().includes(other.id)
  if(!second)await old.hold()
  try{await route.fulfill({json:{run_id:second?'second':'first',complete:true,model:'test-model',paragraphs:[{id:'p1',ordinal:0,speaker:'s0',speaker_label:'Alice',start:0,end:2,original_text:'原文',text:second?'第二任务专属优化正文':'第一任务过时正文',segment_ids:[0]}],scenes:[]}})}finally{if(!second)oldDone=true}
 })
 await page.goto('/#asr')
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.getByRole('button',{name:'优化稿',exact:true}).click()
 await old.arrived
 if(width===390)await page.locator('.asr-task-picker select').selectOption(other.id)
 else await page.locator('.asr-recent-jobs .job-mini').filter({hasText:other.display_name}).click()
 await expect(page.getByRole('button',{name:'优化稿',exact:true})).toBeEnabled()
 await page.getByRole('button',{name:'优化稿',exact:true}).click()
 await expect(page.locator('.optimization-reader')).toContainText('第二任务专属优化正文')
 old.release()
 await expect.poll(()=>oldDone).toBe(true)
 await expect(page.locator('.optimization-reader')).not.toContainText('第一任务过时正文')
 await expect(page.locator('.optimization-reader')).toContainText('第二任务专属优化正文')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 await page.screenshot({path:testInfo.outputPath('optimization-task-switch.png')})
 expect(errors).toEqual([])
})

for(const width of [1440,390])for(const declared of [false,true])test(`unsupported optimization does not request resources at ${width}px declared=${declared}`,{tag:'@timing'},async({page})=>{
 await page.setViewportSize({width,height:900})
 await page.clock.install()
 await page.clock.pauseAt(new Date())
 const {errors}=await uxFixture(page)
 const caps=await capabilitiesFor(page)
 caps.events.sse=false
 if(!declared)delete caps.asr.transcript_optimization
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:caps}))
 let reads=0
 page.on('request',request=>{if(request.url().includes('/transcript-optimization'))reads++})
 await page.goto('/#asr')
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(page.locator('.video-section').first()).toBeVisible()
 await expect(page.getByRole('button',{name:'优化稿',exact:true})).toBeDisabled()
 await page.clock.runFor(4000)
 expect(reads).toBe(0)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 expect(errors).toEqual([])
})
