import {mkdir,readFile,writeFile} from 'node:fs/promises'
import {resolve} from 'node:path'
import {chromium} from '../frontend/node_modules/@playwright/test/index.mjs'

const baseUrl=process.env.AUDIO_INTEL_CAPTURE_BASE_URL||'http://127.0.0.1:20910'
const outputDir=resolve(process.argv[2]||'docs/assets/readme')

function demoWav(seconds=12,rate=16000){
 const sampleCount=seconds*rate
 const buffer=Buffer.alloc(44+sampleCount*2)
 buffer.write('RIFF',0);buffer.writeUInt32LE(36+sampleCount*2,4);buffer.write('WAVE',8)
 buffer.write('fmt ',12);buffer.writeUInt32LE(16,16);buffer.writeUInt16LE(1,20);buffer.writeUInt16LE(1,22)
 buffer.writeUInt32LE(rate,24);buffer.writeUInt32LE(rate*2,28);buffer.writeUInt16LE(2,32);buffer.writeUInt16LE(16,34)
 buffer.write('data',36);buffer.writeUInt32LE(sampleCount*2,40)
 for(let index=0;index<sampleCount;index++){
  const envelope=Math.min(1,index/(rate*.2),(sampleCount-index)/(rate*.2))
  const sample=Math.sin(2*Math.PI*180*index/rate)*.08*envelope
  buffer.writeInt16LE(Math.round(sample*32767),44+index*2)
 }
 return buffer
}

async function submit(path,form){
 const response=await fetch(`${baseUrl}${path}`,{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()},body:form})
 if(!response.ok)throw new Error(`${path} returned ${response.status}: ${await response.text()}`)
 return response.json()
}

async function waitForJob(id){
 const deadline=Date.now()+30000
 while(Date.now()<deadline){
  const response=await fetch(`${baseUrl}/api/v1/jobs/${id}`)
  if(!response.ok)throw new Error(`job ${id} returned ${response.status}`)
  const job=await response.json()
  if(job.state==='succeeded')return job
  if(['failed','cancelled'].includes(job.state))throw new Error(`job ${id} ended in ${job.state}: ${job.error_message||''}`)
  await new Promise(resolveWait=>setTimeout(resolveWait,100))
 }
 throw new Error(`job ${id} did not finish before the capture deadline`)
}

async function seedDemoJobs(){
 const asrForm=new FormData()
 asrForm.set('file',new Blob([demoWav()],{type:'audio/wav'}),'meeting-demo.wav')
 asrForm.set('display_name','meeting-demo.wav')
 asrForm.set('language','Auto')
 asrForm.set('speaker_count','auto')
 asrForm.set('model','qwen3-asr-0.6b')
 asrForm.set('diarize','true')
 asrForm.set('align','true')
 asrForm.set('use_voiceprint_library','true')
 asrForm.set('compute_device','cpu')
 asrForm.set('accelerate_single_task','true')
 const asr=await submit('/api/v1/asr/jobs',asrForm)

 const ttsForm=new FormData()
 ttsForm.set('text','欢迎使用 Sandevistan Audio。这是一套完全本地运行的语音智能服务。')
 ttsForm.set('display_name','本地语音工作站演示')
 ttsForm.set('language','Chinese')
 ttsForm.set('voice_mode','preset')
 ttsForm.set('speaker','Vivian')
 ttsForm.set('model','qwen3-tts-0.6b')
 ttsForm.set('response_format','wav')
 ttsForm.set('compute_device','cpu')
 ttsForm.set('accelerate_single_task','true')
 const tts=await submit('/api/v1/tts/jobs',ttsForm)

 await Promise.all([waitForJob(asr.id),waitForJob(tts.id)])
}

async function webpFromPng(browser,png,quality=.9){
 const codec=await browser.newPage({viewport:{width:16,height:16}})
 const encoded=await codec.evaluate(async ({data,qualityValue})=>{
  const image=new Image()
  image.src=`data:image/png;base64,${data}`
  await image.decode()
  const canvas=document.createElement('canvas')
  canvas.width=image.naturalWidth;canvas.height=image.naturalHeight
  canvas.getContext('2d').drawImage(image,0,0)
  return canvas.toDataURL('image/webp',qualityValue).split(',')[1]
 },{data:png.toString('base64'),qualityValue:quality})
 await codec.close()
 return Buffer.from(encoded,'base64')
}

