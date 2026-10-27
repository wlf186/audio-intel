export type RewriteMode='polish'|'rewrite'|'editorial'
export const rewriteModes:RewriteMode[]=['polish','rewrite','editorial']
const preferenceKey='audio-intel:optimization-preferences:v1'
export function readRewriteMode():RewriteMode{try{const mode=JSON.parse(localStorage.getItem(preferenceKey)||'null')?.rewriteMode;return rewriteModes.includes(mode)?mode:'rewrite'}catch{return 'rewrite'}}
export function saveRewriteMode(rewriteMode:RewriteMode){try{localStorage.setItem(preferenceKey,JSON.stringify({rewriteMode}))}catch{/* Keep the selection usable. */}}
import type {VideoScene} from './types'
export type OptimizationSummary={id:string;job_id:string;state:'queued'|'running'|'pausing'|'paused'|'failed'|'succeeded'|'deleting';published:boolean;requires_regeneration?:boolean;model:string;rewrite_mode?:RewriteMode|null;total_chars:number;completed_chars:number;completed_units:number;total_units:number;activity_chars:number;needs_review_units?:number;progress:number;revision:number;error:string|null;created_at:string;updated_at:string}
export type OptimizationStatus={enabled:boolean;published:OptimizationSummary|null;candidate:OptimizationSummary|null}
export type OptimizationParagraph={id:string;ordinal:number;speaker:string|null;speaker_label:string|null;start:number;end:number;original_text:string;text:string|null;omitted?:boolean;review_status?:'accepted'|'needs_review'|null;review_reason?:'review_failed'|'invalid_output'|null;segment_ids:number[]}
export type OptimizationResult={run_id:string;complete:boolean;model:string;rewrite_mode?:RewriteMode|null;needs_review_units?:number;paragraphs:OptimizationParagraph[];scenes:(Omit<VideoScene,'paragraphs'>&{paragraphs:OptimizationParagraph[]})[]}
export type OptimizationConfiguration={base_url:string;use_proxy:boolean;model:string;rewrite_mode?:RewriteMode|null;enabled:boolean;context_length?:number;model_context_length?:number;verified_at?:string;verification_token?:string}
export const optimizationSettingsPath='/api/v1/settings/transcript-optimization'
export const optimizationPath=(id:string)=>`/api/v1/jobs/${id}/transcript-optimization`
