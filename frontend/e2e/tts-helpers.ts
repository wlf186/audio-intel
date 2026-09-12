import {expect,type Page} from '@playwright/test'

export async function expandTtsSettings(page:Page){
 const toggle=page.locator('.document-settings-toggle')
 const body=page.locator('.tts-settings-body')
 await expect(toggle).toBeVisible()
 // A media-query event must settle before clicking after a viewport change.
 await expect(toggle).toHaveAttribute('aria-expanded',await body.isVisible()?'true':'false')
 if(!await body.isVisible())await toggle.click()
 await expect(body).toBeVisible()
}
