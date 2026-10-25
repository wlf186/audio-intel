import {test as base} from '@playwright/test'
import type {Page} from '@playwright/test'
import type {Capabilities,ComputeCapability,DeploymentCapability,Health} from '../src/lib/types'

export {expect} from '@playwright/test'
export type {Page,Route} from '@playwright/test'

type CapabilitySnapshot=Capabilities&{
 deployment:DeploymentCapability
 asr:Capabilities['asr']&{compute_devices:ComputeCapability[]}
 tts:NonNullable<Capabilities['tts']>&{compute_devices:ComputeCapability[]}
 events:NonNullable<Capabilities['events']>
}
type SnapshotReaders={capabilities:()=>Promise<CapabilitySnapshot>;system:()=>Promise<Health>}
type SnapshotOptions={page:Page;baseURL?:string;extraHTTPHeaders?:Record<string,string>}
type WorkerReaders={capabilities:(options:SnapshotOptions)=>Promise<CapabilitySnapshot>;system:(options:SnapshotOptions)=>Promise<Health>}
const pageReaders=new WeakMap<Page,SnapshotReaders>()

type ResponseGate={arrived:Promise<void>;hold:()=>Promise<void>;release:()=>void}
type ResponseGateFactory=(()=>ResponseGate)&{releaseAll:()=>void}

// Only fixture data is cached. Live API/browser contract tests remain unmocked.
export const test=base.extend<{snapshotAccess:void;responseGate:ResponseGateFactory},{apiSnapshots:WorkerReaders}>({
 responseGate:async({},use)=>{
  const gates:ResponseGate[]=[]
  const create=()=>{
   let signalArrival:()=>void=()=>undefined
   let release:()=>void=()=>undefined
   const arrived=new Promise<void>(resolve=>{signalArrival=resolve})
   const pending=new Promise<void>(resolve=>{release=resolve})
   const gate={arrived,hold:()=>{signalArrival();return pending},release}
   gates.push(gate)
   return gate
  }
  const releaseAll=()=>{for(const gate of gates)gate.release()}
  try{await use(Object.assign(create,{releaseAll}))}finally{releaseAll()}
 },
 apiSnapshots:[async({playwright},use)=>{
  const pending=new Map<string,Promise<unknown>>()
  const read=async<T>(path:string,options:SnapshotOptions):Promise<T>=>{
   const url=new URL(path,options.baseURL).href
   let response=pending.get(url)
   if(!response){
    response=(async()=>{
     // Bound setup resources; later navigations use the cached snapshot.
     const context=await playwright.request.newContext({extraHTTPHeaders:options.extraHTTPHeaders,storageState:await options.page.context().storageState()})
     try{
      const result=await context.get(url,{maxRetries:0})
      if(!result.ok())throw new Error(`Fixture snapshot ${path}: HTTP ${result.status()}`)
      const body=await result.json()
      if(path.endsWith('/capabilities')){
       if(!body.deployment||!Array.isArray(body.asr?.models)||!Array.isArray(body.tts?.model_capabilities)||!body.limits||!body.events)throw new Error('Incomplete capabilities fixture snapshot')
      }else if(!body.version||!body.hardware||!Array.isArray(body.workers))throw new Error('Incomplete system fixture snapshot')
      return body
     }finally{await context.dispose()}
    })()
    pending.set(url,response)
   }
   // Each handler receives its own copy, including nested model/device arrays.
   return structuredClone(await response) as T
  }
  await use({capabilities:options=>read<CapabilitySnapshot>('/api/v1/capabilities',options),system:options=>read<Health>('/api/v1/system',options)})
 },{scope:'worker'}],
 snapshotAccess:[async({page,apiSnapshots,baseURL,extraHTTPHeaders},use)=>{
  const options={page,baseURL,extraHTTPHeaders}
  pageReaders.set(page,{capabilities:()=>apiSnapshots.capabilities(options),system:()=>apiSnapshots.system(options)})
  try{await use()}finally{pageReaders.delete(page)}
 },{auto:true}],
})

// Run before consumers' afterEach hooks that wait for route handlers to finish.
test.afterEach(async({responseGate})=>{responseGate.releaseAll()})

function snapshots(page:Page){
 const readers=pageReaders.get(page)
 if(!readers)throw new Error('Import test from ./fixtures when using API fixture snapshots')
 return readers
}
// Legacy speech fixtures opt out of the optional Ollama workflow explicitly.
// Tests of live API responses still use the real endpoint, without this helper.
export async function capabilitiesFor(page:Page,options:{transcriptOptimization?:boolean}={}){
 const value=await snapshots(page).capabilities()
 if(value.asr.transcript_optimization&&!options.transcriptOptimization)value.asr.transcript_optimization.supported=false
 return value
}
export function systemFor(page:Page){return snapshots(page).system()}
