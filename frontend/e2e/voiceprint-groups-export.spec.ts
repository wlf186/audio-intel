import {capabilitiesFor,expect,test,type Page} from './fixtures'
import type {VoiceprintGroup,VoiceprintPerson} from '../src/lib/types'

const now='2026-10-11T00:00:00Z'
const ungrouped='voiceprint_ungrouped'
async function setup(page:Page){
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 const state={groups:[{id:ungrouped,name:'未分组',builtin:true},{id:'group_work',name:'工作组',builtin:false},{id:'group_film',name:'影视组',builtin:false}].map(g=>({...g,person_count:0,created_at:now,updated_at:now})) as VoiceprintGroup[],people:['Alice','Liu Yifei','Marco'].map((name,i)=>({id:'person_'+i,name,group_id:ungrouped,include_in_hotword_library:true,sample_count:0,samples:[],created_at:now,updated_at:now})) as VoiceprintPerson[],exports:[] as {format:string;redact:boolean;replacements:{source:string;target:string}[]}[],groupError:false}
 const capabilities=await capabilitiesFor(page);capabilities.events.sse=false
 await page.route('**/api/v1/auth/session',route=>route.fulfill({json:{required:false,authenticated:true}}))
 await page.route('**/api/v1/capabilities',route=>route.fulfill({json:capabilities}))
 await page.route('**/api/v1/asr/hotword-lists',route=>route.fulfill({json:{items:[],count:0}}))
 await page.route('**/api/v1/voiceprints/groups',route=>{
  if(route.request().method()==='POST'){const group={id:'group_new',name:route.request().postDataJSON().name,builtin:false,person_count:0,created_at:now,updated_at:now};state.groups.push(group);return route.fulfill({status:201,json:group})}
  if(state.groupError)return route.fulfill({status:503,json:{detail:'Group fixture unavailable'}})
  return route.fulfill({json:{items:state.groups.map(g=>({...g,person_count:state.people.filter(p=>p.group_id===g.id).length}))}})
 })
 await page.route('**/api/v1/voiceprints/groups/*',route=>{
  const id=new URL(route.request().url()).pathname.split('/').pop()!
  if(route.request().method()==='DELETE'){state.groups=state.groups.filter(g=>g.id!==id);for(const person of state.people)if(person.group_id===id)person.group_id=ungrouped;return route.fulfill({status:204})}
  const group=state.groups.find(g=>g.id===id)!;group.name=route.request().postDataJSON().name;return route.fulfill({json:group})
 })
 await page.route('**/api/v1/voiceprints/people',route=>{
  if(route.request().method()==='POST'){const person={...state.people[0],...route.request().postDataJSON(),id:'person_new',group_id:ungrouped};state.people.push(person);return route.fulfill({status:201,json:person})}
  return route.fulfill({json:{items:state.people}})
 })
 await page.route('**/api/v1/voiceprints/people/batch-move',route=>{const body=route.request().postDataJSON();for(const person of state.people)if(body.person_ids.includes(person.id))person.group_id=body.group_id;return route.fulfill({status:204})})
 await page.route('**/api/v1/voiceprints/people/batch-delete',route=>{
  const body=route.request().postDataJSON();const items=body.person_ids.map((id:string)=>({person_id:id,deleted:id!=='person_2',status:id==='person_2'?409:204,detail:id==='person_2'?'Active sample import':undefined}));state.people=state.people.filter(p=>!items.some((item:{person_id:string;deleted:boolean})=>item.person_id===p.id&&item.deleted));return route.fulfill({json:{items}})
 })
 const summary={id:'export-fixture',kind:'asr',state:'succeeded',progress:1,stage:'completed',display_name:'Fixture.wav',created_at:now,updated_at:now,compute_device:'cpu'}
 const result={text:'广东移动联系了 Vince Carter。',language:'Chinese',duration:3,speakers:[{id:'Speaker_0',name:'Vince Carter',label:'Vince Carter（01234567）',note:'01234567'}],segments:[{id:0,start:0,end:3,speaker:'Speaker_0',speaker_label:'Vince Carter（01234567）',text:'广东移动联系了 Vince Carter。',words:[]}],artifacts:[]}
 await page.route(url=>url.pathname==='/api/v1/jobs',route=>route.fulfill({json:{items:[summary],count:1,total:1,limit:100,offset:0,has_more:false}}))
 await page.route('**/api/v1/jobs/export-fixture',route=>route.fulfill({json:{...summary,request:{use_voiceprint_library:true},result}}))
 await page.route('**/api/v1/jobs/export-fixture/source',route=>route.fulfill({contentType:'audio/wav',body:Buffer.from('UklGRiQAAABXQVZFZm10IBAAAAABAAEAgD4AAAB9AAACABAAZGF0YQAAAAA=','base64')}))
 await page.route('**/api/v1/jobs/export-fixture/transcript-export',route=>{const body=route.request().postDataJSON();state.exports.push(body);return route.fulfill({contentType:'application/octet-stream',headers:{'Content-Disposition':`attachment; filename="transcript.${body.format}"`},body:body.redact?'VC（01****67） gy':'Vince Carter 01234567 广东移动'})})
 return{state,errors}
}

