import type {KeyboardEvent} from 'react'

export function handleTabKeys(event:KeyboardEvent<HTMLElement>){
 if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return
 const tabs=[...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]:not(:disabled)')]
 if(!tabs.length)return
 const current=Math.max(0,tabs.indexOf(document.activeElement as HTMLButtonElement))
 const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:event.key==='ArrowRight'?(current+1)%tabs.length:(current-1+tabs.length)%tabs.length
 event.preventDefault()
 tabs[next].focus()
 tabs[next].click()
}
