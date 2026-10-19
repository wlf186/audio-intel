import type {Page} from './fixtures'
export async function mockTimelineWaveform(page:Page,jobId:string,duration:number){
 await page.route(`**/api/v1/jobs/${jobId}/waveform?*`,route=>{
  const params=new URL(route.request().url()).searchParams
  return route.fulfill({json:{track:params.get('track')||'original',duration,start:Number(params.get('start')||0),end:Number(params.get('end')||duration),waveform:Array(Math.min(2048,Number(params.get('points')||240))).fill(.3)}})
 })
}
