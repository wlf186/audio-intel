import {expect,test,type Page} from '@playwright/test'
import type {Job,Speaker,VoiceprintPerson} from '../src/lib/types'
import {showAsrResults} from './asr-helpers'

test.afterEach(async({page})=>{await page.unrouteAll({behavior:'wait'})})

function audio(){
 const buffer=Buffer.alloc(96044)
 buffer.write('RIFF');buffer.writeUInt32LE(buffer.length-8,4);buffer.write('WAVEfmt ',8)
 buffer.writeUInt32LE(16,16);buffer.writeUInt16LE(1,20);buffer.writeUInt16LE(1,22)
 buffer.writeUInt32LE(16000,24);buffer.writeUInt32LE(32000,28);buffer.writeUInt16LE(2,32);buffer.writeUInt16LE(16,34)
 buffer.write('data',36);buffer.writeUInt32LE(buffer.length-44,40)
 return buffer
}

async function workspace(page:Page,options:{libraryDelay?:Promise<void>;failPeople?:boolean;emptyPeople?:boolean;pendingPerson?:boolean}={}){
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error'&&!message.text().includes('the server responded with a status of 503'))errors.push(message.text())})
 const now='2026-10-09T12:00:00Z'
 const speakers:Speaker[]=[
  {id:'Speaker_0',name:'许榕俊',note:'研发',label:'许榕俊（研发）',label_source:'manual',voiceprint_match:{person_id:'old',name:'旧资料',note:'旧部门',score:.8}},
  {id:'Speaker_2',label:'旧名（旧备注）',label_source:'voiceprint',voiceprint_match:{person_id:'old',name:'旧名',note:'旧备注',score:.8}},
  {id:'Speaker_3',name:'许榕俊',note:'销售',label:'许榕俊（销售）',label_source:'manual'},
 ]
 const job:Job={id:'speaker-notes',kind:'asr',state:'succeeded',stage:'completed',progress:1,display_name:'说话人备注.wav',created_at:now,updated_at:now,request:{compute_device:'cpu'},result:{duration:3,language:'Chinese',waveform:Array(240).fill(.4),speakers,segments:speakers.map((speaker,index)=>({id:index,start:index,end:index+1,speaker:speaker.id,speaker_label:speaker.label,text:`第 ${index+1} 位发言`,words:[{text:'发言',start:index+.1,end:index+.8}]})),artifacts:[]}}
 const people:VoiceprintPerson[]=[
  {id:'research',name:'许榕俊',note:'研发',sample_count:0,samples:[],created_at:now,updated_at:now},
  {id:'sales',name:'许榕俊',note:'销售',sample_count:0,samples:[],created_at:now,updated_at:now},
  {id:'old',name:'旧资料',note:'旧部门',sample_count:0,samples:[],created_at:now,updated_at:now},
  {id:'no-note',name:'无备注人员',sample_count:0,samples:[],created_at:now,updated_at:now},
  {id:'long',name:'超长人员姓名'.repeat(20),note:'特别项目',sample_count:0,samples:[],created_at:now,updated_at:now},
 ]
 if(options.pendingPerson){
  people[2].sample_count=1
  people[2].samples=[{id:'pending',person_id:'old',name:'正在分析',state:'pending',words:[],tts_eligible:false,created_at:now,updated_at:now}]
 }
 const state={failRename:false,renameDelay:undefined as Promise<void>|undefined,failPeople:options.failPeople||false,emptyPeople:options.emptyPeople||false,peopleRequests:0,requests:[] as {speakerId:string;name:string;note:string|null}[],enrollment:undefined as {personId:string;job_id:string;segment_ids:number[]}|undefined}
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname
  if(path==='/api/v1/auth/session')return route.fulfill({json:{required:false,authenticated:true}})
  if(path==='/api/v1/capabilities'){
   const response=await route.fetch(),body=await response.json()
   body.events={...body.events,sse:false}
   return route.fulfill({response,json:body})
  }
  if(path==='/api/v1/jobs'){
   const {request:_,result:__,...summary}=job
   return route.fulfill({json:{items:[summary],count:1,total:1,has_more:false}})
  }
  if(path===`/api/v1/jobs/${job.id}`)return route.fulfill({json:job})
  if(path==='/api/v1/voiceprints/people'){
   if(route.request().method()!=='GET')throw new Error('Renaming must only read library people')
   state.peopleRequests+=1
   await options.libraryDelay
   if(state.failPeople)return route.fulfill({status:503,json:{detail:'声纹库暂不可用'}})
   const items=state.emptyPeople?[]:people
   return route.fulfill({json:{items,count:items.length}})
  }
  if(path==='/api/v1/asr/hotword-lists')return route.fulfill({json:{items:[],count:0}})
  if(path.endsWith('/source'))return route.fulfill({contentType:'audio/wav',body:audio()})
  if(path.includes('/speakers/')){
   const input=route.request().postDataJSON() as {name:string;note:string|null}
   const speakerId=path.split('/').at(-1)!
   state.requests.push({speakerId,...input})
   await state.renameDelay
   if(state.failRename)return route.fulfill({status:503,json:{detail:'资料暂未保存，请重试'}})
   const note=input.note?.trim()||null
   const updated=job.result!.speakers!.map(speaker=>speaker.id===speakerId?{...speaker,name:input.name,note,label:note?`${input.name}（${note}）`:input.name,label_source:'manual' as const}:speaker)
   const sameIdentity=updated.filter(speaker=>speaker.name===input.name&&(speaker.note||null)===note)
   const canonical=sameIdentity[0]
   const mergedIds=new Set(sameIdentity.map(speaker=>speaker.id))
   const nextSpeakers=updated.filter(speaker=>!mergedIds.has(speaker.id)||speaker.id===canonical.id)
   job.result={...job.result,speakers:nextSpeakers,segments:job.result!.segments!.map(segment=>{
    const id=mergedIds.has(segment.speaker)?canonical.id:segment.speaker
    return {...segment,speaker:id,speaker_label:nextSpeakers.find(speaker=>speaker.id===id)!.label}
   })}
   return route.fulfill({json:job.result})
  }
  if(path.endsWith('/samples/from-asr')){
   state.enrollment={personId:path.split('/')[5],...route.request().postDataJSON() as {job_id:string;segment_ids:number[]}}
   return route.fulfill({status:201,json:{items:[]}})
  }
  if(route.request().method()!=='GET')throw new Error(`Unexpected mutation: ${path}`)
  return route.continue()
 })
 await page.goto('/#asr')
 await expect(page).toHaveTitle(/Sandevistan/)
 await showAsrResults(page)
 await expect(page.locator('.result-head')).toContainText('说话人备注.wav')
 return {state,errors,job,people}
}

