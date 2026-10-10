import {expect,test,type Page} from '@playwright/test'

async function fixture(page:Page,locale='zh-CN'){
 await page.addInitScript(locale=>localStorage.setItem('audio-intel:ui-locale:v1',locale),locale)
 const segments=Array.from({length:110},(_,id)=>({id,start:id,end:id+.8,speaker:`s${id%2}`,speaker_label:id%2?'Other':'Presenter',text:`Sentence ${id}`,words:[]}))
 const scenes=Array.from({length:55},(_,i)=>({id:`scene_${i}`,start:i*2,end:i*2+2,frame_time:i*2,image:'frame.jpg',paragraphs:[{speaker:'s0',speaker_label:'Presenter',start:i*2,end:i*2+.8,text:`Paragraph ${i}`,segment_ids:[i*2,i*2]}]}))
 const result={text:'Video',duration:120,language:'Chinese',timestamp_precision:'segment',playback_url:'/api/v1/jobs/enrollment-video/playback',speakers:[{id:'s0',label:'Presenter'},{id:'s1',label:'Other'}],segments,artifacts:[],video:{contract_version:1,revision:1,duration:120,scenes,original_scenes:scenes}}
 const job={id:'enrollment-video',kind:'asr',purpose:'video_transcription',state:'succeeded',stage:'completed',progress:1,display_name:'Enrollment.mp4',created_at:'2026-10-10T00:00:00Z',request:{purpose:'video_transcription'},result}
 const state={failPeople:false,failImport:0,imports:[] as Array<{job_id:string;segment_ids:number[]}>,created:0,people:[{id:'existing',name:'Presenter',note:'',sample_count:1,samples:[{id:'sample0',source_job_id:job.id,source_segment_id:0,state:'ready'}]}]}
 await page.route('**/api/v1/auth/session',route=>route.fulfill({json:{required:true,authenticated:true}}))
 await page.route('**/api/v1/capabilities',async route=>{const r=await route.fetch();const body=await r.json();body.events={...body.events,sse:false};await route.fulfill({response:r,json:body})})
 await page.route(url=>url.pathname==='/api/v1/jobs',route=>route.fulfill({json:{items:[job,{...job,id:'other-job',display_name:'Other video'}],total:2,count:2,has_more:false}}))
 await page.route(/\/api\/v1\/jobs\/(enrollment-video|other-job)$/,route=>route.fulfill({json:{...job,id:new URL(route.request().url()).pathname.split('/').pop()}}))
 await page.route('**/video-scenes',route=>route.fulfill({json:{revision:1,scenes}}))
 await page.route('**/video-frames/*',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="white"/></svg>'}))
 const wav=Buffer.alloc(44+16000*120*2);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(16000,24);wav.writeUInt32LE(32000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(wav.length-44,40)
 await page.route('**/playback',route=>{
  const range=route.request().headers()['range']?.match(/bytes=(\d+)-(\d*)/)
  if(!range)return route.fulfill({contentType:'audio/wav',headers:{'Accept-Ranges':'bytes'},body:wav})
  const start=Number(range[1]),end=range[2]?Math.min(Number(range[2]),wav.length-1):wav.length-1
  return route.fulfill({status:206,contentType:'audio/wav',headers:{'Accept-Ranges':'bytes','Content-Range':`bytes ${start}-${end}/${wav.length}`},body:wav.subarray(start,end+1)})
 })
 await page.route('**/api/v1/voiceprints/people',route=>{
  if(route.request().method()==='POST'){state.created++;const p=route.request().postDataJSON();const person={id:'created',...p,samples:[],sample_count:0};state.people.push(person);return route.fulfill({status:201,json:person})}
  return state.failPeople?route.fulfill({status:503,json:{detail:'Library unavailable'}}):route.fulfill({json:{items:state.people}})
 })
 await page.route('**/api/v1/voiceprints/people/*/samples/from-asr',route=>{
  const body=route.request().postDataJSON();state.imports.push(body)
  if(state.failImport)return route.fulfill({status:state.failImport,json:{detail:'Import unavailable'}})
  const person=state.people.find(item=>item.id===new URL(route.request().url()).pathname.split('/')[5])!
  person.samples.push(...body.segment_ids.map((id:number)=>({id:`sample${id}`,source_job_id:job.id,source_segment_id:id,state:'ready'})))
  return route.fulfill({status:201,json:{items:[]}})
 })
 return {state,async open(){await page.goto('/#asr');await page.getByRole('tab',{name:locale==='zh-CN'?'任务与结果':'Tasks & results',exact:true}).click();await expect(page.locator('.video-section')).toHaveCount(20)}}
}
for(const width of [1440,390])for(const locale of ['zh-CN','en-US']){
 const zh=locale==='zh-CN'
 test(`video enrollment and automatic scenes ${width} ${locale}`,async({page},info)=>{
  await page.setViewportSize({width,height:960})
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())})
  const f=await fixture(page,locale);await f.open()
  await expect.poll(()=>page.locator('.result-panel audio').evaluate((a:HTMLAudioElement)=>a.readyState)).toBeGreaterThanOrEqual(1)
  // Seconds must not be interpreted as a waveform ratio.
  await page.locator('.video-section').nth(1).locator('.video-scene-time').click()
  await expect.poll(()=>page.locator('.result-panel audio').evaluate((a:HTMLAudioElement)=>a.currentTime)).toBe(2)
  await page.locator('.video-section').first().getByRole('button',{name:zh?'加入声纹库':'Add to voiceprint library',exact:true}).click()
  const dialog=page.getByRole('dialog')
  await expect(dialog.locator('.video-enrollment-row')).toHaveCount(40)
  await expect(dialog.getByRole('checkbox').first()).toBeDisabled()
  await dialog.getByRole('checkbox').nth(1).check()
  await dialog.screenshot({path:info.outputPath('sentence-selection.png')})
  await dialog.getByRole('button',{name:zh?'试听语句 3':'Preview sentence 3',exact:true}).click()
  await expect.poll(()=>dialog.locator('audio').evaluate((a:HTMLAudioElement)=>a.currentTime)).toBeGreaterThanOrEqual(2)
  await expect.poll(()=>dialog.locator('audio').evaluate((a:HTMLAudioElement)=>a.paused)).toBe(true)
  expect(await dialog.locator('audio').evaluate((a:HTMLAudioElement)=>a.currentTime)).toBeLessThan(3.3)
  await dialog.getByRole('button',{name:zh?'下一步：选择人员':'Next: choose person'}).click()
  await expect(dialog.locator('select')).toHaveValue('existing')
  await dialog.getByRole('button',{name:zh?'返回选择语句':'Back to sentences'}).click()
  await expect(dialog.getByRole('checkbox').nth(1)).toBeChecked()
  await dialog.getByRole('button',{name:zh?'下一步：选择人员':'Next: choose person'}).click()
  if(width===390){await dialog.locator('select').selectOption('');await dialog.getByRole('textbox').first().fill('New presenter')}
  expect(await dialog.locator('button:visible,select:visible,input:not([type=checkbox]):visible').evaluateAll(items=>items.every(item=>item.getBoundingClientRect().height>=44))).toBe(true)
  expect(await dialog.evaluate(e=>e.scrollWidth<=e.clientWidth)).toBe(true)
  await page.screenshot({path:info.outputPath('enrollment.png'),fullPage:true})
  await dialog.getByRole('button',{name:zh?'确认加入':'Confirm import',exact:true}).click()
  await expect(dialog).toHaveCount(0)
  expect(f.state.imports).toEqual([{job_id:'enrollment-video',segment_ids:[2]}])
  expect(f.state.created).toBe(width===390?1:0)
  const root=page.locator('.video-transcript')
  await root.evaluate(e=>e.scrollTop=e.scrollHeight);await expect(page.locator('.video-section')).toHaveCount(40)
  await root.evaluate(e=>e.scrollTop=e.scrollHeight);await expect(page.locator('.video-section')).toHaveCount(55)
  await expect(root.getByRole('button',{name:zh?'加载更多':'Load more',exact:true})).toHaveCount(0)
  await page.getByRole('button',{name:zh?'逐句明细':'Segment details',exact:true}).click()
  await expect(page.locator('.segments article')).toHaveCount(40)
  await page.locator('.segments').evaluate(e=>e.scrollTop=e.scrollHeight)
  await expect(page.locator('.segments article')).toHaveCount(80)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  expect(errors).toEqual([])
 })
}

