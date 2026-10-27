import {capabilitiesFor,expect,test,type Page} from './fixtures'
import type {Job,VoiceprintGroup,VoiceprintPerson} from '../src/lib/types'
import {expandAsrAdvanced,showAsrResults} from './asr-helpers'
import {assertReachable,downloadConcise,openTranscriptExport} from './ux-helpers'

const now='2026-10-21T10:00:00Z'
const ungrouped='voiceprint_ungrouped'
async function fixture(page:Page){
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error'&&!/status of (422|503)/.test(message.text()))errors.push(message.text())})
 const wave=Buffer.alloc(44+96000);wave.write('RIFF');wave.writeUInt32LE(wave.length-8,4);wave.write('WAVEfmt ',8);wave.writeUInt32LE(16,16);wave.writeUInt16LE(1,20);wave.writeUInt16LE(1,22);wave.writeUInt32LE(16000,24);wave.writeUInt32LE(32000,28);wave.writeUInt16LE(2,32);wave.writeUInt16LE(16,34);wave.write('data',36);wave.writeUInt32LE(96000,40)
 const audio:Job={id:'export-audio',kind:'asr',state:'succeeded',stage:'completed',progress:1,display_name:'Recording_10.m4a',created_at:now,updated_at:now,request:{},result:{duration:3,text:'测试发言',language:'Chinese',speakers:[{id:'s0',label:'新人员'}],segments:[{id:0,start:0,end:3,speaker:'s0',speaker_label:'新人员',text:'测试发言',words:[]}],artifacts:[]}}
 const scene={id:'scene0',start:0,end:3,frame_time:0,image:'frame.jpg',paragraphs:[{speaker:'s0',speaker_label:'新人员',start:0,end:3,text:'测试发言',segment_ids:[0]}]}
 const video:Job={...audio,id:'export-video',display_name:'WorkBuddy与AI-Coding融合方案-用户旅程版.mp4',request:{purpose:'video_transcription'},result:{...audio.result,video:{contract_version:1,revision:1,duration:3,time_origin:0,detector:'presentation-v1',scenes:[scene],original_scenes:[scene]}}}
 const state={jobs:[audio,video],groups:[{id:ungrouped,name:'未分组',builtin:true},{id:'work',name:'工作组',builtin:false},{id:'film',name:'影视组',builtin:false}].map(group=>({...group,person_count:0,created_at:now,updated_at:now})) as VoiceprintGroup[],people:[{id:'existing',name:'已有人员',group_id:'film',include_in_hotword_library:true,sample_count:0,samples:[],created_at:now,updated_at:now}] as VoiceprintPerson[],created:[] as {group_id:string;name:string}[],exports:[] as {format:string;redact:boolean}[],submissions:[] as {groups:string|null;video:boolean}[],header:'normal',failGroups:false,failImport:false,deleteOnCreate:false,imports:0}
 const capabilities=await capabilitiesFor(page);capabilities.events.sse=false;capabilities.deployment.default_compute_device='cpu'
 for(const model of capabilities.asr.models){model.installed=true;for(const device of model.compute_devices)if(device.id==='cpu')device.available=true}
 await page.route('**/api/v1/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname
  if(path==='/api/v1/auth/session')return route.fulfill({json:{required:false,authenticated:true}})
  if(path==='/api/v1/capabilities')return route.fulfill({json:capabilities})
  if(path==='/api/v1/asr/hotword-lists')return route.fulfill({json:{items:[],count:0}})
  if(path==='/api/v1/jobs'&&request.method()==='GET')return route.fulfill({json:{items:state.jobs.map(({request:_,result:__,...job})=>job),count:state.jobs.length,total:state.jobs.length,has_more:false}})
  if(path==='/api/v1/voiceprints/groups')return state.failGroups?route.fulfill({status:503,json:{detail:'Groups unavailable'}}):route.fulfill({json:{items:state.groups}})
  if(path==='/api/v1/voiceprints/people'){
   if(request.method()==='POST'){
    const body=request.postDataJSON()
    if(state.deleteOnCreate){state.groups=state.groups.filter(group=>group.id!==body.group_id);return route.fulfill({status:422,json:{detail:'Unknown voiceprint group ID'}})}
    state.created.push(body);const person={...body,id:'created',samples:[],sample_count:0,created_at:now,updated_at:now};state.people.push(person);return route.fulfill({status:201,json:person})
   }
   return route.fulfill({json:{items:state.people}})
  }
  if(path.endsWith('/samples/from-asr')){state.imports++;return state.failImport?route.fulfill({status:503,json:{detail:'Import unavailable'}}):route.fulfill({status:201,json:{items:[]}})}
  if(path.endsWith('/transcript-export')){
   const body=request.postDataJSON();state.exports.push(body)
   const job=state.jobs.find(job=>path.includes('/'+job.id+'/'))!
   const name=job.display_name.replace(/\.(m4a|mp4)$/i,'')+'.'+body.format
   const disposition=state.header==='malformed'?"attachment; filename*=UTF-8''%XX":state.header==='plain'?'attachment; filename="server-name.'+body.format+'"':"attachment; filename*=UTF-8''"+encodeURIComponent(name)
   return route.fulfill({headers:state.header==='missing'?{}:{'Content-Disposition':disposition},contentType:'application/octet-stream',body:'export fixture'})
  }
  if(path==='/api/v1/asr/jobs'&&request.method()==='POST'){
   const groups=/name="voiceprint_group_ids"\r\n\r\n([^\r]*)/.exec(request.postDataBuffer()!.toString())?.[1]||null
   state.submissions.push({groups,video:false});return route.fulfill({status:202,json:{...audio,id:'submitted',state:'queued',stage:'queued',progress:0,result:undefined}})
  }
  if(path.startsWith('/api/v1/asr/video-uploads'))return route.fulfill({status:request.method()==='POST'?201:200,json:{id:'upload',name:'selection.mp4',size_bytes:64,offset:request.method()==='PATCH'?64:0,expires_at:Date.now()/1000+3600,chunk_bytes:1024,job_id:null,chunks:[]}})
  if(path==='/api/v1/asr/video-jobs'&&request.method()==='POST'){const body=request.postDataJSON();state.submissions.push({groups:body.voiceprint_group_ids?JSON.stringify(body.voiceprint_group_ids):null,video:true});return route.fulfill({status:202,json:{...video,id:'submitted-video',state:'queued',stage:'queued',progress:0,result:undefined}})}
  if(path.endsWith('/video-scenes'))return route.fulfill({json:{revision:1,scenes:[scene]}})
  if(path.includes('/video-frames/'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"/>'})
  if(path.endsWith('/source')||path.endsWith('/playback'))return route.fulfill({contentType:'audio/wav',body:wave})
  if(path.endsWith('/waveform'))return route.fulfill({json:{start:0,end:3,duration:3,peaks:[.2,.4,.1],track:'original'}})
  if(path==='/api/v1/jobs/storage')return route.fulfill({json:{items:[]}})
  const job=state.jobs.find(job=>path==='/api/v1/jobs/'+job.id)
  if(job)return route.fulfill({json:job})
  return route.continue()
 })
 return {state,errors,wave}
}

async function chooseTask(page:Page,id:'audio'|'video',width:number){
 if(width===390)await page.locator('.asr-task-picker select').selectOption('export-'+id)
 else await page.locator('.asr-recent-jobs .job-mini').filter({hasText:id==='audio'?'Recording_10.m4a':'WorkBuddy与AI-Coding融合方案-用户旅程版.mp4'}).click()
 await expect(page.locator('.result-head')).toContainText(id==='audio'?'Recording_10.m4a':'用户旅程版.mp4')
}

async function exportFormat(page:Page,format:string,_width:number){
 if(format==='pdf'||format==='docx')return downloadConcise(page,format)
 await openTranscriptExport(page)
 await page.locator('.transcript-export-modal .export-format').selectOption(format);await page.locator('.transcript-export-modal .modal-actions .primary').click()
}

for(const width of [1440,390]){
 for(const source of ['audio','video'] as const)for(const redact of [true,false])test(`transcript filenames ${source} redact=${redact} at ${width}px`,async({page})=>{
  const {state,errors}=await fixture(page);await page.setViewportSize({width,height:960});await page.goto('/#asr');await showAsrResults(page);await chooseTask(page,source,width)
  await openTranscriptExport(page);await page.locator('.transcript-export').getByRole('checkbox',{name:'脱敏导出',exact:true}).setChecked(redact)
  for(const format of ['pdf','docx','json','txt','srt','vtt']){
   const downloading=page.waitForEvent('download');await exportFormat(page,format,width)
   expect((await downloading).suggestedFilename()).toBe((source==='audio'?'Recording_10':'WorkBuddy与AI-Coding融合方案-用户旅程版')+'.'+format)
   expect(state.exports.at(-1)).toMatchObject({format,redact})
  }
  await assertReachable(page,width);expect(errors).toEqual([])
 })

 test(`download fallback tracks task changes and retains header precedence at ${width}px`,async({page})=>{
  const {state,errors}=await fixture(page);await page.setViewportSize({width,height:960});await page.goto('/#asr');await showAsrResults(page)
  for(const source of ['audio','video','audio'] as const){
   if(await page.locator('.transcript-export-modal').isVisible())await page.locator('.transcript-export-modal .modal-close').click()
   await chooseTask(page,source,width);state.header='missing'
   const downloading=page.waitForEvent('download');await exportFormat(page,'txt',width)
   expect((await downloading).suggestedFilename()).toBe((source==='audio'?'Recording_10':'WorkBuddy与AI-Coding融合方案-用户旅程版')+'.txt')
  }
  for(const header of ['malformed','plain']){
   state.header=header;const downloading=page.waitForEvent('download');await exportFormat(page,'txt',width)
   expect((await downloading).suggestedFilename()).toBe(header==='plain'?'server-name.txt':'Recording_10.txt')
  }
  expect(errors).toEqual([])
 })

 test(`scope modes distinguish current select-all from future groups at ${width}px`,async({page},info)=>{
  const {state,errors}=await fixture(page);await page.setViewportSize({width,height:960});await page.goto('/#asr');await expandAsrAdvanced(page)
  await page.getByRole('button',{name:'选择分组',exact:true}).click();const dialog=page.getByRole('dialog')
  const allMode=dialog.getByRole('radio',{name:'全部分组（包含以后新增的分组）',exact:true})
  const specific=dialog.getByRole('radio',{name:'指定分组',exact:true})
  await expect(allMode).toBeChecked();await expect(dialog.getByRole('checkbox')).toHaveCount(0)
  await specific.check();const master=dialog.getByRole('checkbox',{name:'全选当前分组',exact:true})
  await expect(dialog.getByRole('checkbox',{checked:true})).toHaveCount(0);await expect(dialog.getByRole('alert')).toContainText('请至少选择一个')
  await master.focus();await page.keyboard.press('Space');await expect(dialog.getByRole('checkbox',{checked:true})).toHaveCount(4)
  await dialog.getByRole('checkbox',{name:'工作组',exact:false}).uncheck();await expect(master).toBeChecked({indeterminate:true})
  await master.click();await expect(master).toBeChecked();await expect(specific).toBeChecked()
  await master.click();await expect(dialog.getByRole('checkbox',{checked:true})).toHaveCount(0)
  await dialog.getByRole('checkbox',{name:'工作组',exact:false}).check();await dialog.getByRole('button',{name:'完成',exact:true}).click()
  state.groups.push({...state.groups[1],id:'later',name:'以后新增'})
  await page.reload();await expandAsrAdvanced(page);await page.getByRole('button',{name:'选择分组',exact:true}).click()
  await expect(dialog.getByRole('checkbox',{name:'工作组',exact:false})).toBeChecked();await expect(dialog.getByRole('checkbox',{name:'以后新增',exact:false})).not.toBeChecked()
  await allMode.check();await specific.check();await expect(dialog.getByRole('checkbox',{checked:true})).toHaveCount(0)
  await dialog.getByRole('checkbox',{name:'工作组',exact:false}).check()
  await assertReachable(page,width);await dialog.screenshot({path:info.outputPath('specific-groups.png')})
  await dialog.getByRole('button',{name:'完成',exact:true}).click()
  await page.locator('input[type=file]').setInputFiles({name:'selection.wav',mimeType:'audio/wav',buffer:Buffer.alloc(64)})
  await page.getByRole('button',{name:'开始转写',exact:true}).click();await expect.poll(()=>state.submissions.length).toBe(1)
  expect(JSON.parse(state.submissions[0].groups!)).toEqual(['work'])
  expect(errors).toEqual([])
 })

 for(const source of ['audio','video'] as const)test(`new ${source} enrollment chooses a group and retries without duplicating a person at ${width}px`,async({page},info)=>{
  const {state,errors}=await fixture(page);await page.setViewportSize({width,height:960});await page.goto('/#asr');await showAsrResults(page);await chooseTask(page,source,width)
  const open=async()=>{
   if(source==='audio'){await page.getByLabel('选择片段 1',{exact:true}).check();await page.getByRole('button',{name:'加入声纹库',exact:true}).click()}
   else {await page.locator('.video-section').first().getByRole('button',{name:'加入声纹库',exact:true}).click();await page.getByRole('dialog').getByLabel('选择片段 1',{exact:true}).check();await page.getByRole('button',{name:'下一步：选择人员',exact:true}).click()}
  }
  await open();const dialog=page.getByRole('dialog');const group=dialog.getByRole('combobox',{name:'人员分组',exact:true})
  await expect(group).toHaveValue(ungrouped);await group.selectOption('work')
  await dialog.getByRole('button',{name:'关闭加入声纹库',exact:true}).click();await open();await expect(group).toHaveValue(ungrouped)
  await group.selectOption('work');await dialog.getByRole('combobox',{name:'指定人员',exact:true}).selectOption('existing');await expect(group).toHaveCount(0)
  await dialog.getByRole('combobox',{name:'指定人员',exact:true}).selectOption('');await expect(group).toHaveValue('work')
  await assertReachable(page,width);await dialog.screenshot({path:info.outputPath(source+'-person-group.png')})
  state.failImport=true;await dialog.getByRole('button',{name:'确认加入',exact:true}).click();await expect(dialog.getByRole('alert')).toContainText('Import unavailable')
  expect(state.created).toHaveLength(1);expect(state.created[0].group_id).toBe('work');await expect(group).toHaveCount(0)
  state.failImport=false;await dialog.getByRole('button',{name:'确认加入',exact:true}).click();await expect(dialog).toHaveCount(0)
  expect(state.created).toHaveLength(1);expect(state.imports).toBe(2);expect(state.people.find(person=>person.id==='existing')!.group_id).toBe('film');expect(errors).toEqual([])
 })
}

test('new-person group loading failures and deleted selections require explicit recovery',async({page})=>{
 const {state,errors}=await fixture(page);state.failGroups=true;await page.goto('/#asr');await showAsrResults(page)
 await page.getByLabel('选择片段 1',{exact:true}).check();await page.getByRole('button',{name:'加入声纹库',exact:true}).click();const dialog=page.getByRole('dialog')
 await expect(dialog.getByRole('combobox',{name:'人员分组',exact:true})).toBeDisabled();await expect(dialog.getByRole('button',{name:'确认加入',exact:true})).toBeDisabled()
 state.failGroups=false;await dialog.getByRole('button',{name:'重试',exact:true}).click();await expect(dialog.getByRole('combobox',{name:'人员分组',exact:true})).toHaveValue(ungrouped)
 await dialog.getByRole('combobox',{name:'人员分组',exact:true}).selectOption('work');state.deleteOnCreate=true
 await dialog.getByRole('button',{name:'确认加入',exact:true}).click();await expect(dialog.getByRole('alert').filter({hasText:'分组已不可用，请重新选择'})).toBeVisible();expect(state.created).toHaveLength(0)
 await expect(dialog.getByRole('button',{name:'确认加入',exact:true})).toBeDisabled();state.deleteOnCreate=false
 await dialog.getByRole('combobox',{name:'人员分组',exact:true}).selectOption(ungrouped);await dialog.getByRole('button',{name:'确认加入',exact:true}).click();await expect(dialog).toHaveCount(0)
 expect(state.created[0].group_id).toBe(ungrouped);expect(errors).toEqual([])
})

for(const source of ['audio','video'])for(const mode of ['all','selected'])test(`${source} submission preserves ${mode} group scope`,async({page})=>{
 const {state,errors}=await fixture(page);await page.goto('/#asr')
 if(source==='video')await page.getByRole('tab',{name:'新建视频转写',exact:true}).click()
 await expandAsrAdvanced(page);await page.getByRole('button',{name:'选择分组',exact:true}).click()
 const dialog=page.getByRole('dialog')
 if(mode==='selected'){await dialog.getByRole('radio',{name:'指定分组',exact:true}).check();await dialog.getByRole('checkbox',{name:'影视组',exact:false}).check()}
 await dialog.getByRole('button',{name:'完成',exact:true}).click()
 await page.locator('input[type=file]').setInputFiles({name:'selection.'+(source==='video'?'mp4':'wav'),mimeType:source==='video'?'video/mp4':'audio/wav',buffer:Buffer.alloc(64)})
 await page.getByRole('button',{name:'开始转写',exact:true}).click();await expect.poll(()=>state.submissions.length).toBe(1)
 expect(state.submissions[0].video).toBe(source==='video')
 expect(state.submissions[0].groups===null?null:JSON.parse(state.submissions[0].groups!)).toEqual(mode==='all'?null:['film'])
 expect(errors).toEqual([])
})

for(const width of [1440,390])test(`new enrollment waits for groups before showing real choices at ${width}px @timing`,async({page,responseGate})=>{
 const {errors}=await fixture(page);await page.setViewportSize({width,height:960})
 const gate=responseGate();await page.route('**/api/v1/voiceprints/groups',async route=>{await gate.hold();return route.fallback()})
 await page.goto('/#asr');await showAsrResults(page);await gate.arrived
 await page.getByLabel('选择片段 1',{exact:true}).check();await page.getByRole('button',{name:'加入声纹库',exact:true}).click()
 const dialog=page.getByRole('dialog'),group=dialog.getByRole('combobox',{name:'人员分组',exact:true})
 await expect(group).toBeDisabled();await expect(group.locator('option')).toHaveCount(1);await expect(group).toContainText('正在加载分组')
 await expect(dialog.getByRole('button',{name:'确认加入',exact:true})).toBeDisabled()
 gate.release();await expect(group).toBeEnabled();await expect(group).toHaveValue(ungrouped);await expect(group.locator('option')).toHaveCount(3)
 await assertReachable(page,width);expect(errors).toEqual([])
})