for(const width of [1440,390])test(`ASR notes merge identities and preserve filtering and selection at ${width}px`,async({page})=>{
 await page.setViewportSize({width,height:900})
 const {state,errors}=await workspace(page)
 await page.getByLabel('按说话人过滤').selectOption('Speaker_2')
 await page.getByLabel('选择片段 2').check()
 await page.getByRole('button',{name:'重命名说话人 旧名（旧备注）',exact:true}).click()
 const dialog=page.getByRole('dialog',{name:'重命名说话人'})
 await expect(dialog.getByLabel('新名称')).toHaveValue('旧名')
 await expect(dialog.getByLabel('备注（选填）')).toHaveValue('旧备注')
 await expect(dialog).toContainText('姓名和备注都相同的说话人会自动合并')
 const picker=dialog.getByLabel('从声纹库选择人员')
 await expect(picker).toHaveValue('')
 await expect(picker.locator('option',{hasText:'许榕俊（研发）'})).toHaveCount(1)
 await expect(picker.locator('option',{hasText:'许榕俊（销售）'})).toHaveCount(1)
 await picker.selectOption('sales')
 await expect(dialog.getByLabel('新名称')).toHaveValue('许榕俊')
 await expect(dialog.getByLabel('备注（选填）')).toHaveValue('销售')
 await picker.selectOption('research')
 await expect(dialog.getByLabel('新名称')).toHaveValue('许榕俊')
 await expect(dialog.getByLabel('备注（选填）')).toHaveValue('研发')
 await expect(dialog.getByLabel('备注（选填）')).toHaveAttribute('maxlength','20')
 await expect(dialog).toContainText('2 / 20 个字符')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 if(width===390)for(const control of await dialog.locator('input,select,button').all()){
  const box=await control.boundingBox()
  expect(box!.height).toBeGreaterThanOrEqual(44)
 }
 await page.screenshot({path:`/tmp/asr-speaker-note-dialog-${width}.png`})
 state.failRename=true
 const saving=Promise.withResolvers<void>()
 state.renameDelay=saving.promise
 try{
  await dialog.getByRole('button',{name:'保存名称'}).click()
  await expect(picker).toBeDisabled()
  await expect(dialog.getByLabel('新名称')).toBeDisabled()
  await expect(dialog.getByLabel('备注（选填）')).toBeDisabled()
  await expect(dialog.getByRole('button',{name:'保存名称'})).toBeDisabled()
 }finally{saving.resolve()}
 await expect(dialog.getByRole('alert')).toContainText('资料暂未保存')
 await expect(dialog.getByLabel('新名称')).toHaveValue('许榕俊')
 await expect(dialog.getByLabel('备注（选填）')).toHaveValue('研发')
 await expect(picker).toHaveValue('research')
 state.failRename=false
 await dialog.getByRole('button',{name:'保存名称'}).click()
 await expect(dialog).toHaveCount(0)
 expect(state.requests.at(-1)).toEqual({speakerId:'Speaker_2',name:'许榕俊',note:'研发'})
 const filter=page.getByLabel('按说话人过滤')
 await expect(filter).toHaveValue('Speaker_0')
 await expect(filter.locator('option')).toHaveCount(3)
 await expect(filter.locator('option',{hasText:'许榕俊（研发）'})).toHaveCount(1)
 await expect(filter.locator('option',{hasText:'许榕俊（销售）'})).toHaveCount(1)
 await expect(page.locator('.segments article')).toHaveCount(2)
 await expect(page.getByLabel('选择片段 2')).toBeChecked()
 await page.getByLabel('选择片段 1').check()
 await expect(page.getByLabel('段落选择操作')).toContainText('2 个 许榕俊（研发）')
 await page.getByRole('button',{name:'加入声纹库'}).click()
 const library=page.getByRole('dialog',{name:'加入声纹库'})
 await expect(library.getByLabel('指定人员')).toHaveValue('research')
 await library.getByRole('button',{name:'确认加入'}).click()
 await expect(library).toHaveCount(0)
 expect(state.enrollment).toEqual({personId:'research',job_id:'speaker-notes',segment_ids:[1,0]})
 await page.getByRole('button',{name:'重命名说话人 许榕俊（研发）',exact:true}).first().click()
 await expect(dialog.getByLabel('新名称')).toHaveValue('许榕俊')
 await expect(dialog.getByLabel('备注（选填）')).toHaveValue('研发')
 await dialog.getByLabel('备注（选填）').fill('')
 await dialog.getByRole('button',{name:'保存名称'}).click()
 await expect(dialog).toHaveCount(0)
 expect(state.requests.at(-1)).toEqual({speakerId:'Speaker_0',name:'许榕俊',note:null})
 await expect(page.locator('.segments .speaker-identity small')).toHaveCount(0)
 await expect(filter.locator('option',{hasText:'许榕俊（销售）'})).toHaveCount(1)
 await page.screenshot({path:`/tmp/asr-speaker-note-merged-${width}.png`})
 await page.reload();await showAsrResults(page)
 await expect(page.getByLabel('按说话人过滤').locator('option')).toHaveCount(3)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 expect(errors).toEqual([])
})