for(const width of [1440,390])test(`group management and partial batch deletion at ${width}px`,async({page},info)=>{
 const {state,errors}=await setup(page)
 await page.setViewportSize({width,height:900})
 await page.goto('/#library/voiceprints')
 await expect(page.locator('.person-row')).toHaveCount(3)
 await page.getByRole('button',{name:'新增分组',exact:true}).click()
 await page.getByRole('dialog').getByLabel('分组名称').fill('项目组')
 await page.getByRole('dialog').getByRole('button',{name:'保存',exact:true}).click()
 await expect(page.getByRole('dialog')).toHaveCount(0)
 await page.getByRole('combobox',{name:'人员分组',exact:true}).selectOption('group_new')
 await page.getByRole('button',{name:'重命名分组',exact:true}).click()
 await page.getByRole('dialog').getByLabel('分组名称').fill('项目二组')
 await page.getByRole('dialog').getByRole('button',{name:'保存',exact:true}).click()
 await expect(page.getByRole('dialog')).toHaveCount(0)
 await expect.poll(()=>state.groups.find(g=>g.id==='group_new')?.name).toBe('项目二组')
 await page.getByRole('combobox',{name:'人员分组',exact:true}).selectOption('')
 await page.getByRole('checkbox',{name:'勾选 Liu Yifei',exact:true}).check()
 await page.getByRole('checkbox',{name:'勾选 Marco',exact:true}).check()
 await page.getByLabel('移动到分组',{exact:true}).selectOption('group_film')
 await page.getByRole('button',{name:'移动人员',exact:true}).click()
 await expect.poll(()=>state.people.filter(p=>p.group_id==='group_film').length).toBe(2)
 await page.getByRole('combobox',{name:'人员分组',exact:true}).selectOption('group_film')
 await expect(page.locator('.person-row')).toHaveCount(2)
 await page.screenshot({path:info.outputPath(`group-selection-${width}.png`),fullPage:true})
 await page.getByRole('checkbox',{name:'全选当前列表',exact:true}).check()
 await page.getByRole('button',{name:'批量删除人员',exact:true}).click()
 await expect(page.getByRole('dialog')).toContainText('2 位人员')
 await page.getByRole('dialog').getByRole('button',{name:'永久删除',exact:true}).click()
 await expect(page.getByRole('checkbox',{name:'勾选 Marco',exact:true})).toBeChecked()
 await expect(page.locator('.person-row')).toHaveCount(1)
 await expect(page.getByRole('alert')).toContainText('Active sample import')
 await page.getByRole('button',{name:'删除分组',exact:true}).click()
 await page.getByRole('dialog').getByRole('button',{name:'删除',exact:true}).click()
 await expect.poll(()=>state.people.find(p=>p.name==='Marco')?.group_id).toBe(ungrouped)
 await page.getByRole('combobox',{name:'人员分组',exact:true}).selectOption(ungrouped)
 await expect(page.getByRole('button',{name:'删除分组',exact:true})).toBeDisabled()
 await expect(page.getByRole('button',{name:'重命名分组',exact:true})).toBeDisabled()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 const control=await page.locator('.person-select-check').first().boundingBox();expect(control!.height).toBeGreaterThanOrEqual(44)
 await page.screenshot({path:info.outputPath(`groups-${width}.png`),fullPage:true})
 expect(errors).toEqual([])
})

