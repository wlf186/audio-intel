import {expect,test} from './fixtures'
import {uxFixture} from './ux-fixtures'
import {assertReachable,openTranscriptExport} from './ux-helpers'

for(const width of [1440,390]){
 test(`exports stay reachable and rules validate before submitting at ${width}px`,async({page},info)=>{
  await page.setViewportSize({width,height:844})
  const {state,errors}=await uxFixture(page)
  await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click();await expect(page.locator('.video-section').first()).toBeVisible()
  await assertReachable(page,width)
  await openTranscriptExport(page)
  if(width===1440)await page.getByRole('button',{name:'关键词替换',exact:true}).click()
  const editor=page.getByLabel('替换规则（每行 原词=替换词）',{exact:true})
  await editor.fill('bad syntax');await expect(editor).toHaveAttribute('aria-invalid','true')
  const exportButton=page.getByRole('button',{name:'导出简洁 PDF',exact:true})
  await expect(exportButton).toBeDisabled();expect(state.exports).toHaveLength(0)
  await editor.fill('产品=项目');await expect(exportButton).toBeEnabled()
  if(width===1440){await page.keyboard.press('Escape');await expect(editor).toBeHidden();await page.getByRole('button',{name:'关键词替换',exact:true}).click();await page.locator('h1').click();await expect(editor).toBeHidden()}
  await assertReachable(page,width)
  await page.screenshot({path:info.outputPath('export.png')})
  expect(errors).toEqual([])
 })
 test(`reading survives scene refresh, view switches and reload at ${width}px @timing`,async({page,responseGate})=>{
  await page.setViewportSize({width,height:844})
  const {state,errors}=await uxFixture(page)
  await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click();await expect(page.locator('.video-section')).toHaveCount(20)
  const root=page.locator('.video-transcript')
  await root.evaluate(element=>element.scrollTop=element.scrollHeight);await expect(page.locator('.video-section')).toHaveCount(40)
  await page.getByRole('button',{name:'编辑分段',exact:true}).click()
  const target=page.locator('.video-section').nth(25);await target.scrollIntoViewIfNeeded()
  await target.getByRole('button',{name:'更换截图',exact:true}).click()
  const before=await target.boundingBox()
  const gate=responseGate();state.sceneHold=gate.hold
  try{
   await page.getByRole('dialog').getByRole('button',{name:'保存',exact:true}).click();await gate.arrived
   await expect(root.getByRole('status')).toContainText('正在读取图文分段');await expect(page.locator('.video-section')).toHaveCount(40)
   gate.release();await expect(root).toHaveAttribute('aria-busy','false')
   await expect.poll(async()=>Math.abs((await target.boundingBox())!.y-before!.y)).toBeLessThanOrEqual(2)
   await page.getByRole('button',{name:'完成编辑',exact:true}).click();await target.scrollIntoViewIfNeeded()
   const position=await root.evaluate(element=>element.scrollTop)
   await page.getByRole('button',{name:'逐句明细',exact:true}).click();await page.getByRole('button',{name:'图文阅读',exact:true}).click()
   await expect(root).toHaveJSProperty('scrollTop',position)
   await page.reload();await expect(root).toHaveJSProperty('scrollTop',position)
   expect(state.patches).toHaveLength(1);expect(errors).toEqual([])
  }finally{gate.release()}
 })
 test(`person detail restores filtered list and grouping at ${width}px`,async({page},info)=>{
  await page.setViewportSize({width,height:844})
  const {state,errors}=await uxFixture(page)
  await page.goto('/#library/voiceprints');await expect(page.locator('.person-row')).toHaveCount(45)
  await page.getByLabel('人员分组',{exact:true}).selectOption('film')
  await page.getByRole('button',{name:'新建人员',exact:true}).click();const dialog=page.getByRole('dialog')
  await expect(dialog.getByLabel('所属分组')).toHaveValue('film');await dialog.getByLabel('名字（必填）').fill('新同事');await dialog.getByRole('button',{name:'保存人员',exact:true}).click();await expect(dialog).toHaveCount(0)
  expect(state.created[0]).toMatchObject({group_id:'film',name:'新同事'})
  if(width===390){await expect(page.locator('.samples-panel h2')).toBeInViewport();await page.getByRole('button',{name:'返回人员列表',exact:true}).click()}
  await expect(page.getByLabel('人员分组',{exact:true})).toHaveValue('film')
  await page.getByLabel('搜索姓名或备注').fill('曾');await expect(page.locator('.person-row')).toHaveCount(1);await page.locator('.person-row').click()
  await expect(page).toHaveURL(/#library\/voiceprints\/person-44$/)
  if(width===390){await expect(page.locator('.samples-panel h2')).toBeInViewport();await page.goBack();await expect(page.getByLabel('搜索姓名或备注')).toHaveValue('曾');await expect(page.locator('.person-row')).toHaveCount(1)}
  await assertReachable(page,width);await page.screenshot({path:info.outputPath('people.png')});expect(errors).toEqual([])
 })
 test(`time validation and paragraph enrollment preserve context at ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:844})
  const {state,errors}=await uxFixture(page)
  await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click();await expect(page.locator('.video-section').first()).toBeVisible()
  await page.getByRole('button',{name:'编辑分段',exact:true}).click();await page.getByRole('button',{name:'更换截图',exact:true}).first().click()
  const dialog=page.getByRole('dialog');await dialog.getByRole('spinbutton').fill('-1');await expect(dialog.getByRole('button',{name:'保存',exact:true})).toBeDisabled()
  await dialog.getByLabel('时间（时:分:秒）').fill('00:00:00.500');await expect(dialog.getByRole('spinbutton')).toHaveValue('0.5');await expect(dialog.getByRole('button',{name:'保存',exact:true})).toBeEnabled();await dialog.getByRole('button',{name:'取消',exact:true}).click()
  await page.locator('.video-section').nth(4).getByRole('button',{name:'加入声纹库',exact:true}).click()
  await expect(page.getByRole('dialog').getByRole('checkbox',{name:'选择片段 5',exact:true})).toBeChecked()
  expect(state.imports).toHaveLength(0);expect(errors).toEqual([])
 })
}