for(const width of [1440,390])test(`ASR library choices remain editable and retain full names at ${width}px`,async({page})=>{
 await page.setViewportSize({width,height:900})
 const {state,errors,people}=await workspace(page)
 await page.getByRole('button',{name:'重命名说话人 旧名（旧备注）',exact:true}).click()
 const dialog=page.getByRole('dialog',{name:'重命名说话人'})
 const picker=dialog.getByLabel('从声纹库选择人员')
 const name=dialog.getByLabel('新名称'),note=dialog.getByLabel('备注（选填）')
 await picker.selectOption('research')
 await name.fill('手动姓名')
 await expect(picker).toHaveValue('')
 await expect(note).toHaveValue('研发')
 await picker.selectOption('research')
 await note.fill('手动备注')
 await expect(picker).toHaveValue('')
 await expect(name).toHaveValue('许榕俊')
 await picker.selectOption('research')
 await picker.selectOption('')
 await expect(name).toHaveValue('许榕俊')
 await expect(note).toHaveValue('研发')
 expect(state.requests).toHaveLength(0)
 await picker.selectOption('no-note')
 await expect(name).toHaveValue('无备注人员')
 await expect(note).toHaveValue('')
 await dialog.getByRole('button',{name:'保存名称'}).click()
 await expect(dialog).toHaveCount(0)
 expect(state.requests.at(-1)).toEqual({speakerId:'Speaker_2',name:'无备注人员',note:null})
 await expect(page.getByLabel('按说话人过滤').locator('option')).toHaveCount(4)
 await page.getByRole('button',{name:'重命名说话人 无备注人员',exact:true}).click()
 await expect(picker).toHaveValue('')
 await picker.selectOption('long')
 const longName=people.find(person=>person.id==='long')!.name
 expect(longName.length).toBeGreaterThan(80)
 await expect(name).toHaveValue(longName)
 await expect(name).not.toHaveAttribute('maxlength',/.+/)
 await expect(note).toHaveValue('特别项目')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 await page.screenshot({path:`/tmp/asr-person-picker-long-${width}.png`})
 await dialog.getByRole('button',{name:'保存名称'}).click()
 await expect(dialog).toHaveCount(0)
 expect(state.requests.at(-1)).toEqual({speakerId:'Speaker_2',name:longName,note:'特别项目'})
 await page.reload();await showAsrResults(page)
 await page.getByRole('button',{name:`重命名说话人 ${longName}（特别项目）`,exact:true}).click()
 await expect(name).toHaveValue(longName)
 await expect(note).toHaveValue('特别项目')
 await expect(picker).toHaveValue('')
 expect(errors).toEqual([])
})

