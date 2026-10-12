import {assertReachable} from './ux-helpers'
import {uxFixture} from './ux-fixtures'
import {expect,test} from './fixtures'

const viewports=[{width:1024,height:768},{width:1280,height:720},{width:1440,height:900},{width:1920,height:1080},{width:390,height:844}]
const labels={
 'zh-CN':{manage:'分组管理',selection:'人员批量操作',clear:'清除选择',all:'全选当前列表',search:'搜索姓名或备注',addSample:'添加样本',groups:'人员分组'},
 'en-US':{manage:'Manage groups',selection:'Selected people actions',clear:'Clear selection',all:'Select the current list',search:'Search names or notes',addSample:'Add sample',groups:'Person group'},
}

for(const locale of ['zh-CN','en-US'] as const)for(const viewport of viewports)test(`voiceprint management gives people room at ${viewport.width}px ${locale}`,async({page},info)=>{
 await page.setViewportSize(viewport)
 await page.addInitScript(locale=>localStorage.setItem('audio-intel:ui-locale:v1',locale),locale)
 const {state,errors}=await uxFixture(page)
 await page.goto('/#library/voiceprints')
 await expect(page.locator('.person-row')).toHaveCount(45)
 await expect(page.locator('.people-list-heading>span')).toContainText('45')
 const text=labels[locale]
 const toolbar=page.getByRole('group',{name:text.selection,exact:true})
 await expect(toolbar).toHaveCount(0)
 if(viewport.width>=1024){
  const layout=(await page.locator('.voiceprint-layout').boundingBox())!,people=(await page.locator('.people-panel').boundingBox())!
  expect(people.width/layout.width).toBeCloseTo(.5,2)
  if(viewport.width===1440)expect((await page.locator('.people-index').boundingBox())!.height).toBeGreaterThanOrEqual(350)
 }
 const firstCheckbox=page.locator('.person-select-check input').first()
 await firstCheckbox.check()
 await expect(toolbar).toBeVisible()
 await expect(toolbar.getByRole('status')).toContainText('1')
 await expect(page.getByRole('checkbox',{name:text.all,exact:true})).toHaveJSProperty('indeterminate',true)
 await assertReachable(page,viewport.width)
 await toolbar.getByRole('button',{name:text.clear,exact:true}).click()
 await expect(toolbar).toHaveCount(0)
 await expect(firstCheckbox).not.toBeChecked()
 await expect(page.getByRole('checkbox',{name:text.all,exact:true})).toBeFocused()
 const manage=page.getByRole('button',{name:text.manage,exact:true})
 await manage.click()
 await expect(page.getByRole('dialog',{name:text.manage,exact:true})).toBeVisible()
 await assertReachable(page,viewport.width)
 await page.keyboard.press('Escape')
 await expect(page.getByRole('dialog')).toHaveCount(0)
 await expect(manage).toBeFocused()
 await page.getByRole('combobox',{name:text.groups,exact:true}).selectOption('empty')
 await expect(page.locator('.people-list-heading>span')).toContainText('0')
 await expect(page.getByRole('checkbox',{name:text.all,exact:true})).not.toBeChecked()
 await expect(page.getByRole('checkbox',{name:text.all,exact:true})).toBeDisabled()
 await page.getByRole('combobox',{name:text.groups,exact:true}).selectOption('')
 await page.getByLabel(text.search,{exact:true}).fill('Alice 00')
 await expect(page.locator('.person-row')).toHaveCount(1)
 await expect(page.locator('.people-list-heading>span')).toContainText('1')
 await page.getByLabel(text.search,{exact:true}).fill('')
 await expect(page.locator('.person-row')).toHaveCount(state.people.length)
 await assertReachable(page,viewport.width)
 await page.screenshot({path:info.outputPath(`people-${locale}-${viewport.width}.png`),fullPage:false})
 expect(errors).toEqual([])
})

for(const width of [1440,390])test(`group loading and retry keep management explicit at ${width}px @timing`,async({page,responseGate})=>{
 await page.setViewportSize({width,height:900})
 const {state,errors}=await uxFixture(page)
 const gate=responseGate()
 let attempts=0
 await page.route('**/api/v1/voiceprints/groups',async route=>{
  if(++attempts===1){await gate.hold();return route.fulfill({status:503,json:{detail:'Group loading regression'}})}
  return route.fulfill({json:{items:state.groups}})
 })
 await page.goto('/#library/voiceprints');await gate.arrived
 const groups=page.locator('.voiceprint-group-controls')
 await expect(groups.getByRole('status')).toContainText('正在加载分组')
 await expect(page.getByRole('button',{name:'分组管理',exact:true})).toBeDisabled()
 await expect(page.getByRole('combobox',{name:'人员分组',exact:true})).toBeDisabled()
 gate.release()
 await expect(groups.getByRole('alert')).toContainText('无法加载分组')
 await groups.getByRole('button',{name:'重试',exact:true}).click()
 await expect(page.getByRole('button',{name:'分组管理',exact:true})).toBeEnabled()
 expect(attempts).toBe(2)
 await assertReachable(page,width)
 expect(errors).toEqual([])
})

