import type {Page} from './fixtures'
export async function mockTimelineWaveform(page:Page,jobId:string,duration:number){
 await page.route(`**/api/v1/jobs/${jobId}/waveform?*`,route=>{
  const params=new URL(route.request().url()).searchParams
  return route.fulfill({json:{track:params.get('track')||'original',duration,start:Number(params.get('start')||0),end:Number(params.get('end')||duration),waveform:Array(Math.min(2048,Number(params.get('points')||240))).fill(.3)}})
 })
}

export async function scrollTimelineTo(page:Page,start:number,duration=120,span=60){
 await page.locator('.timeline-scroll-viewport').evaluate((node,{start,duration,span})=>{node.scrollLeft=start/(duration-span)*(node.scrollWidth-node.clientWidth)},{start,duration,span})
}
