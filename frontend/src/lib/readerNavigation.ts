/** Half-open intervals; gaps reveal the next item and the endpoint reveals the last. */
export function readingIndex(items:readonly {start:number;end:number}[],time:number){
 const index=items.findIndex(item=>time>=item.start&&time<item.end)
 if(index>=0)return index
 const next=items.findIndex(item=>item.start>=time)
 return next>=0?next:items.length-1
}

export function revealReadingNode(root:HTMLElement,node:HTMLElement,center=false){
 const box=root.getBoundingClientRect(),target=node.getBoundingClientRect()
 root.scrollTo({top:root.scrollTop+target.top-box.top-(center?Math.max(0,(root.clientHeight-target.height)/2):0),behavior:'instant'})
 if(matchMedia('(max-width:1023px), (max-height:600px)').matches){
  const visible=node.getBoundingClientRect()
  if(visible.top<0||visible.bottom>innerHeight)node.scrollIntoView({block:center?'center':'start',behavior:'instant'})
 }
}
