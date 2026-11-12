import type {VideoScene} from './types'

/** Numbers are based on the complete snapshot, never the filtered reading list. */
export function pictureNumbers(scenes:readonly Pick<VideoScene,'id'|'image'>[]){
 const numbers=new Map<string,number>()
 for(const scene of scenes)if(scene.image)numbers.set(scene.id,numbers.size+1)
 return numbers
}
export function hasReadingContent(scene:{image:string|null;paragraphs?:readonly unknown[];title_summary?:{title:string|null}|null}){
 return Boolean(scene.image||scene.paragraphs?.length||scene.title_summary?.title)
}
export function relatedTextStart(scene:{start:number;end:number},paragraphs:readonly {start:number;end:number}[]){
 return paragraphs.find(p=>p.start<scene.end&&p.end>scene.start)?.start
}
