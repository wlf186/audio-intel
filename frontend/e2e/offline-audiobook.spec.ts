import {test,expect} from '@playwright/test'
import {execFileSync} from 'node:child_process'
import path from 'node:path'
import {pathToFileURL} from 'node:url'

const root=path.resolve('..')
const python=path.join(root,process.platform==='win32'?'.runtime/api/Scripts/python.exe':'.runtime/api/bin/python')
const fixture=`
import hashlib,json,sys
from pathlib import Path
import av
import numpy as np
from audio_intel.audiobook import Book
out=Path(sys.argv[1]); root=out.parent
(root/'input').mkdir(parents=True); (root/'reading').mkdir()
lines=['阅读时，声音跟随文字。','第二片段。','第三片段。']
chapter=''.join(lines)
source=chapter*2
ids=['one','two']
(root/'input/document.txt').write_text(source,encoding='utf-8')
(root/'input/document.json').write_text(json.dumps({'sections':[dict(id=id,start=i*len(chapter),end=(i+1)*len(chapter)) for i,id in enumerate(ids)],'plans':{id:[dict(text=t) for t in lines] for id in ids},'headings':[dict(offset=len(lines[0]),title=lines[1])]}),encoding='utf-8')
mp3=root/'001.mp3'
with av.open(str(mp3),'w') as container:
    stream=container.add_stream('libmp3lame',rate=24000)
    stream.layout='mono'
    frame=av.AudioFrame.from_ndarray(np.zeros((1,96000),dtype='float32'),format='fltp',layout='mono')
    frame.sample_rate=24000
    for packet in stream.encode(frame):container.mux(packet)
    for packet in stream.encode():container.mux(packet)
digest=hashlib.sha256(mp3.read_bytes()).hexdigest()
for id in ids:(root/'reading'/f'{id}.json').write_text(json.dumps({'version':1,'sha256':digest,'samples':[[0,24000],[30000,54000],[60000,96000]]}))
section=dict(id='one',title='第一章',artifact_name=mp3.name,duration=4,sample_rate=24000)
job=dict(id='book',request={'document':{'title':'离线听读','text_hash':hashlib.sha256(source.encode()).hexdigest()}},result={'artifacts':[{'name':mp3.name,'sha256':digest}],'document':{'sections':[section,dict(section,id='two',title='第二章')]}})
book=Book(job,root,[(mp3.name,mp3)])
with out.open('wb') as handle:handle.writelines(book.html(book.sections))
(root/'reading/two.json').unlink()
with (root/'single.html').open('wb') as handle:handle.writelines(book.html([book.sections[1]],single=True))
`
for(const width of [390,1440])test('offline audiobook playback and resume at '+width,async({page,context},info)=>{
 const file=info.outputPath('book.html')
 execFileSync(python,['-c',fixture,file],{cwd:root})
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 await context.setOffline(true)
 await page.setViewportSize({width,height:844})
 await page.goto(pathToFileURL(file).href)
 await expect(page).toHaveTitle('离线听读')
 await page.getByRole('button',{name:'开始听'}).click()
 await expect(page.locator('article .active')).toHaveText('阅读时，声音跟随文字。')
 await page.getByLabel('倍速').selectOption('1.5')
 await page.locator('audio').evaluate((a:HTMLAudioElement)=>{a.currentTime=1.6})
 await page.getByRole('button',{name:'暂停'}).click()
 await page.reload()
 await expect(page.locator('#play')).toContainText('继续')
 await expect(page.getByLabel('倍速')).toHaveValue('1.5')
 const directory=page.locator('#toc')
 for(const title of ['第二章','第二片段。','第二章','第一章']){
   if(width===390)await page.locator('#menu').click()
   await directory.getByRole('link',{name:title,exact:true}).click()
   await expect(directory.locator('[aria-current]')).toHaveText(title==='第二章'?title:'第一章')
 }
 if(width===390){
   await page.locator('#menu').click()
   await expect(directory.locator('[aria-current]')).toBeFocused()
   await page.keyboard.press('Escape')
 }
 await page.locator('#play').click()
 await expect.poll(()=>page.locator('audio').evaluate((a:HTMLAudioElement)=>a.currentTime)).toBeGreaterThanOrEqual(1.59)
 await page.locator('#more').click()
 const clock=()=>page.locator('audio').evaluate((a:HTMLAudioElement)=>a.currentTime)
 const seek=async(t:number)=>page.locator('audio').evaluate((a:HTMLAudioElement,v)=>{a.pause();a.currentTime=v},t)
 await seek(2.85)
 await page.locator('#prev').click()
 expect(await clock()).toBeGreaterThanOrEqual(1.24)
 expect(await clock()).toBeLessThan(2)
 await seek(2.4)
 await page.locator('#restart').click()
 expect(await clock()).toBeLessThan(2)
 await page.locator('#next').click()
 expect(await clock()).toBeGreaterThanOrEqual(2.49)
 await page.locator('#next').click()
 await expect(directory.locator('.listening')).toHaveText('第二章')
 await page.locator('#prev').click()
 await expect(directory.locator('.listening')).toHaveText('第一章')
 await expect.poll(clock).toBeGreaterThanOrEqual(2.49)
 await seek(0)
 await expect(page.locator('#prev')).toBeDisabled()
 await expect(page.getByText('位置码')).toHaveCount(0)
 await page.goto(pathToFileURL(path.join(path.dirname(file),'single.html')).href)
 await page.locator('#play').click()
 await page.locator('#more').click()
 await expect(page.getByRole('button',{name:'上一章'})).toBeDisabled()
 await expect(page.getByRole('button',{name:'下一章'})).toBeDisabled()
 await page.getByRole('button',{name:'重播本章'}).click()
 for(const button of await page.locator('#extras button').all()){
   const box=await button.boundingBox()
   expect(box!.width).toBeGreaterThanOrEqual(44)
   expect(box!.height).toBeGreaterThanOrEqual(44)
 }
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 expect(errors).toEqual([])
})
