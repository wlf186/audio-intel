import {readFile,readdir} from 'node:fs/promises'
import {join,relative} from 'node:path'
import {fileURLToPath} from 'node:url'

const root=new URL('../',import.meta.url)
const localeDir=new URL('src/i18n/locales/',root)
const sourceDir=fileURLToPath(new URL('src/',root))
const rootPath=fileURLToPath(root)
const locales=['zh-CN','en-US']

function flatten(value,prefix='',result=new Map()){
 for(const [key,item] of Object.entries(value)){
  const path=prefix?`${prefix}.${key}`:key
  if(typeof item==='string')result.set(path,item)
  else if(item&&typeof item==='object'&&!Array.isArray(item))flatten(item,path,result)
  else throw new Error(`Invalid translation value at ${path}`)
 }
 return result
}

function placeholders(value){return [...value.matchAll(/{{\s*([^},\s]+)[^}]*}}/g)].map(match=>match[1]).sort().join(',')}

const maps=[]
for(const locale of locales){
 const parsed=JSON.parse(await readFile(new URL(`${locale}.json`,localeDir),'utf8'))
 const map=flatten(parsed)
 for(const [key,value] of map)if(!value.trim())throw new Error(`${locale}: empty translation at ${key}`)
 maps.push(map)
}
const allKeys=new Set(maps.flatMap(map=>[...map.keys()]))
for(const key of allKeys){
 for(let index=0;index<maps.length;index++)if(!maps[index].has(key))throw new Error(`${locales[index]}: missing key ${key}`)
 const signatures=maps.map(map=>placeholders(map.get(key)))
 if(new Set(signatures).size>1)throw new Error(`Placeholder mismatch at ${key}: ${signatures.join(' / ')}`)
}

async function sourceFiles(directory){
 const entries=await readdir(directory,{withFileTypes:true})
 const files=[]
 for(const entry of entries){
  const path=join(directory,entry.name)
  if(entry.isDirectory())files.push(...await sourceFiles(path))
  else if(/\.(ts|tsx)$/.test(entry.name)&&!path.includes(`${join('i18n','locales')}`))files.push(path)
 }
 return files
}
const violations=[]
for(const file of await sourceFiles(sourceDir)){
 const content=await readFile(file,'utf8')
 content.split(/\r?\n/).forEach((line,index)=>{if(/[\p{Script=Han}]/u.test(line))violations.push(`${relative(rootPath,file)}:${index+1}`)})
}
if(violations.length)throw new Error(`Untranslated Han text outside locale resources:\n${violations.join('\n')}`)
console.log(`i18n check passed: ${allKeys.size} keys across ${locales.join(', ')}`)
