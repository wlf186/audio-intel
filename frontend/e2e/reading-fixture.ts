import type {Page} from './fixtures'
import {uxFixture} from './ux-fixtures'
export async function readingFixture(page:Page,audio=false){
 const f=await uxFixture(page),{state}=f
 if(audio)delete state.job.result!.video
 const run={id:'e'.repeat(32),state:'succeeded',published:true,reading_plan_version:2,model:'test',rewrite_mode:'editorial',revision:1,calls_limit:100,topic_state:'completed',topic_revision:1,summary_status:{version:2,state:'completed',revision:1,counts:{group:{total:3,processed:3,ready:3,failed:0},scene:{total:3,processed:3,ready:3,failed:0}}}}
 const items=[0,1,2].map(i=>({id:`block${i}`,kind:'block',title:`标题${i}`,number:i+1,start:i*2,end:i*2+2,primary_topic_id:i===1?'b':'a',related_topic_ids:[],occurrence_id:`occurrence${i}`,paragraph_id:`p${i}`,scene_ids:audio?[]:[`scene-${i}`],frame_numbers:audio?[]:[i+1],source_refs:[{segment_id:i,start_offset:0,end_offset:state.job.result!.segments![i].text.length,start:i*2,end:i*2+2}]}))
 const groups=items.map((item,i)=>({topic_id:item.primary_topic_id,occurrence_id:item.occurrence_id,title:i===1?'发布流程':'产品设计',summary:'',primary_item_ids:[item.id],related_item_ids:[],recurring:i===2}))
 const outline={version:2,items,groups,merged_groups:[{...groups[0],occurrence_id:'mergeda',primary_item_ids:['block0','block2']},{...groups[1],occurrence_id:'mergedb'}]}
 await page.route('**/api/v1/jobs/*/transcript-optimization',route=>route.fulfill({json:{enabled:true,published:run,candidate:null}}))
 await page.route('**/transcript-optimization/summaries?*',route=>route.fulfill({json:{run_id:run.id,summary_status:run.summary_status,groups:{},scenes:{},blocks:{}}}))
 await page.route('**/transcript-optimization/topics?*',route=>route.fulfill({json:{run_id:run.id,version:3,result:{topics:[],unclassified:[]},outline}}))
 await page.route('**/transcript-optimization/result?*',route=>route.fulfill({json:{run_id:run.id,reading_plan_version:2,reading_blocks:items,complete:true,paragraphs:items.map((b,i)=>({id:`p${i}`,ordinal:i,speaker:'s0',speaker_label:'Alice',start:b.start,end:b.end,original_text:'测试原文。',text:`测试原文${i}。`,segment_ids:[i],scene_ids:b.scene_ids,reading_block_id:b.id})),scenes:[]}}))
 return {...f,run,outline}
}
