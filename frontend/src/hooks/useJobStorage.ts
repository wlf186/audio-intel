import {useEffect,useState} from 'react'
import {HttpError,request} from '../lib/api'
import type {JobSummary,JobStorageItem} from '../lib/types'

export function useJobStorage(jobs:JobSummary[]){
 const signature=jobs.map(job=>`${job.id}:${job.state}`).join(',')
 const ids=jobs.map(job=>job.id).join(',')
 const active=jobs.some(job=>job.state==='running'||job.state==='queued')
 const [resource,setResource]=useState<{key:string;items:Record<string,JobStorageItem>;error?:string}>({key:'',items:{}})
 const [refresh,setRefresh]=useState(0)
 const [blockedUntil,setBlockedUntil]=useState(0)
 const [waiting,setWaiting]=useState(false)
 useEffect(()=>{
  if(!waiting)return
  const timer=setInterval(()=>{if(Date.now()>=blockedUntil)setWaiting(false)},250)
  return()=>clearInterval(timer)
 },[blockedUntil,waiting])
 useEffect(()=>{
  if(!ids)return
  const controller=new AbortController()
  setResource({key:signature,items:{}})
  void request<{items:JobStorageItem[]}>('/api/v1/jobs/storage',{method:'POST',signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({job_ids:ids.split(','),refresh:refresh>0})}).then(value=>{if(!controller.signal.aborted)setResource({key:signature,items:Object.fromEntries(value.items.map(item=>[item.id,item]))})},cause=>{if(!controller.signal.aborted){setResource({key:signature,items:{},error:cause.message});if(cause instanceof HttpError&&cause.retryAfter){setBlockedUntil(Date.now()+cause.retryAfter*1000);setWaiting(true)}}})
  return()=>controller.abort()
 },[signature,ids,refresh])
 useEffect(()=>{if(!active)return;const timer=setInterval(()=>setRefresh(value=>value+1),5000);return()=>clearInterval(timer)},[active])
 return {items:resource.key===signature?resource.items:{},error:resource.key===signature?resource.error:undefined,retry:()=>setRefresh(value=>value+1),waiting}
}
