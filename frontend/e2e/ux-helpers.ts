import {expect,type Page} from './fixtures'

export async function openTranscriptExport(page:Page){
 const trigger=page.locator('.export-mobile-trigger')
 if(await trigger.isVisible())await trigger.click()
}
export async function closeTranscriptExport(page:Page){
 const dialog=page.locator('.transcript-export-modal')
 if(await dialog.isVisible())await dialog.locator('.modal-close').click()
}
export async function openLibraryImport(page:Page){
 await page.getByRole('button',{name:/^(资料库工具|Library tools)$/}).click()
 await page.getByRole('button',{name:/^(导入完整包|Import full archive)$/}).click()
}
export async function openSampleForm(page:Page){
 if(await page.locator('.person-row').first().isVisible()&&await page.locator('.samples-panel').isHidden())await page.locator('.person-row').first().click()
 const button=page.locator('.add-sample-toggle')
 if(await button.getAttribute('aria-expanded')==='false')await button.click()
}
export async function assertReachable(page:Page,width:number){
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
 const clipped=await page.locator('button:visible,select:visible,summary:visible,a:visible').evaluateAll(elements=>elements.filter(element=>{
  const dialog=[...document.querySelectorAll('dialog[open]')].at(-1)
  if(element.matches(':disabled')||element.closest('[inert]')||(dialog&&!dialog.contains(element)))return false
  const rect=element.getBoundingClientRect()
  if(rect.bottom<=0||rect.top>=innerHeight||rect.width===0)return false
  if(rect.left<-.5||rect.right>innerWidth+.5)return true
  if(innerWidth===390&&(rect.width<44||rect.height<44))return true
  let parent=element.parentElement
  while(parent){
   const style=getComputedStyle(parent),box=parent.getBoundingClientRect()
   if(['hidden','clip','auto','scroll'].includes(style.overflowX)&&(rect.left<box.left-.5||rect.right>box.right+.5))return true
   parent=parent.parentElement
  }
  return false
 }).map(element=>element.getAttribute('aria-label')||element.textContent?.trim()))
 expect(clipped).toEqual([])
}
