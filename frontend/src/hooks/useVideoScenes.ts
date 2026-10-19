import {useEffect,useState} from 'react'
import {request} from '../lib/api'
import {checkpointWorkspace} from '../lib/workspaceSession'
import type {JobResult,VideoScenes} from '../lib/types'

export type VideoScenesResource={data?:VideoScenes;loading:boolean;error:string;reload:()=>void}
export function useVideoScenes(jobId:string|undefined,result:JobResult|undefined):VideoScenesResource{
 const [resource,setResource]=useState<{id:string;data?:VideoScenes;loading:boolean;error:string}>({id:'',loading:false,error:''})
 const [revision,setRevision]=useState(0)
 useEffect(()=>{
  if(!jobId||!result?.video)return
  const controller=new AbortController()
  checkpointWorkspace()
  setResource(current=>({id:jobId,data:current.id===jobId?current.data:undefined,loading:true,error:''}))
  void request<VideoScenes>(`/api/v1/jobs/${jobId}/video-scenes`,{signal:controller.signal}).then(data=>{if(!controller.signal.aborted)setResource({id:jobId,data,loading:false,error:''})},cause=>{if(!controller.signal.aborted)setResource(current=>({id:jobId,data:current.id===jobId?current.data:undefined,loading:false,error:cause.message}))})
  return()=>controller.abort()
 },[jobId,result,revision])
 return {...(resource.id===jobId?resource:{loading:Boolean(result?.video),error:''}),reload:()=>setRevision(value=>value+1)}
}
