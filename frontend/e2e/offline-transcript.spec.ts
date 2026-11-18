import {expect,test} from '@playwright/test'
import {execFileSync} from 'node:child_process'
import path from 'node:path'
import {pathToFileURL} from 'node:url'

const root=path.resolve('..')
const python=path.join(root,process.platform==='win32'?'.runtime/api/Scripts/python.exe':'.runtime/api/bin/python')
const fixture=`
from pathlib import Path
import sys
from audio_intel.transcript_html import render
p=Path(sys.argv[1])
p.parent.mkdir(parents=True,exist_ok=True)
rows=[dict(speaker="林悦", start=n*300, end=n*300+30, text="我们将分批上线，首周跟踪反馈。", original_text="Source passage "+str(n), group_summary=dict(state="ready",sentences=["团队将分批上线。"],subgroups=[dict(state='ready',start=n*300,end=n*300+30,sentences=['Fine detail '+str(n)])]),review_status="needs_review",review_note="核对说明") for n in range(8)]
p.write_bytes(render("产品评审会议", dict(_optimized=True, text_version="comparison",optimization_policy=6, duration=2130, segments=rows), p.parent))
`
for(const width of [1680,1440,1280,1200,1199,960,959,768,767,390])test('offline HTML search, disclosure and print at '+width,async({page,context},info)=>{
 const file=info.outputPath('reading.html')
 execFileSync(python,['-c',fixture,file],{cwd:root})
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
 await context.setOffline(true)
 await page.setViewportSize({width,height:width===390?844:width===1280?720:900})
 await page.goto(pathToFileURL(file).href)
 await page.screenshot({path:info.outputPath('reader.png')})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 if(width>=960){
  const visible=await page.locator('article').evaluate(article=>{
   const bounds=article.getBoundingClientRect()
   let top=Math.max(0,bounds.top),bottom=Math.min(innerHeight,bounds.bottom)
   for(let ancestor=article.parentElement;ancestor;ancestor=ancestor.parentElement){
    if(/auto|scroll|hidden|clip/.test(getComputedStyle(ancestor).overflowY)){
     const rect=ancestor.getBoundingClientRect();top=Math.max(top,rect.top);bottom=Math.min(bottom,rect.bottom)
    }
   }
   return (bottom-top)/innerHeight
  })
  expect(visible).toBeGreaterThanOrEqual(.6)
 }
 if(width<1200){
  await page.locator('#menu').click()
  await page.locator('nav a').first().click()
  await expect(page.locator('nav')).toBeHidden()
 }
 const note=page.locator('.speech .margin-note').first()
 if(width>=768){
  await expect(note).toBeVisible()
  await expect(note.locator('p').first()).toHaveCSS('color','rgb(40, 101, 75)')
  const rect=await note.boundingBox()
  expect(rect!.x+rect!.width).toBeLessThanOrEqual(width)
  const source=page.locator('.source').first()
  expect(await source.evaluate(el=>el.hasAttribute('open'))).toBe(width>=960)
  const aligned=await note.evaluate(n=>Math.abs(n.getBoundingClientRect().top-n.parentElement!.getBoundingClientRect().top)<2 && n.getBoundingClientRect().left>n.parentElement!.getBoundingClientRect().right)
  expect(aligned).toBeTruthy()
 }else await expect(note).toBeHidden()
 await page.locator('#brief').click()
 await expect(note.getByText('Fine detail 0')).toBeVisible()
 await page.locator('.summary-jump').first().click()
 await expect(page.locator('.body').first()).toBeVisible()
 await page.locator('#brief').click()
 await expect(page.locator('.body').first()).toBeHidden()
 await page.locator('#find').click()
 await page.locator('#query').fill('Fine detail 7')
 await expect(page.locator('#count')).toHaveText('1 / 1')
 await page.locator('#query').fill('Source passage')
 await expect(page.locator('#count')).toHaveText('1 / 8')
 await page.locator('#next').click()
 await expect(page.locator('#count')).toHaveText('2 / 8')
 await expect(page.locator('.source p').nth(1)).toBeVisible()
 const pdf=info.outputPath('print.pdf')
 await page.pdf({path:pdf})
 execFileSync(python,['-c',"from pypdf import PdfReader; import sys; text=' '.join(p.extract_text() for p in PdfReader(sys.argv[1]).pages); text=' '.join(text.split()); assert 'Source passage 0' in text and 'Source passage 7' in text and all(text.count('Fine detail '+str(i))==1 for i in range(8)), text",pdf])
 await page.locator('#query').fill('')
 await expect(page.locator('.body').nth(1)).toBeHidden()
 expect(errors).toEqual([])
})

test('topic hierarchy, aligned notes and mobile reading position',async({page,context},info)=>{
 const file=info.outputPath('topics.html')
 const program=`
from pathlib import Path
import sys
from tests.test_reading_outline import fixture
from audio_intel.reading_outline import build
from audio_intel.transcript_html import render
result,catalogue=fixture()
for topic,title in zip(catalogue['topics'],['让长文章更好读','下一期安排']):
    topic.update(title=title,summary='先让读者进入现场，再补充采访背景。')
for n,scene in enumerate(result['video']['scenes']):
    scene['title_summary']['title']=['画面 1 · 采访的开头','编排与校对','画面 2 · 照片的选择'][n]
result['reading_outline']=build(result,catalogue)
result.update(_optimized=True,text_version='optimized',duration=2520)
for n,row in enumerate(result['segments']):
    row.update(speaker_label='林悦',text='我读完这篇采访，记住的是开头那个场景。雨停了，书店老板把椅子搬到门口，坐下来等第一位客人。')
    row['group_summary']=dict(state='ready',sentences=['用书店开门的动作开篇，背景留到第二段。']) if n!=1 else dict(state='skipped',subgroups=[dict(state='ready',start=10,end=20,sentences=['最后一条分段小结。'])])
p=Path(sys.argv[1])
p.parent.mkdir(parents=True,exist_ok=True)
p.write_bytes(render('编辑部周会',result,p.parent))
`
 execFileSync(python,['-c',program,file],{cwd:root})
 await context.setOffline(true)
 await page.setViewportSize({width:1440,height:900})
 await page.goto(pathToFileURL(file).href)
 const child=page.locator('nav .level-3').first()
 const parent=page.locator('nav .level-2').first()
 const textStart=(element:Element)=>{
  const range=document.createRange()
  range.selectNodeContents(element)
  return range.getBoundingClientRect().left
 }
 expect(await child.evaluate(textStart)-await parent.evaluate(textStart)).toBeGreaterThanOrEqual(24)
 await page.screenshot({path:info.outputPath('desktop.png')})
 const note=page.locator('.speech .margin-note').first()
 for(const width of [1280,1279,390]){
  await page.setViewportSize({width,height:844})
  if(width===1280)await expect(note).toBeVisible()
  else await expect(note).toBeHidden()
 }
 await page.locator('#menu').click()
 const last=page.locator('nav .level-2').last()
 await last.click()
 await page.locator('#menu').click()
 await expect(last).toBeFocused()
 await page.keyboard.press('Escape')
 const position=await page.evaluate(()=>window.scrollY)
 await page.locator('#brief').click()
 await expect(page.getByText('最后一条分段小结。',{exact:true})).toBeVisible()
 await page.screenshot({path:info.outputPath('mobile.png')})
 await page.locator('#brief').click()
 expect(Math.abs(position-await page.evaluate(()=>window.scrollY))).toBeLessThanOrEqual(1)
})
