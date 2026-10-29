export type RewriteMode='polish'|'rewrite'|'editorial'
export const rewriteModes:RewriteMode[]=['polish','rewrite','editorial']
const preferenceKey='audio-intel:optimization-preferences:v1'
export function readRewriteMode():RewriteMode{try{const mode=JSON.parse(localStorage.getItem(preferenceKey)||'null')?.rewriteMode;return rewriteModes.includes(mode)?mode:'rewrite'}catch{return 'rewrite'}}
export function saveRewriteMode(rewriteMode:RewriteMode){try{localStorage.setItem(preferenceKey,JSON.stringify({rewriteMode}))}catch{/* Keep the selection usable. */}}
import type {VideoScene} from './types'
export type ReviewState='queued'|'running'|'paused'|'completed'|'unavailable'
export type ReviewOutcome='pending'|'pass'|'fact'|'uncertain'|'unavailable'
export type OptimizationSource={segment_id:number;text:string;start_offset:number;end_offset:number;start:number;end:number}
export type OptimizationSummary={id:string;job_id:string;state:'queued'|'running'|'pausing'|'paused'|'failed'|'succeeded'|'deleting';published:boolean;review_state?:ReviewState|null;reviewed_units?:number;review_total_units?:number;uncertain_units?:number;unavailable_units?:number;basic_cleanup_units?:number;requires_regeneration?:boolean;model:string;rewrite_mode?:RewriteMode|null;total_chars:number;completed_chars:number;completed_units:number;total_units:number;activity_chars:number;needs_review_units?:number;progress:number;revision:number;error:string|null;created_at:string;updated_at:string}
export type OptimizationStatus={enabled:boolean;published:OptimizationSummary|null;candidate:OptimizationSummary|null}
export type OptimizationParagraph={review_outcome?:ReviewOutcome|null;generation_method?:'model'|'basic_cleanup'|'mixed'|null;sources?:OptimizationSource[];scene_ids?:string[];merged_source_count?:number;candidate_text?:string|null;review_details?:{stage:string;issues?:{source_quote:string;candidate_quote:string;reason:string}[]}|null;id:string;ordinal:number;speaker:string|null;speaker_label:string|null;start:number;end:number;original_text:string;text:string|null;omitted?:boolean;review_status?:'accepted'|'needs_review'|null;review_reason?:'review_failed'|'invalid_output'|null;segment_ids:number[]}
export type OptimizationResult={prompt_version?:number;revision?:number;review_state?:ReviewState|null;reviewed_units?:number;review_total_units?:number;uncertain_units?:number;unavailable_units?:number;basic_cleanup_units?:number;run_id:string;complete:boolean;model:string;rewrite_mode?:RewriteMode|null;needs_review_units?:number;paragraphs:OptimizationParagraph[];scenes:(Omit<VideoScene,'paragraphs'>&{paragraphs:OptimizationParagraph[]})[]}
export type OptimizationConfiguration={base_url:string;use_proxy:boolean;model:string;rewrite_mode?:RewriteMode|null;enabled:boolean;context_length?:number;model_context_length?:number;verified_at?:string;verification_token?:string}
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