test('ASR library loading stays distinct from empty and manual saves remain available',async({page})=>{
 await page.setViewportSize({width:390,height:900})
 const library=Promise.withResolvers<void>()
 try{
  const {state,errors}=await workspace(page,{libraryDelay:library.promise})
  await page.getByRole('button',{name:'重命名说话人 旧名（旧备注）',exact:true}).click()
  const dialog=page.getByRole('dialog',{name:'重命名说话人'})
  const picker=dialog.getByLabel('从声纹库选择人员')
  await expect(dialog.getByRole('status')).toContainText('正在加载')
  await expect(picker).toBeDisabled()
  await expect(dialog).not.toContainText('声纹库暂无人员')
  await dialog.getByLabel('新名称').fill('手动姓名')
  await dialog.getByLabel('备注（选填）').fill('手动备注')
  await dialog.getByRole('button',{name:'保存名称'}).click()
  await expect(dialog).toHaveCount(0)
  expect(state.requests.at(-1)).toEqual({speakerId:'Speaker_2',name:'手动姓名',note:'手动备注'})
  await page.getByRole('button',{name:'重命名说话人 手动姓名（手动备注）',exact:true}).click()
  library.resolve()
  await expect(picker).toBeEnabled()
  await expect(picker).toHaveValue('')
  await expect(dialog.getByLabel('新名称')).toHaveValue('手动姓名')
  await expect(dialog.getByLabel('备注（选填）')).toHaveValue('手动备注')
  expect(state.peopleRequests).toBe(1)
  expect(errors).toEqual([])
 }finally{library.resolve()}
})