async function capturePage(browser,hash,readySelector,name){
 const errors=[]
 const page=await browser.newPage({viewport:{width:1584,height:950},deviceScaleFactor:1})
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 page.on('pageerror',error=>errors.push(error.message))
 await page.goto(`${baseUrl}/#${hash}`,{waitUntil:'networkidle'})
 await page.locator(readySelector).first().waitFor({state:'visible'})
 await page.waitForTimeout(500)
 await page.evaluate(()=>{
  window.scrollTo({top:0,left:0,behavior:'auto'})
  document.documentElement.scrollLeft=0
  document.body.scrollLeft=0
  document.querySelector('#main-content')?.scrollTo({top:0,left:0,behavior:'auto'})
 })
 const png=await page.screenshot({type:'png',fullPage:false})
 const webp=await webpFromPng(browser,png)
 await writeFile(resolve(outputDir,name),webp)
 await page.close()
 if(errors.length)throw new Error(`${hash} emitted browser errors: ${errors.join(' | ')}`)
 return webp
}

async function captureSocialPreview(browser,hero){
 const logo=await readFile(resolve('frontend/public/sandevistan-audio.svg'))
 const page=await browser.newPage({viewport:{width:1280,height:640},deviceScaleFactor:1})
 await page.setContent(`<!doctype html><html><head><style>
  *{box-sizing:border-box}html,body{margin:0;width:1280px;height:640px;overflow:hidden;background:#05090a;color:#edf3ef;font-family:Inter,Segoe UI,Arial,sans-serif}
  body{position:relative;background:radial-gradient(circle at 18% 15%,#112326 0,#070d0f 38%,#030506 100%)}
  body:before{content:"";position:absolute;inset:0;background-image:linear-gradient(rgba(0,231,238,.055) 1px,transparent 1px),linear-gradient(90deg,rgba(0,231,238,.055) 1px,transparent 1px);background-size:38px 38px;mask-image:linear-gradient(90deg,#000,transparent 73%)}
  .accent{position:absolute;left:0;top:0;width:12px;height:640px;background:#f4ed00}.copy{position:absolute;left:74px;top:76px;width:550px;z-index:2}
  .brand{display:flex;align-items:center;gap:24px}.brand img{width:108px}.eyebrow{color:#00e7ee;font-size:18px;letter-spacing:.18em;text-transform:uppercase}
  h1{margin:32px 0 20px;font-size:62px;line-height:1.03;letter-spacing:-.045em;color:#fff}.yellow{color:#f4ed00}
  p{margin:0;width:515px;font-size:27px;line-height:1.38;color:#c3d0cb}.tags{display:flex;gap:10px;margin-top:34px;flex-wrap:wrap}
  .tags span{border:1px solid #264247;background:#081214;padding:9px 13px;color:#9fb3ae;font:600 15px ui-monospace,SFMono-Regular,Consolas,monospace}
  .screen{position:absolute;left:650px;top:74px;width:760px;height:500px;border:1px solid #00e7ee;overflow:hidden;box-shadow:0 28px 80px rgba(0,0,0,.6);transform:perspective(1100px) rotateY(-7deg)}
  .screen img{height:100%;width:auto;transform:translateX(-115px);filter:saturate(1.08) contrast(1.03)}
  .screen:after{content:"";position:absolute;inset:0;box-shadow:inset 0 0 55px rgba(0,0,0,.35)}
 </style></head><body><div class="accent"></div><section class="copy"><div class="brand"><img src="data:image/svg+xml;base64,${logo.toString('base64')}"><span class="eyebrow">Local speech intelligence</span></div><h1>Private speech.<br><span class="yellow">Your machine.</span></h1><p>Offline-first ASR, diarization, timestamps, voiceprints, TTS, and voice cloning for Linux and Windows.</p><div class="tags"><span>QWEN3 ASR + TTS</span><span>WEB UI + API</span><span>CPU + NVIDIA GPU</span></div></section><div class="screen"><img src="data:image/webp;base64,${hero.toString('base64')}"></div></body></html>`)
 await page.screenshot({path:resolve(outputDir,'social-preview.png'),type:'png'})
 await page.close()
}

await mkdir(outputDir,{recursive:true})
await seedDemoJobs()
const browser=await chromium.launch({headless:true,executablePath:'/usr/bin/chromium',args:['--no-sandbox']})
try{
 const hero=await capturePage(browser,'asr','.segments article','asr-workspace.webp')
 await capturePage(browser,'tts','.audio-card','tts-workspace.webp')
 await capturePage(browser,'jobs','.jobs-table .table-row','job-history.webp')
 await captureSocialPreview(browser,hero)
}finally{
 await browser.close()
}

console.log(`README assets written to ${outputDir}`)