test('video enrollment retains selection after failure and retries library loading',async({page})=>{
 const f=await fixture(page);f.state.failPeople=true;await f.open()
 await page.locator('.video-section').first().getByRole('button',{name:'加入声纹库'}).click()
 const dialog=page.getByRole('dialog');await expect(dialog.getByRole('checkbox').nth(1)).toBeDisabled()
 f.state.failPeople=false;await dialog.getByRole('button',{name:'重试',exact:true}).click()
 await dialog.getByRole('checkbox').nth(1).check();await dialog.getByRole('button',{name:'下一步：选择人员'}).click()
 f.state.failImport=503;await dialog.getByRole('button',{name:'确认加入'}).click();await expect(dialog.getByRole('alert')).toContainText('Import unavailable')
 f.state.failImport=0;await dialog.getByRole('button',{name:'确认加入'}).click();await expect(dialog).toHaveCount(0)
 await page.locator('.video-section').first().getByRole('button',{name:'加入声纹库'}).click();await expect(dialog.getByRole('checkbox').nth(1)).toBeDisabled()
 await dialog.getByRole('button',{name:'关闭加入声纹库'}).click()
 await page.locator('.video-transcript').getByRole('button',{name:'加载更多'}).dispatchEvent('click');await expect(page.locator('.video-section')).toHaveCount(40)
 await page.getByRole('button',{name:/Other video/}).first().click();await expect(page.locator('.video-section')).toHaveCount(20)
})

