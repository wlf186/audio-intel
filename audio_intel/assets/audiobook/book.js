(() => {
  const el = id => document.getElementById(id)
  const audio = document.querySelector('audio')
  const sections = [...document.querySelectorAll('main section')]
  const links = [...document.querySelectorAll('nav .section-link')]
  const key = 'audio-intel:book:1:' + document.body.dataset.book +
    (document.body.dataset.single === '1' ? ':' + sections[0].dataset.id : '')
  let objectUrl
  let index = -1, revision = 0, current, picked, returning, following = true, lastSave = 0
  let cues = [], resume
  try { resume = JSON.parse(localStorage.getItem(key)) } catch {}
  const clock = t => Math.floor(t / 60) + ':' + String(Math.floor(t % 60)).padStart(2,'0')
  const snapshot = () => ({id:sections[index]?.dataset.id, time:audio.currentTime, speed:Number(el('speed').value)})
  function save() {
    if (index < 0 || !audio.readyState) return
    const state = snapshot()
    try { localStorage.setItem(key,JSON.stringify(state)) } catch {}
    lastSave = Date.now()
  }
  const message = value => { el('status').textContent = value }
  function reveal(node) {
    const box = node.getBoundingClientRect()
    if (box.top < 50 || box.bottom > document.querySelector('footer').getBoundingClientRect().top)
      node.scrollIntoView({block:'center'})
  }
  const starts = sections.map(section => [...new Set(
    Array.from(section.querySelectorAll('[data-at]'), node => Number(node.dataset.at))
  )])
  let visibleChapter = -1, pinned = false, pendingTime = 0
  function markChapter(i) {
    if (visibleChapter === i) return
    links[visibleChapter]?.removeAttribute('aria-current')
    links[i].setAttribute('aria-current','location')
    visibleChapter = i
  }
  function cueIndex(times,t) {
    let low=0, high=times.length
    while(low<high){
      const mid=Math.floor((low+high)/2)
      if(times[mid]<=t)low=mid+1
      else high=mid
    }
    return low-1
  }
  function sync() {
    if(index < 0) return
    const t = audio.readyState ? audio.currentTime : pendingTime
    const times = starts[index]
    const pos = cueIndex(times,t)
    el('prev').disabled = index===0 && pos<=0
    el('next').disabled = index===sections.length-1 && pos>=times.length-1
    const hit=cues[pos]
    const active=hit && t<Number(hit.dataset.end) ? hit.dataset.at : null
    if(current!==active){
      sections[index].querySelectorAll('.active').forEach(n=>n.classList.remove('active'))
      current=active
      if(active!==null){
        const matches=sections[index].querySelectorAll('[data-at="'+active+'"]')
        matches.forEach(n=>n.classList.add('active'))
        if(following) reveal(matches[0])
      }
    }
    if(Number.isFinite(audio.duration)){
      el('seek').max=audio.duration
      el('seek').value=t
      document.querySelector('time').textContent='剩余 '+clock(Math.max(0,audio.duration-t)/audio.playbackRate)
    }
    if(Date.now()-lastSave>5000) save()
  }
  async function play(){
    try { await audio.play() } catch(e){ if(e.name!=='AbortError') message('播放未开始，请点播放重试。') }
  }
  function open(i,time=0,start=true,follow=true){
    if(i<0 || i>=sections.length)return
    following=follow
    el('follow').hidden=follow
    if(follow){pinned=true;markChapter(i)}
    pendingTime=time
    if(i===index){if(audio.readyState)audio.currentTime=time;if(start)void play();sync();return}
    save()
    const token=++revision
    audio.onpause=null
    audio.pause()
    document.querySelectorAll('.active,.listening').forEach(n=>n.classList.remove('active','listening'))
    index=i
    current=undefined
    cues=[...sections[i].querySelectorAll('[data-at]')].filter((node,j,nodes)=>
      j===0 || node.dataset.at!==nodes[j-1].dataset.at)
    el('prev').textContent=cues.length?'上一片段':'上一章'
    el('next').textContent=cues.length?'下一片段':'下一章'
    el('restart').textContent=cues.length?'重播本片段':'重播本章'
    el('restart').disabled=false
    links[i].classList.add('listening')
    message(sections[i].dataset.title+(cues.length?'':' · 本段支持按时间定位'))
    const embedded=el('audio'+i)
    if(objectUrl) URL.revokeObjectURL(objectUrl)
    objectUrl=null
    if(embedded){
      const encoded=embedded.textContent, parts=[]
      for(let pos=0;pos<encoded.length;pos+=262144){
        const binary=atob(encoded.slice(pos,pos+262144))
        parts.push(Uint8Array.from(binary,c=>c.charCodeAt(0)))
      }
      objectUrl=URL.createObjectURL(new Blob(parts,{type:'audio/mpeg'}))
    }
    audio.src=objectUrl||sections[i].dataset.src
    audio.playbackRate=Number(el('speed').value)
    audio.onloadedmetadata=()=>{
      if(token!==revision)return
      audio.currentTime=Math.min(pendingTime,Number.isFinite(audio.duration)?audio.duration:pendingTime)
      sync()
    }
    audio.onpause=()=>{el('play').textContent='继续';save()}
    sync()
    if(start)void play()
  }
  function restore(state){
    const i=sections.findIndex(s=>s.dataset.id===state?.id)
    if(i>=0 && Number.isFinite(state.time) && state.time>=0){open(i,state.time);return true}
    return false
  }
  function jump(i,t){if(index>=0){returning=snapshot();el('undo').disabled=false}open(i,t)}
  if(resume && sections.some(s=>s.dataset.id===resume.id)){
    el('play').textContent='继续 '+clock(resume.time)
    message(sections.find(s=>s.dataset.id===resume.id).dataset.title)
    if([...el('speed').options].some(o=>Number(o.value)===resume.speed))el('speed').value=resume.speed
  }
  el('play').onclick=()=>{if(index<0){if(!restore(resume))open(0)}else if(audio.paused)void play();else audio.pause()}
  audio.onplay=()=>{el('play').textContent='暂停'}
  audio.ontimeupdate=audio.onseeked=sync
  audio.onended=()=>{if(index+1<sections.length)open(index+1,0,true,following);else{el('play').textContent='重听';save()}}
  audio.onerror=()=>message('音频无法播放，请重新打开文件或下载当前分段。')
  el('back').onclick=()=>{if(index>=0)audio.currentTime=Math.max(0,audio.currentTime-10)}
  el('seek').oninput=()=>{if(index>=0)audio.currentTime=Number(el('seek').value)}
  el('speed').onchange=()=>{audio.playbackRate=Number(el('speed').value);sync();save()}
  function move(direction){
    if(index<0)return
    const times=starts[index]
    const pos=cueIndex(times,audio.readyState?audio.currentTime:pendingTime)
    if(direction===0){open(index,times[Math.max(0,pos)]??0);return}
    const target=times[pos+direction]
    if(target!==undefined){open(index,target);return}
    const adjacent=index+direction
    if(adjacent<0 || adjacent>=sections.length)return
    open(adjacent,(direction<0?starts[adjacent].at(-1):starts[adjacent][0])??0)
  }
  el('prev').onclick=()=>move(-1)
  el('restart').onclick=()=>move(0)
  el('next').onclick=()=>move(1)
  el('follow').onclick=()=>{following=true;pinned=true;markChapter(index);el('follow').hidden=true;(sections[index]?.querySelector('.active')||sections[index])?.scrollIntoView({block:'center'})}
  el('undo').onclick=()=>{restore(returning);el('undo').disabled=true}
  el('more').onclick=()=>{el('extras').hidden=!el('extras').hidden;el('more').setAttribute('aria-expanded',String(!el('extras').hidden));save()}
  function menu(show){el('toc').classList.toggle('open',show);el('menu').setAttribute('aria-expanded',String(show));if(show)(el('toc').querySelector('[aria-current]')||links[0]).focus();else el('menu').focus()}
  el('menu').onclick=()=>menu(!el('toc').classList.contains('open'))
  document.onkeydown=e=>{if(e.key==='Escape'){if(el('toc').classList.contains('open'))menu(false);if(!el('extras').hidden)el('more').click()}}
  function browse(){following=false;el('follow').hidden=index<0}
  function manualScroll(e){
    if(e.target.closest?.('nav,footer'))return
    pinned=false
    browse()
  }
  for(const evt of ['wheel','touchmove'])addEventListener(evt,manualScroll,{passive:true})
  addEventListener('pointerdown',e=>{if(e.clientX>=document.documentElement.clientWidth)manualScroll(e)})
  addEventListener('keydown',e=>{if(['PageUp','PageDown','ArrowUp','ArrowDown','Home','End',' '].includes(e.key))manualScroll(e)})
  function selectAnchor(hash){
    const target=document.getElementById(hash.slice(1))
    const i=sections.indexOf(target?.closest('section'))
    if(i<0)return
    pinned=true
    browse()
    markChapter(i)
  }
  document.querySelectorAll('nav a').forEach(link=>link.onclick=()=>{
    selectAnchor(link.hash)
    if(el('toc').classList.contains('open'))menu(false)
  })
  addEventListener('hashchange',()=>selectAnchor(location.hash))
  function trackScroll(){
    if(pinned)return
    const line=document.querySelector('header').getBoundingClientRect().bottom+24
    let low=0, high=sections.length
    while(low<high){
      const mid=Math.floor((low+high)/2)
      if(sections[mid].getBoundingClientRect().top<=line)low=mid+1
      else high=mid
    }
    const atEnd=scrollY>0 && Math.ceil(scrollY+innerHeight)>=document.documentElement.scrollHeight
    markChapter(atEnd?sections.length-1:Math.max(0,low-1))
  }
  addEventListener('scroll',trackScroll,{passive:true})
  addEventListener('resize',trackScroll)
  trackScroll()
  if(location.hash)selectAnchor(location.hash)
  const wideNotes=matchMedia('(min-width:1280px)')
  function layoutNotes(){document.querySelectorAll('details.note').forEach(note=>{note.open=wideNotes.matches})}
  layoutNotes()
  wideNotes.addEventListener('change',layoutNotes)
  document.addEventListener('click',e=>{
    if(el('toc').classList.contains('open') && !e.target.closest('#toc,#menu'))menu(false)
  })
  sections.forEach((s,i)=>{
    const heading=s.querySelector('article > :is(h1,h2):first-child')
    if(heading)heading.after(s.querySelector('.chapter'))
    const action=s.querySelector('.chapter')
    action.onclick=()=>jump(i,Number(s.querySelector('.picked')?.dataset.at||0))
    s.querySelector('article').onclick=e=>{
      const n=e.target.closest('[data-at]')
      if(!n || !getSelection().isCollapsed || e.target.closest('button'))return
      picked?.classList.remove('picked')
      picked=n;n.classList.add('picked')
      action.textContent='从这里听'
      n.after(action)
      action.focus({preventScroll:true})
    }
    s.onkeydown=e=>{if(e.target.hasAttribute('data-at') && e.key==='Enter'){e.preventDefault();jump(i,Number(e.target.dataset.at))}}
  })
  addEventListener('pagehide',save)
  document.addEventListener('visibilitychange',()=>{if(document.hidden)save()})
})()
