import {test,expect} from './fixtures'
import {readingFixture} from './reading-fixture'
import {uxFixture} from './ux-fixtures'

for(const width of [1960,1280,390])test(`source selection survives block layout and deduplicates source IDs ${width}`,async({page})=>{
 await page.setViewportSize({width,height:width===1280?720:1040})
 const {state,outline,errors}=await readingFixture(page)
 // Two reading blocks refer to the same original segment: one source sample, not two.
 outline.items[1].source_refs=outline.items[0].source_refs
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 const checks=page.locator('.reading-blocks input[type=checkbox]')
 await checks.first().check()
 await expect(checks.nth(1)).toBeChecked()
 await expect(page.locator('.segment-selection')).toContainText('1 个')
 await page.locator('.segment-selection').getByRole('button',{name:'加入声纹库',exact:true}).click()
 const dialog=page.getByRole('dialog',{name:'加入声纹库',exact:true})
 await expect(dialog).toBeVisible()
 await dialog.getByRole('button',{name:'确认加入',exact:true}).click()
 await expect(dialog).not.toBeVisible()
 expect(state.imports).toHaveLength(1)
 expect(state.imports[0].segment_ids).toEqual([0])
 await expect(checks.first()).not.toBeChecked()
 await checks.first().check();await page.getByRole('button',{name:'清除段落选择',exact:true}).click();await expect(checks.first()).not.toBeChecked()
 const row=page.locator('.reading-blocks article').first()
 expect(await row.evaluate(n=>getComputedStyle(n).display)).toBe('grid')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 expect(errors).toEqual([])
})

test('playback failure is visible without opening media details and retry recovers',async({page})=>{
 await readingFixture(page)
 await page.route('**/api/v1/jobs/ux-video/playback',route=>route.fulfill({status:503,body:'Unavailable'}))
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await expect(page.getByRole('alert').filter({hasText:'音频播放失败'})).toBeVisible()
 await page.unroute('**/api/v1/jobs/ux-video/playback')
 // Restore a valid media response without accessing a real job.
 const wav=Buffer.alloc(44+32000);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(16000,24);wav.writeUInt32LE(32000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(32000,40)
 await page.route('**/api/v1/jobs/ux-video/playback',route=>route.fulfill({contentType:'audio/wav',body:wav}))
 await page.getByRole('button',{name:'重新加载音频',exact:true}).click()
 await expect(page.locator('.playback-feedback')).toHaveCount(0)
 await expect(page.locator('.media-loading')).toHaveCount(0)
})

for(const [width,height] of [[1960,1040],[1792,976],[1470,732],[1280,720],[390,844]])test(`reader visible budget, frames and timeline ${width}x${height}`,async({page})=>{
 await page.setViewportSize({width,height});const {errors}=await readingFixture(page)
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.locator('.reading-blocks img').first().waitFor()
 await expect(page.locator('.media-loading')).toHaveCount(0)
 const area=await page.locator('.reading-blocks').evaluate(n=>{const r=n.getBoundingClientRect();let top=Math.max(0,r.top),bottom=Math.min(innerHeight,r.bottom);for(let p=n.parentElement;p;p=p.parentElement)if(/auto|scroll|hidden|clip/.test(getComputedStyle(p).overflowY)){const b=p.getBoundingClientRect();top=Math.max(top,b.top);bottom=Math.min(bottom,b.bottom)}return {visible:bottom-top,panel:n.closest('.result-panel')!.getBoundingClientRect().height}})
 if(width>=1024){expect(area.visible).toBeGreaterThanOrEqual(359);expect(area.visible/area.panel).toBeGreaterThanOrEqual(.6)}
 const frame=page.locator('.reading-blocks .video-frame').first()
 if(width>=1024)expect(await frame.evaluate(n=>{const a=n.getBoundingClientRect(),b=n.closest('.reading-blocks')!.getBoundingClientRect();return a.top>=b.top&&a.bottom<=b.bottom})).toBeTruthy()
 if(height<=850||width<=700){await page.getByRole('button',{name:'展开时间轴',exact:true}).click();await expect(page.getByRole('dialog',{name:'视频时间轴',exact:true})).toBeVisible();await page.locator('.timeline-scenes').focus();await expect(page.getByRole('dialog').locator('.timeline-preview')).toBeVisible();await page.keyboard.press('Escape');await expect(page.getByRole('dialog')).toBeVisible();await expect(page.locator('.timeline-preview')).toHaveCount(0);await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).click()}
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();expect(errors).toEqual([])
})

test('audio waveform has truthful toggle and no decorative signal label',async({page})=>{
 const {state,errors}=await uxFixture(page);delete state.job.result!.video
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 const wave=page.locator('.wave-area')
 expect(await wave.evaluate(n=>getComputedStyle(n,'::before').content)).toBe('none')
 await page.locator('.audio-wave-options>summary').click();await expect(wave).not.toBeVisible()
 await expect(page.locator('.audio-wave-options>summary')).toHaveText('展开时间轴')
 await page.locator('.audio-wave-options>summary').click();await expect(wave).toBeVisible()
 await expect(page.locator('.audio-wave-options>summary')).toHaveText('收起时间轴');expect(errors).toEqual([])
})
