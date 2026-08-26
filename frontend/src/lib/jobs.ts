import type {Job} from './types'

export const workspaceJobLimit=5

export function newestJobsFirst(jobs:readonly Job[]):Job[]{
 return [...jobs].sort((left,right)=>{
  const leftTime=Date.parse(left.created_at)
  const rightTime=Date.parse(right.created_at)
  return Number.isFinite(leftTime)&&Number.isFinite(rightTime)?rightTime-leftTime:0
 })
}

export function visibleWorkspaceJobs(jobs:readonly Job[],selectedJobId?:string):Job[]{
 const recent=jobs.slice(0,workspaceJobLimit)
 if(!selectedJobId||recent.some(job=>job.id===selectedJobId))return recent
 const selected=jobs.find(job=>job.id===selectedJobId)
 return selected?[...recent,selected]:recent
}
