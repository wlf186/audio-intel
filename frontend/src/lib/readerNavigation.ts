type Interval = {start:number;end:number}
// Transcript arrays are immutable snapshots; validate once per snapshot.
const ordered = new WeakMap<readonly Interval[],boolean>()
function isOrdered(items:readonly Interval[]){
 let valid=ordered.get(items)
 if(valid===undefined){valid=items.every((item,index)=>Number.isFinite(item.start)&&Number.isFinite(item.end)&&item.start<=item.end&&(!index||items[index-1].end<=item.start));ordered.set(items,valid)}
 return valid
}
export function activeIntervalIndex(items:readonly Interval[],time:number){
 if(!isOrdered(items)||!Number.isFinite(time))return items.findIndex(item=>time>=item.start&&time<item.end)
 let low=0,high=items.length
 while(low<high){const middle=(low+high)>>>1;if(items[middle].end<=time)low=middle+1;else high=middle}
 return low<items.length&&items[low].start<=time?low:-1
}
/** Half-open intervals; gaps reveal the next item and the endpoint reveals the last. */
export function readingIndex(items:readonly Interval[],time:number){
 const index=activeIntervalIndex(items,time)
 if(index>=0)return index
 if(!isOrdered(items)||!Number.isFinite(time)){const next=items.findIndex(item=>item.start>=time);return next>=0?next:items.length-1}
 let low=0,high=items.length
 while(low<high){const middle=(low+high)>>>1;if(items[middle].start<time)low=middle+1;else high=middle}
 return low<items.length?low:items.length-1
}

export function revealReadingNode(root:HTMLElement,node:HTMLElement,center=false){
 const box=root.getBoundingClientRect(),target=node.getBoundingClientRect()
 root.scrollTo({top:root.scrollTop+target.top-box.top-(center?Math.max(0,(root.clientHeight-target.height)/2):0),behavior:'instant'})
 if(matchMedia('(max-width:1023px), (max-height:600px)').matches){
  const visible=node.getBoundingClientRect()
  if(visible.top<0||visible.bottom>innerHeight)node.scrollIntoView({block:center?'center':'start',behavior:'instant'})
 }
}