function referenceWave(){
 const data=Buffer.alloc(44+16000*2*8)
 data.write('RIFF');data.writeUInt32LE(data.length-8,4);data.write('WAVEfmt ',8);data.writeUInt32LE(16,16);data.writeUInt16LE(1,20);data.writeUInt16LE(1,22);data.writeUInt32LE(16000,24);data.writeUInt32LE(32000,28);data.writeUInt16LE(2,32);data.writeUInt16LE(16,34);data.write('data',36);data.writeUInt32LE(data.length-44,40)
 return data
}

test('sample pane adapts to its width while preserving drafts and player nodes',async({page},info)=>{
 const {state,errors}=await uxFixture(page)
 state.people[0].name='这是很长的人员姓名，用于验证左右两栏都可以完整显示人物身份'
 state.people[0].note='这是二十个字以内的备注内容'
 state.people[0].samples[0].name='这是一条很长的参考录音样本名称，用于验证操作按钮与状态不会被挤出右侧详情区'
 state.people[0].samples[0].audio_url='/api/v1/voiceprints/samples/layout-reference/audio'
 const recording=referenceWave()
 await page.route('**/api/v1/voiceprints/samples/layout-reference/audio',route=>{
  const range=/^bytes=(\d+)-(\d*)$/.exec(route.request().headers().range||'')
  const start=range?Number(range[1]):0,end=range?.[2]?Math.min(Number(range[2]),recording.length-1):recording.length-1
  const body=recording.subarray(start,end+1)
  return route.fulfill({status:range?206:200,contentType:'audio/wav',headers:{'Accept-Ranges':'bytes','Content-Length':String(body.length),...(range?{'Content-Range':`bytes ${start}-${end}/${recording.length}`}:{})},body})
 })
 await page.setViewportSize({width:1280,height:720})
 await page.goto('/#library/voiceprints')
 await expect(page.locator('.person-row')).toHaveCount(45)
 await page.locator('.person-row').first().click()
 await page.getByRole('button',{name:'添加样本',exact:true}).click()
 const file=page.locator('.sample-add-form input[type=file]')
 await file.setInputFiles({name:'voiceprint-layout-draft.wav',mimeType:'audio/wav',buffer:referenceWave()})
 const input=await file.elementHandle(),audio=page.locator('.sample-list audio').first(),player=await audio.elementHandle()
 await audio.evaluate(element=>(element as HTMLAudioElement).load())
 await expect.poll(()=>audio.evaluate(element=>(element as HTMLAudioElement).readyState)).toBeGreaterThanOrEqual(2)
 await audio.evaluate(element=>{(element as HTMLAudioElement).currentTime=1})
 await expect.poll(()=>audio.evaluate(element=>(element as HTMLAudioElement).currentTime)).toBeCloseTo(1,1)
 for(const viewport of [{width:1024,height:768},{width:1920,height:1080},{width:390,height:844},{width:1440,height:900}]){
  await page.setViewportSize(viewport)
  await expect(file).toHaveCount(1)
  expect(await file.evaluate(element=>(element as HTMLInputElement).files?.[0]?.name)).toBe('voiceprint-layout-draft.wav')
  expect(await input!.evaluate(element=>element.isConnected)).toBe(true)
  expect(await player!.evaluate(element=>element.isConnected)).toBe(true)
  expect(await audio.evaluate(element=>(element as HTMLAudioElement).currentTime)).toBeCloseTo(1,1)
  const options=page.locator('.voiceprint-sample-options'),boxes=await options.locator('select,.primary').evaluateAll(elements=>elements.map(element=>{const box=element.getBoundingClientRect();return {top:Math.round(box.top),left:box.left,right:box.right}}))
  expect(new Set(boxes.map(box=>box.top)).size).toBe(viewport.width===390?4:viewport.width===1024?2:1)
  const pane=(await page.locator('.samples-panel').boundingBox())!
  expect(boxes.every(box=>box.left>=pane.x&&box.right<=pane.x+pane.width)).toBe(true)
  await expect(page.locator('.samples-panel h2')).toHaveText(state.people[0].name)
  await expect(page.locator('.sample-head>b')).toHaveText(state.people[0].samples[0].name)
  await assertReachable(page,viewport.width)
  await page.screenshot({path:info.outputPath(`samples-${viewport.width}.png`),fullPage:false})
 }
 expect(errors).toEqual([])
})
