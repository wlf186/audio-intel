import type {Page} from '@playwright/test'
export async function showReadingOptions(page:Page){const options=page.locator('.reading-reader-tools .reader-options');await options.waitFor();if(await options.getAttribute('open')===null)await options.locator('summary').click()}
export async function closeReadingOptions(page:Page){const options=page.locator('.reading-reader-tools .reader-options');if(await options.count()&&await options.getAttribute('open')!==null)await options.locator('summary').click()}
export async function showOptimizationDetails(page:Page){if(!await page.getByRole('dialog').count())await page.getByRole('button',{name:'优化详情',exact:true}).click()}
export async function closeOptimizationDetails(page:Page){const dialog=page.getByRole('dialog',{name:'优化详情',exact:true});if(await dialog.count())await dialog.getByRole('button',{name:'关闭',exact:true}).click()}
export async function showMediaStatus(page:Page){const options=page.locator('.reading-workspace-tools>.reader-options');await options.waitFor();if(await options.getAttribute('open')===null)await options.locator('summary').click()}
