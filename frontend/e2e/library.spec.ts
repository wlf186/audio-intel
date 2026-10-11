import {capabilitiesFor,expect,test,type Page} from './fixtures'
import {expandTtsSettings} from './tts-helpers'
import type {LibraryImport,LibraryMode,LibraryPreview,VoiceprintPerson} from '../src/lib/types'

test.afterEach(async({page})=>{await page.unrouteAll({behavior:'wait'})})
const now='2026-10-10T12:00:00Z'

async function workspace(page:Page){
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 const people:VoiceprintPerson[]=[...Array.from({length:24},(_,index)=>({id:`a${index}`,name:`Alice ${String(index).padStart(2,'0')}`,sort_initial:'A'})),{id:'s',name:'单明',sort_initial:'S'},{id:'z',name:'曾华',sort_initial:'Z'},{id:'symbol',name:'123',sort_initial:'#'}].map(value=>({...value,include_in_hotword_library:true,sample_count:1,created_at:now,updated_at:now,samples:[{id:'sample_'+value.id,person_id:value.id,name:'参考录音',state:'ready',language:'Chinese',duration:5,transcript:'这是用于声音克隆的参考录音。',words:[],tts_eligible:true,embedding_status:'ready',created_at:now,updated_at:now}]}))
 const capability=await capabilitiesFor(page)
 capability.events.sse=false
 capability.deployment.default_compute_device='cpu'
 for(const model of [...capability.asr.models,...capability.tts.model_capabilities])model.installed=true
 await page.route('**/api/v1/auth/session',route=>route.fulfill({json:{required:false,authenticated:true}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:capability}))
 await page.route('**/api/v1/system',route=>route.fulfill({json:{version:'test',status:'ok',offline:true,bind:'127.0.0.1:20915',services:['asr','tts'],workers:[],hardware:{},models:[],storage:{data:'/isolated'}}}))
 await page.route(/\/api\/v1\/jobs(?:\?.*)?$/,route=>route.fulfill({json:{items:[],count:0,total:0,limit:100,offset:0,has_more:false}}))
 await page.route('**/api/v1/voiceprints/people',route=>route.fulfill({json:{items:people,count:people.length}}))
 await page.route('**/api/v1/asr/hotword-lists',route=>route.fulfill({json:{items:[],count:0}}))
 return {people,errors}
}

