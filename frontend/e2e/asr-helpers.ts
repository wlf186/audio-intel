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

export async function showAudioOptions(page:Page){const toggle=page.locator('.transport-audio-menu>button');await toggle.waitFor();if(await toggle.getAttribute('aria-expanded')==='false')await toggle.click()}
export async function showAudioTimeline(page:Page){const panel=page.locator('.audio-wave-options');await panel.waitFor();if(await panel.getAttribute('open')===null)await panel.locator('summary').click()}

export async function closeAudioOptions(page:Page){const toggle=page.locator('.transport-audio-menu>button');if(await toggle.count()&&await toggle.getAttribute('aria-expanded')==='true')await toggle.click()}
