import type {Page} from './fixtures'
export async function mockTimelineWaveform(page:Page,jobId:string,duration:number){
 await page.route(`**/api/v1/jobs/${jobId}/waveform?*`,route=>{
  const params=new URL(route.request().url()).searchParams
  return route.fulfill({json:{track:params.get('track')||'original',duration,start:Number(params.get('start')||0),end:Number(params.get('end')||duration),waveform:Array(Math.min(2048,Number(params.get('points')||240))).fill(.3)}})
 })
}

export async function scrollTimelineTo(page:Page,start:number,duration=120,span=60){
 const bar=page.getByRole('scrollbar')
 await bar.scrollIntoViewIfNeeded()
 if(start<=0){await bar.press('Home');return}
 if(start>=duration-span){await bar.press('End');return}
 const rail=(await bar.boundingBox())!,thumb=(await page.locator('.timeline-scrollbar-thumb').boundingBox())!
 const target=rail.x+start/(duration-span)*(rail.width-thumb.width)+thumb.width/2
 const y=rail.y+rail.height/2
 await page.mouse.move(thumb.x+thumb.width/2,y);await page.mouse.down()
 await page.mouse.move(target,y,{steps:4});await page.mouse.up()
}