for(const width of [1440,390]){
 test(`library navigation, scoped alphabet scrolling and ordered clone picker at ${width}px`,async({page})=>{
  const {people,errors}=await workspace(page)
  await page.setViewportSize({width,height:width===390?844:900})
  await page.goto('/#voiceprints')
  await expect(page).toHaveURL(/#library\/voiceprints$/)
  const navigation=page.getByRole('navigation',{name:'主导航'})
  await expect(navigation.getByRole('button')).toHaveCount(5)
  await expect(navigation.getByRole('button',{name:'资料库',exact:true})).toHaveAttribute('aria-current','page')
  const list=page.locator('.people-list')
  await expect(list.locator('.person-row').first()).toContainText('Alice 00')
  const selected=await page.locator('.samples-panel h2').textContent()
  const rail=page.getByRole('navigation',{name:'人员字母索引'})
  await expect(rail.getByRole('button',{name:'跳到 B 开头的人员'})).toBeDisabled()
  const jump=rail.getByRole('button',{name:'跳到 Z 开头的人员'})
  await jump.scrollIntoViewIfNeeded();await jump.click()
  expect(await list.evaluate(element=>element.scrollTop)).toBeGreaterThan(0)
  await expect(list.locator('#people-group-Z')).toBeInViewport()
  await expect(page.locator('.samples-panel h2')).toHaveText(selected!)
  if(width===390){const box=await jump.boundingBox();expect(box!.height).toBeGreaterThanOrEqual(44);expect(box!.width).toBeGreaterThanOrEqual(44)}
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  await page.screenshot({path:`/tmp/audio-intel-library-${width}.png`})
  await page.getByRole('tab',{name:'热词库',exact:true}).click()
  await expect(page).toHaveURL(/#library\/hotwords$/)
  await navigation.getByRole('button',{name:'语音合成',exact:true}).click()
  await expandTtsSettings(page)
  await page.getByRole('tab',{name:'声音克隆'}).click()
  await page.getByRole('tab',{name:'声纹库',exact:true}).click()
  const picker=page.locator('.clone-person-picker select')
  expect(await picker.locator('option').evaluateAll(options=>options.filter(option=>(option as HTMLOptionElement).value).map(option=>option.textContent))).toEqual(people.map(person=>person.name))
  await navigation.getByRole('button',{name:'资料库',exact:true}).click()
  await expect(page.getByRole('tab',{name:'热词库',exact:true})).toHaveAttribute('aria-selected','true')
  expect(errors).toEqual([])
 })
}

function plan(mode:LibraryMode):LibraryPreview{return {mode,preview_revision:mode+'-revision',source:{people:1,samples:1,hotword_lists:1},target:{people:27,samples:27,hotword_lists:0},added:{people:1,samples:1,hotword_lists:1},removed:{people:mode==='replace'?27:0,samples:mode==='replace'?27:0,hotword_lists:0},merged_people:0,merged_hotword_lists:0,skipped_samples:0,renamed_samples:0,warnings:[]}}

test('upload, replace confirmation, completed counts, backup and preserved TTS draft',async({page})=>{
 const {errors}=await workspace(page)
 let receipt:LibraryImport={id:'1'.repeat(32),name:'资料.zip',state:'ready',size_bytes:8,created_at:now,updated_at:now}
 let applies=0
 await page.route(/\/api\/v1\/library\/imports(?:\/.*)?$/,async route=>{
  const path=new URL(route.request().url()).pathname
  if(path.endsWith('/preview')){const mode=route.request().postDataJSON().mode as LibraryMode;return route.fulfill({json:plan(mode)})}
  if(path.endsWith('/apply')){applies++;const mode=route.request().postDataJSON().mode as LibraryMode;receipt={...receipt,state:'completed',mode,result:plan(mode),backup_url:'/api/v1/library/imports/'+receipt.id+'/backup'};return route.fulfill({status:202,json:receipt})}
  if(path.endsWith('/backup'))return route.fulfill({body:Buffer.from('backup'),contentType:'application/zip',headers:{'Content-Disposition':'attachment; filename="backup.zip"'}})
  if(path.endsWith('/imports')&&route.request().method()==='GET')return route.fulfill({json:[]})
  if(route.request().method()==='POST')return route.fulfill({status:202,json:receipt})
  return route.fulfill({json:receipt})
 })
 await page.goto('/#tts')
 await page.locator('.text-editor textarea').fill('迁移资料后保留这段正文。')
 await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:'资料库',exact:true}).click()
 await page.getByRole('button',{name:'导入完整包'}).click()
 const dialog=page.getByRole('dialog',{name:'导入资料库',exact:true})
 await dialog.getByLabel('完整资料包（ZIP）').setInputFiles({name:'资料.zip',mimeType:'application/zip',buffer:Buffer.from('mock zip')})
 await dialog.getByRole('button',{name:'上传并校验'}).click()
 await expect(dialog.getByRole('table')).toBeVisible()
 await dialog.getByRole('radio',{name:/替换现有资料/}).check()
 await expect(dialog.getByRole('table')).toContainText('移除')
 await dialog.getByRole('button',{name:'应用导入'}).click()
 expect(applies).toBe(0)
 const confirmation=page.getByRole('dialog',{name:'替换整个资料库？'})
 await expect(confirmation).toContainText('TTS 正文和 ASR 输入草稿保留')
 await confirmation.getByRole('button',{name:'备份并替换资料库'}).click()
 await expect(dialog).toContainText('新增 1 位人员、1 条样本、1 个热词表')
 expect(applies).toBe(1)
 const download=page.waitForEvent('download')
 await dialog.getByRole('button',{name:'下载替换前备份'}).click()
 expect((await download).suggestedFilename()).toBe('backup.zip')
 await dialog.getByRole('button',{name:'关闭',exact:true}).click()
 await page.getByRole('navigation',{name:'主导航'}).getByRole('button',{name:'语音合成',exact:true}).click()
 await expect(page.locator('.text-editor textarea')).toHaveValue('迁移资料后保留这段正文。')
 expect(errors).toEqual([])
})

test('a delayed merge preview cannot overwrite the selected replacement preview',async({page})=>{
 const {errors}=await workspace(page)
 const receipt:LibraryImport={id:'2'.repeat(32),name:'已校验.zip',state:'ready',size_bytes:1,created_at:now,updated_at:now}
 let mergeCalls=0
 await page.route(/\/api\/v1\/library\/imports(?:\/.*)?$/,async route=>{
  if(new URL(route.request().url()).pathname.endsWith('/preview')){
   const mode=route.request().postDataJSON().mode as LibraryMode
   if(mode==='merge'&&mergeCalls++===0)await new Promise(resolve=>setTimeout(resolve,700))
   return route.fulfill({json:plan(mode)})
  }
  return route.fulfill({json:[receipt]})
 })
 await page.goto('/#library')
 await page.getByRole('button',{name:'导入完整包'}).click()
 const dialog=page.getByRole('dialog',{name:'导入资料库'})
 await dialog.getByRole('radio',{name:/替换现有资料/}).check()
 await expect(dialog.getByRole('table')).toContainText('移除')
 await page.waitForTimeout(850)
 await expect(dialog.getByRole('table').locator('tbody tr').first()).toContainText('27')
 await expect(dialog.getByRole('button',{name:'应用导入'})).toBeEnabled()
 expect(errors).toEqual([])
})