test('ASR library failure retries without overwriting the manual rename draft',async({page})=>{
 const {state,errors}=await workspace(page,{failPeople:true})
 await page.getByRole('button',{name:'重命名说话人 旧名（旧备注）',exact:true}).click()
 const dialog=page.getByRole('dialog',{name:'重命名说话人'})
 const picker=dialog.getByLabel('从声纹库选择人员')
 await expect(dialog.getByRole('alert')).toContainText('加载失败')
 await expect(picker).toBeDisabled()
 await expect(dialog).not.toContainText('声纹库暂无人员')
 await dialog.getByLabel('新名称').fill('草稿姓名')
 await dialog.getByLabel('备注（选填）').fill('草稿备注')
 await expect(dialog.getByRole('button',{name:'保存名称'})).toBeEnabled()
 state.failPeople=false
 await dialog.getByRole('button',{name:'重试',exact:true}).click()
 await expect(picker).toBeEnabled()
 await expect(dialog.getByRole('alert')).toHaveCount(0)
 await expect(picker).toHaveValue('')
 await expect(dialog.getByLabel('新名称')).toHaveValue('草稿姓名')
 await expect(dialog.getByLabel('备注（选填）')).toHaveValue('草稿备注')
 expect(state.peopleRequests).toBe(2)
 await dialog.getByRole('button',{name:'保存名称'}).click()
 await expect(dialog).toHaveCount(0)
 expect(state.requests.at(-1)).toEqual({speakerId:'Speaker_2',name:'草稿姓名',note:'草稿备注'})
 expect(errors).toEqual([])
})

test('ASR empty library allows manual renaming and note clearing',async({page})=>{
 const {state,errors}=await workspace(page,{emptyPeople:true})
 await page.getByRole('button',{name:'重命名说话人 旧名（旧备注）',exact:true}).click()
 const dialog=page.getByRole('dialog',{name:'重命名说话人'})
 const picker=dialog.getByLabel('从声纹库选择人员')
 await expect(picker).toBeDisabled()
 await expect(picker).toContainText('声纹库暂无人员，可手动填写')
 await expect(dialog.getByRole('alert')).toHaveCount(0)
 await expect(dialog.getByRole('status')).toHaveCount(0)
 await dialog.getByLabel('新名称').fill('手动人员')
 await dialog.getByLabel('备注（选填）').fill('')
 await dialog.getByRole('button',{name:'保存名称'}).click()
 await expect(dialog).toHaveCount(0)
 expect(state.requests.at(-1)).toEqual({speakerId:'Speaker_2',name:'手动人员',note:null})
 expect(errors).toEqual([])
})

test('ASR library refresh and removal preserve copied rename fields until another explicit choice',async({page})=>{
 await page.clock.install({time:new Date('2026-10-09T12:00:00Z')})
 await page.clock.pauseAt(new Date('2026-10-09T12:00:01Z'))
 const {state,errors,people}=await workspace(page,{pendingPerson:true})
 await page.getByRole('button',{name:'重命名说话人 旧名（旧备注）',exact:true}).click()
 const dialog=page.getByRole('dialog',{name:'重命名说话人'})
 const picker=dialog.getByLabel('从声纹库选择人员')
 const name=dialog.getByLabel('新名称'),note=dialog.getByLabel('备注（选填）')
 await picker.selectOption('research')
 const person=people.find(person=>person.id==='research')!
 person.name='库内新姓名';person.note='库内新备注'
 await page.clock.runFor(2100)
 await expect.poll(()=>state.peopleRequests).toBeGreaterThan(1)
 await expect(picker.locator('option',{hasText:'库内新姓名（库内新备注）'})).toHaveCount(1)
 await expect(picker).toHaveValue('')
 await expect(name).toHaveValue('许榕俊')
 await expect(note).toHaveValue('研发')
 await picker.selectOption('research')
 await expect(name).toHaveValue('库内新姓名')
 await expect(note).toHaveValue('库内新备注')
 people.splice(people.indexOf(person),1)
 await page.clock.runFor(2100)
 await expect.poll(()=>state.peopleRequests).toBeGreaterThan(2)
 await expect(picker.locator('option[value="research"]')).toHaveCount(0)
 await expect(picker).toHaveValue('')
 await expect(name).toHaveValue('库内新姓名')
 await expect(note).toHaveValue('库内新备注')
 await dialog.getByRole('button',{name:'保存名称'}).click()
 await expect(dialog).toHaveCount(0)
 expect(state.requests.at(-1)).toEqual({speakerId:'Speaker_2',name:'库内新姓名',note:'库内新备注'})
 expect(errors).toEqual([])
})
