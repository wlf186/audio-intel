import type {TranscriptExportFormat} from './types'

const mediaSuffix=/\.(wav|mp3|flac|m4a|aac|ogg|opus|aiff|mp4|m4v|mov|mkv|webm|avi|mpeg|mpg|ts)$/i
export function transcriptFilename(displayName:string,jobId:string,format:TranscriptExportFormat){
 const leaf=displayName.split(/[/\\]/).pop()||''
 const base=leaf.lastIndexOf('.')>0?leaf.replace(mediaSuffix,''):leaf
 const stem=Array.from(base.replace(/[^\p{L}\p{N}_.-]+/gu,'_').replace(/^[._]+|[._]+$/g,'')).slice(0,180).join('')
 return `${stem||`transcript-${jobId}`}.${format}`
}

export function transcriptDownloadName(response:Response,fallback:string){
 const disposition=response.headers.get('Content-Disposition')||''
 const encoded=/filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1]
 let name=/filename="([^"]+)"/i.exec(disposition)?.[1]||fallback
 if(encoded){try{name=decodeURIComponent(encoded)}catch{/* Keep the safe plain-name fallback. */}}
 const clean=name.split(/[/\\]/).pop()?.replace(/[\x00-\x1f\x7f]/g,'')
 return clean&&clean!=='.'&&clean!=='..'?clean:fallback
}