test('ASR group preferences migrate, persist, retry and never expand a stale selection',async({page})=>{
 const {state,errors}=await setup(page)
 await page.addInitScript(()=>{if(!localStorage.getItem('group-migration-seeded')){localStorage.setItem('audio-intel:asr-preferences:v3',JSON.stringify({language:'English',useVoiceprints:true}));localStorage.setItem('group-migration-seeded','1')}})
 await page.goto('/#asr')
 await page.getByRole('button',{name:'选择分组',exact:true}).click()
 const dialog=page.getByRole('dialog')
 await expect(dialog.getByRole('checkbox',{name:'全部分组（包含以后新增的分组）',exact:true})).toBeChecked()
 await dialog.getByRole('checkbox',{name:'未分组',exact:false}).uncheck()
 await dialog.getByRole('button',{name:'完成',exact:true}).click()
 await page.reload()
 await page.getByRole('button',{name:'选择分组',exact:true}).click()
 await expect(dialog.getByRole('checkbox',{name:'工作组',exact:false})).toBeChecked()
 await expect(dialog.getByRole('checkbox',{name:'影视组',exact:false})).toBeChecked()
 const preferences=await page.evaluate(()=>JSON.parse(localStorage.getItem('audio-intel:asr-preferences:v4')!));expect(preferences.language).toBe('English');expect(preferences.voiceprintGroupIds).toEqual(['group_work','group_film'])
 state.groups=state.groups.filter(g=>g.id!=='group_work')
 await page.reload();await page.getByRole('button',{name:'选择分组',exact:true}).click()
 await expect(dialog.getByRole('status')).toContainText('已移除不存在的分组')
 state.groups=state.groups.filter(g=>g.id!=='group_film')
 await page.reload();await page.getByRole('button',{name:'选择分组',exact:true}).click()
 await expect(dialog.getByRole('alert')).toContainText('请至少选择一个声纹分组')
 await expect(dialog.getByRole('checkbox',{name:'全部分组（包含以后新增的分组）',exact:true})).not.toBeChecked()
 expect(errors).toEqual([])
})

for(const width of [1440,390])test(`redaction export preference, validation and raw toggle at ${width}px`,async({page},info)=>{
 const {state,errors}=await setup(page)
 await page.setViewportSize({width,height:900});await page.goto('/#asr')
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 const panel=page.locator('.transcript-export')
 await expect(panel.getByRole('checkbox',{name:'脱敏导出',exact:true})).toBeChecked()
 await panel.getByRole('button',{name:'关键词替换',exact:true}).click()
 const editor=panel.getByLabel('替换规则（每行 原词=替换词）',{exact:true})
 await editor.fill('广东移动=gy\ncontact=reach')
 let downloading=page.waitForEvent('download');await panel.getByRole('button',{name:'导出简洁 PDF',exact:true}).click();await downloading
 expect(state.exports.at(-1)).toEqual({format:'pdf',redact:true,replacements:[{source:'广东移动',target:'gy'},{source:'contact',target:'reach'}]})
 await panel.getByRole('checkbox',{name:'脱敏导出',exact:true}).uncheck()
 await editor.fill('bad syntax')
 await panel.getByText('明细与字幕',{exact:true}).click()
 await expect(editor).toBeHidden()
 downloading=page.waitForEvent('download');await panel.getByRole('button',{name:/^JSON/}).click();await downloading
 expect(state.exports.at(-1)).toEqual({format:'json',redact:false,replacements:[]})
 await page.reload();await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(panel.getByRole('checkbox',{name:'脱敏导出',exact:true})).not.toBeChecked()
 await panel.getByRole('button',{name:'关键词替换',exact:true}).click();await expect(editor).toHaveValue('bad syntax')
 await panel.getByRole('checkbox',{name:'脱敏导出',exact:true}).check()
 await panel.getByRole('button',{name:'导出简洁 PDF',exact:true}).click()
 await expect(panel.getByRole('alert')).toContainText('第 1 行')
 expect(state.exports).toHaveLength(2)
 await editor.fill('广东移动=gy')
 await page.screenshot({path:info.outputPath(`redaction-${width}.png`),fullPage:true})
 await panel.getByRole('button',{name:'恢复默认导出设置',exact:true}).click()
 await expect(editor).toHaveValue('');await expect(panel.getByRole('checkbox',{name:'脱敏导出',exact:true})).toBeChecked()
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 expect(errors).toEqual([])
})
