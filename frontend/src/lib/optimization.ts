export type RewriteMode='polish'|'rewrite'|'editorial'
export const rewriteModes:RewriteMode[]=['polish','rewrite','editorial']
const preferenceKey='audio-intel:optimization-preferences:v1'
export function readRewriteMode():RewriteMode{try{const mode=JSON.parse(localStorage.getItem(preferenceKey)||'null')?.rewriteMode;return rewriteModes.includes(mode)?mode:'rewrite'}catch{return 'rewrite'}}
export function saveRewriteMode(rewriteMode:RewriteMode){try{localStorage.setItem(preferenceKey,JSON.stringify({...readPreferences(),rewriteMode}))}catch{/* Keep the selection usable. */}}
function readPreferences():Record<string,unknown>{try{return JSON.parse(localStorage.getItem(preferenceKey)||'{}')||{}}catch{return {}}}
export function readAnalyzeTopics():boolean{return readPreferences().analyzeTopics===true}
export function saveAnalyzeTopics(analyzeTopics:boolean){try{localStorage.setItem(preferenceKey,JSON.stringify({...readPreferences(),analyzeTopics}))}catch{/* In-memory selection remains usable. */}}
import type {VideoScene} from './types'
export type InsightState='pending'|'running'|'ready'|'not_applicable'|'unavailable'
export type SummaryCounts={total:number;processed:number;ready:number;skipped:number;failed:number;model_ready?:number;repaired_ready?:number;keyword_ready?:number;extractive_ready?:number;fallback_ready?:number}
export type SummaryStatus={version:number|null;state:'not_requested'|'pending'|'queued'|'running'|'paused'|'partial'|'completed';revision:number;counts:Partial<Record<'group'|'scene',SummaryCounts>>}
export type InsightMetadata={generation_method?:'model'|'model_repair'|'model_keywords'|'source_excerpt'|null;attempt_count?:number;quality_notice?:'keywords'|'source_excerpt'|null;skip_reason?:'no_substantive_content'|null}
export type GroupInsight=InsightMetadata&{state:InsightState;sentences:string[];error?:string|null}
export type GroupDetail=GroupInsight&{id:string;start:number;end:number}
export type GroupSummary=GroupInsight&{subgroups?:GroupDetail[]}
export type SceneSummary=InsightMetadata&{state:InsightState;title:string|null;error?:string|null}
export type OptimizationInsights={run_id:string;summary_status:SummaryStatus;groups:Record<string,GroupSummary>;scenes:Record<string,SceneSummary>;blocks?:Record<string,SceneSummary>}
export type ReviewState='queued'|'running'|'paused'|'completed'|'unavailable'
export type ReviewOutcome='pending'|'pass'|'fact'|'uncertain'|'unavailable'
export type OptimizationSource={segment_id:number;text:string;start_offset:number;end_offset:number;start:number;end:number}
export type OptimizationSummary={reading_plan_version?:number;topic_state?:string;topic_revision?:number;topic_completed_nodes?:number;topic_total_nodes?:number;calls_limit?:number|null;calls_used?:number|null;budget_error?:string|null;summary_status?:SummaryStatus;phase?:'generation'|'review'|'summaries'|'topics'|'complete';reviewed_parts?:number;total_parts?:number;id:string;job_id:string;state:'queued'|'running'|'pausing'|'paused'|'failed'|'succeeded'|'deleting';published:boolean;review_state?:ReviewState|null;reviewed_units?:number;review_total_units?:number;uncertain_units?:number;unavailable_units?:number;basic_cleanup_units?:number;requires_regeneration?:boolean;model:string;rewrite_mode?:RewriteMode|null;total_chars:number;completed_chars:number;completed_units:number;total_units:number;activity_chars:number;needs_review_units?:number;progress:number;revision:number;error:string|null;created_at:string;updated_at:string}
export type OptimizationStatus={enabled:boolean;published:OptimizationSummary|null;candidate:OptimizationSummary|null}
export type OptimizationParagraph={reading_block_id?:string|null;reading_topic_id?:string|null;occurrence_id?:string|null;group_summary?:GroupSummary|null;review_outcome?:ReviewOutcome|null;generation_method?:'model'|'basic_cleanup'|'mixed'|null;sources?:OptimizationSource[];scene_ids?:string[];merged_source_count?:number;candidate_text?:string|null;review_details?:{stage:string;issues?:{source_quote:string;candidate_quote:string;reason:string}[]}|null;id:string;ordinal:number;speaker:string|null;speaker_label:string|null;start:number;end:number;original_text:string;text:string|null;omitted?:boolean;review_status?:'accepted'|'needs_review'|null;review_reason?:'review_failed'|'invalid_output'|null;segment_ids:number[]}
export type OptimizationResult={reading_plan_version?:number;reading_blocks?:import('../components/ReadingWorkspace').OutlineItem[];summary_status?:SummaryStatus;prompt_version?:number;revision?:number;review_state?:ReviewState|null;reviewed_units?:number;review_total_units?:number;uncertain_units?:number;unavailable_units?:number;basic_cleanup_units?:number;run_id:string;complete:boolean;model:string;rewrite_mode?:RewriteMode|null;needs_review_units?:number;paragraphs:OptimizationParagraph[];scenes:(Omit<VideoScene,'paragraphs'>&{title_summary?:SceneSummary|null;paragraphs:OptimizationParagraph[]})[]}
export type OptimizationConfiguration={token_usage_mode?:'automatic'|'include_usage'|'unavailable'|null;max_output_tokens?:number;protocol?:'openai_chat_completions';has_api_key?:boolean;extra_body?:Record<string,unknown>;structured_output_mode?:'json_schema'|'json_object'|'prompt'|null;base_url:string;use_proxy:boolean;model:string;rewrite_mode?:RewriteMode|null;enabled:boolean;context_length?:number;model_context_length?:number;observed_context_length?:number|null;verified_at?:string;verification_token?:string}
export const optimizationSettingsPath='/api/v1/settings/transcript-optimization'
export const optimizationPath=(id:string)=>`/api/v1/jobs/${id}/transcript-optimization`


export function needsServiceNotice(address:string):boolean{
 try{
  const url=new URL(address.trim())
  if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.search||url.hash)return false
  const host=url.hostname.toLowerCase().replace(/\.$/,'')
  return !(host==='localhost'||host==='[::1]'||/^127\.\d+\.\d+\.\d+$/.test(host))
 }catch{return false}
}

export type CallEstimate={estimated_calls:number;used_calls:number;minimum_limit:number;recommended_limit:number;max_llm_calls:number;sufficient:boolean;can_start:boolean;fingerprint:string;breakdown:Record<string,number>}
export type TokenUsage={
 prompt_tokens:number|null;completion_tokens:number|null;input_recorded:number;output_recorded:number;attempts:number
 total_tokens?:number|null;total_recorded?:number
}
export type CallUsage={
 run_id:string;recorded:boolean;calls_used:number|null;max_llm_calls:number|null;remaining_calls:number|null
 active_duration_ms?:number|null;timing_partial?:boolean;in_progress_calls?:number;token_usage?:TokenUsage
 estimate:CallEstimate|null;stages:Record<string,number>;failed_or_interrupted:number
}
