import {test,expect} from './fixtures'
import {uxFixture} from './ux-fixtures'
import {activeIntervalIndex,readingIndex} from '../src/lib/readerNavigation'

test('indexed time lookup preserves overlap, zero duration, gap and endpoint semantics',()=>{
 const cases=[[],[{start:0,end:0},{start:0,end:1},{start:2,end:3}],[{start:3,end:5},{start:0,end:4}],[{start:1,end:0}]]
 for(let length=1;length<=100;length+=7)cases.push(Array.from({length},(_,i)=>({start:i*3,end:i*3+(i%4?2:0)})))
 for(const items of cases)for(let time=-1;time<305;time+=.5){
  const active=items.findIndex(item=>time>=item.start&&time<item.end)
  const next=items.findIndex(item=>item.start>=time)
  expect(activeIntervalIndex(items,time)).toBe(active)
  expect(readingIndex(items,time)).toBe(active>=0?active:next>=0?next:items.length-1)
 }
})

for(const width of [1440,390])test(`video search reveals a lazy hit and playback preserves selected mark at ${width}px`,async({page})=>{
 await page.setViewportSize({width,height:940})
 const {errors}=await uxFixture(page)
 await page.goto('/#asr')
 await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(page.locator('.video-section')).toHaveCount(20)
 // The last match is initially outside the progressive rendering window.
 await page.getByPlaceholder('搜索原文').fill('第 60 段')
 await page.locator('.transcript-search-navigation button').last().click()
 const mark=page.locator('.video-transcript .search-current')
 await expect(mark).toHaveText('第 60 段')
 await expect(mark).toBeInViewport()
 await page.locator('.result-panel audio').evaluate((node:HTMLAudioElement)=>{node.currentTime=118.5;node.dispatchEvent(new Event('timeupdate'))})
 await expect(mark).toHaveText('第 60 段')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 expect(errors).toEqual([])
})
