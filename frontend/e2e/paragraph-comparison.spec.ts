import {test,expect} from './fixtures'
import {readingFixture} from './reading-fixture'

for(const width of [1960,1280,390])test(`paragraph comparison stays local at ${width}`,async({page})=>{
 await page.setViewportSize({width,height:width===1280?720:1040})
 const {errors}=await readingFixture(page)
 await page.goto('/#asr');await page.getByRole('tab',{name:'任务与结果',exact:true}).click()
 await page.getByRole('button',{name:'优化稿',exact:true}).click()
 const row=page.locator('[data-reading-block-id="block1"]')
 const button=row.getByRole('button',{name:'对照此段',exact:true})
 await button.scrollIntoViewIfNeeded()
 const before=(await button.boundingBox())!
 await button.click()
 await expect(page.getByRole('button',{name:'优化稿',exact:true})).toHaveAttribute('aria-pressed','true')
 await expect(page.locator('.optimization-pair')).toHaveCount(1)
 await expect(row.locator('.optimization-pair-columns')).toContainText('测试原文。')
 const collapse=row.getByRole('button',{name:'收起对照',exact:true})
 await expect(collapse).toBeFocused()
 expect(Math.abs((await collapse.boundingBox())!.y-before.y)).toBeLessThan(3)
 await collapse.click();await expect(page.locator('.optimization-pair')).toHaveCount(0)
 await page.getByRole('button',{name:'对照',exact:true}).click();await expect(page.locator('.optimization-pair')).toHaveCount(3)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
 expect(errors).toEqual([])
})
