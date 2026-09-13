import type {DocumentPreview} from './types'

const structureBases=new Set(['heading','toc','spine','worksheet','slide'])
const lengthBases=new Set(['paragraph','sentence','newline','word','forced','remainder'])

export function sectionKind(basis:string):'structure'|'length'|'unknown'{
 return structureBases.has(basis)?'structure':lengthBases.has(basis)?'length':'unknown'
}

export function segmentationSummary(preview:DocumentPreview):'structure'|'mixed'|'fallback'|'length'|'unknown'{
 if(preview.segmentation_mode==='length')return 'length'
 const kinds=new Set(preview.sections.map(section=>sectionKind(section.basis)))
 if(kinds.has('unknown')||!kinds.size)return 'unknown'
 return kinds.has('structure')?(kinds.has('length')?'mixed':'structure'):'fallback'
}
