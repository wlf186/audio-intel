import type {Page} from '@playwright/test'
export async function expandAsrSettings(page:Page){
 const toggle=page.locator('.asr-settings-toggle')
 if(await toggle.getAttribute('aria-expanded')==='false')await toggle.click()
}
export async function expandAsrAdvanced(page:Page){
 await expandAsrSettings(page)
 const toggle=page.locator('.asr-advanced-toggle')
 if(await toggle.getAttribute('aria-expanded')==='false')await toggle.click()
}
export async function showAsrResults(page:Page){await page.getByRole('tab',{name:'任务与结果',exact:true}).click()}
