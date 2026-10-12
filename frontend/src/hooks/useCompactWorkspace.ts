import {useEffect,useState} from 'react'

export function useCompactWorkspace(){
 const [compact,setCompact]=useState(()=>matchMedia('(max-width:1023px)').matches)
 useEffect(()=>{
  const media=matchMedia('(max-width:1023px)')
  const update=()=>setCompact(media.matches)
  media.addEventListener('change',update)
  return()=>media.removeEventListener('change',update)
 },[])
 return compact
}