test('video duplicate conflict refreshes samples and closing stops preview',async({page})=>{
 const f=await fixture(page);await f.open()
 const entry=page.locator('.video-section').first().getByRole('button',{name:'加入声纹库'})
 await entry.click();const dialog=page.getByRole('dialog')
 await dialog.getByRole('checkbox').nth(1).check();await dialog.getByRole('button',{name:'下一步：选择人员'}).click()
 f.state.people[0].samples.push({id:'concurrent-sample',source_job_id:'enrollment-video',source_segment_id:2,state:'ready'})
 f.state.failImport=409;await dialog.getByRole('button',{name:'确认加入'}).click()
 await expect(dialog.getByRole('button',{name:'确认加入'})).toBeDisabled()
 await dialog.getByRole('button',{name:'返回选择语句'}).click();await expect(dialog.getByRole('checkbox').nth(1)).toBeDisabled()
 await dialog.getByRole('button',{name:'试听语句 5',exact:true}).click()
 const audio=await dialog.locator('audio').elementHandle()
 await dialog.getByRole('button',{name:'关闭加入声纹库'}).click()
 expect(await audio!.evaluate((a:HTMLAudioElement)=>a.paused)).toBe(true)
 await entry.click();await expect(page.getByRole('dialog').getByRole('checkbox',{checked:true})).toHaveCount(0)
})

test('task change discards enrollment UI while an accepted request finishes',async({page})=>{
 const f=await fixture(page);await f.open()
 let release:()=>void=()=>{};let entered=false
 const gate=new Promise<void>(resolve=>{release=resolve})
 await page.route('**/api/v1/voiceprints/people/*/samples/from-asr',async route=>{entered=true;await gate;await route.fulfill({status:201,json:{items:[]}})})
 await page.locator('.video-section').first().getByRole('button',{name:'加入声纹库'}).click()
 const dialog=page.getByRole('dialog');await dialog.getByRole('checkbox').nth(1).check();await dialog.getByRole('button',{name:'下一步：选择人员'}).click();await dialog.getByRole('button',{name:'确认加入'}).click()
 await expect.poll(()=>entered).toBe(true)
 // Simulate selection changing outside the modal, as with external navigation.
 await page.getByRole('button',{name:/Other video/}).first().evaluate((button:HTMLButtonElement)=>button.click())
 await expect(dialog).toHaveCount(0)
 const response=page.waitForResponse(r=>r.url().endsWith('/samples/from-asr'));release();await response
 await expect(page.getByText('已加入 1 个声纹样本。',{exact:true})).toHaveCount(0)
 await page.locator('.video-section').first().getByRole('button',{name:'加入声纹库'}).click()
 await expect(page.getByRole('dialog').getByRole('checkbox',{checked:true})).toHaveCount(0)
})
