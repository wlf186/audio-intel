(() => {
  const get = id => document.getElementById(id)
  const article = document.querySelector('article')
  const nav = get('directory')
  const menu = get('menu')
  const query = get('query')
  document.body.classList.add('enhanced')
  document.querySelector('.tools').hidden = false
  const narrow = matchMedia('(max-width:959px)')
  article.querySelectorAll('details:not(.source)').forEach(node=>node.open=false)
  if (narrow.matches) article.querySelectorAll('.source').forEach(node => node.open = false)
  function drawer(open) {
    nav.classList.toggle('open',open)
    menu.setAttribute('aria-expanded',String(open))
    document.querySelector('main').inert=open
    get('closemenu').hidden=!open
    if (open) {
      const active=nav.querySelector('[aria-current]') || nav.querySelector('a')
      if(active)chapterFor.get(active.hash.slice(1))?.setOpen(true)
      active?.focus({preventScroll:true})
      if(active)nav.scrollTop+=active.getBoundingClientRect().top-nav.getBoundingClientRect().top-nav.clientHeight/3
    }
    else menu.focus()
  }
  get('closemenu').onclick=()=>drawer(false)
  let screenNarrow=narrow.matches
  window.addEventListener('resize',()=>{
    if(matchMedia('print').matches || screenNarrow===narrow.matches)return
    screenNarrow=narrow.matches
    article.querySelectorAll('.source').forEach(node=>node.open=!screenNarrow)
  })
  matchMedia('(min-width:1200px)').addEventListener('change',()=>{if(nav.classList.contains('open'))drawer(false)})
  const options=get('options')
  function showOptions(open) {
    options.hidden=!open
    get('more').setAttribute('aria-expanded',String(open))
  }
  get('more').onclick=()=>showOptions(options.hidden)
  document.addEventListener('click',event=>{
    if(!options.contains(event.target) && !get('more').contains(event.target))showOptions(false)
    if(nav.classList.contains('open') && !nav.contains(event.target) && !menu.contains(event.target))drawer(false)
  })
  const box=get('lightbox')
  const zoom=get('zoom')
  function imageSize(actual){
    box.classList.toggle('actual',actual)
    zoom.textContent=actual?zoom.dataset.fit:zoom.dataset.actual
  }
  zoom.onclick=()=>imageSize(!box.classList.contains('actual'))
  for(const image of article.querySelectorAll('figure img')){
    const button=document.createElement('button')
    button.className='frame-button'
    button.setAttribute('aria-label',document.documentElement.lang==='en-US'?'Enlarge frame':'放大画面')
    image.replaceWith(button)
    button.append(image)
    button.onclick=()=>{
      const large=box.querySelector('img')
      large.src=image.src
      large.alt=image.closest('figure').querySelector('figcaption').textContent
      imageSize(narrow.matches && image.naturalWidth>window.innerWidth)
      box.showModal()
      box.querySelector('.frame-view').scrollTo(0,0)
    }
  }
  get('frame-close').onclick=()=>box.close()
  box.onclick=event=>{if(event.target===box)box.close()}
  box.onclose=()=>box.querySelector('img').removeAttribute('src')
  get('find').onclick=()=>{
    const open=get('searchbox').classList.toggle('open')
    get('find').setAttribute('aria-expanded',String(open))
    showOptions(false)
    if(open)query.focus()
    else {query.value='';search();get('find').focus()}
  }
  menu.onclick = () => drawer(!nav.classList.contains('open'))
  document.addEventListener('keydown',e => {
    if(e.key!=='Escape' || box.open)return
    if(nav.classList.contains('open'))drawer(false)
    else if(!options.hidden){showOptions(false);get('more').focus()}
    else if(get('searchbox').classList.contains('open'))get('find').click()
  })
  let directoryJumpY
  nav.onclick = e => {
    const link = e.target.closest('a')
    if (!link) return
    if (nav.classList.contains('open')) drawer(false)
    const target=get(link.hash.slice(1))
    if(target){
      target.tabIndex=-1
      target.focus({preventScroll:true})
      target.scrollIntoView()
      directoryJumpY=window.scrollY
    }
    selectChapter(link.hash.slice(1))
  }
  const links=new Map([...nav.querySelectorAll('a')].map(a=>[a.hash.slice(1),a]))
  const chapterFor=new Map()
  let chapter
  for(const link of nav.dataset.hierarchical==='1'?links.values():[]){
    if(link.classList.contains('level-2')){
      const wrapper=document.createElement('div')
      wrapper.className='chapter-links'
      link.replaceWith(wrapper)
      const toggle=document.createElement('button'),children=document.createElement('div')
      const setOpen=open=>{
        children.hidden=!open
        toggle.textContent=open?'⌄':'›'
        toggle.setAttribute('aria-expanded',String(open))
      }
      toggle.hidden=true
      toggle.setAttribute('aria-label',link.textContent)
      children.id='children-'+link.hash.slice(1)
      toggle.setAttribute('aria-controls',children.id)
      const group={toggle,children,setOpen,keepOpen:false}
      toggle.onclick=()=>{
        group.keepOpen=children.hidden
        setOpen(group.keepOpen)
      }
      wrapper.append(link,toggle,children)
      chapter=group
      setOpen(false)
    }else if(chapter){
      chapter.toggle.hidden=false
      chapter.children.append(link)
    }
    if(chapter)chapterFor.set(link.hash.slice(1),chapter)
  }
  let activeChapter
  function selectChapter(id){
    nav.querySelector('[aria-current]')?.removeAttribute('aria-current')
    const current=chapterFor.get(id)
    if(current!==activeChapter){
      if(activeChapter && !activeChapter.keepOpen)activeChapter.setOpen(false)
      current?.setOpen(true)
      activeChapter=current
    }
    links.get(id)?.setAttribute('aria-current','location')
  }
  if(links.size)selectChapter(links.keys().next().value)
  const chapters=new IntersectionObserver(entries=>{
    const active=entries.find(entry=>entry.isIntersecting)
    if(active && directoryJumpY!==window.scrollY && !nav.classList.contains('open'))selectChapter(active.target.id)
  },{rootMargin:'-10% 0px -60% 0px'})
  for(const id of links.keys()){const target=get(id);if(target)chapters.observe(target)}
  const brief = get('brief')
  const briefLabel=brief?.textContent
  let returnY=0
  function summaryMode(on){
    document.body.classList.toggle('brief',on)
    brief.setAttribute('aria-pressed',String(on))
    brief.textContent=on?brief.dataset.full:briefLabel
  }
  if(brief)brief.onclick=()=>{
    if(document.body.classList.contains('brief')){
      summaryMode(false)
      window.scrollTo(0,returnY)
    }else{
      returnY=window.scrollY
      const notes=[...article.querySelectorAll('.margin-note')]
      const owner=notes.map(note=>note.parentElement).find(row=>row.getBoundingClientRect().bottom>64)
      summaryMode(true)
      ;(owner || notes.at(-1)?.parentElement)?.scrollIntoView({block:'start'})
    }
  }
  article.onclick = e => {
    if(e.target.closest('.margin-note a,.summary-jump') && document.body.classList.contains('brief'))summaryMode(false)
    if (e.target.matches('.timing')) e.target.setAttribute('aria-expanded',String(e.target.closest('.body').classList.toggle('show-timing')))
  }
  const searchable = [...article.querySelectorAll('p,h2,h3,header strong,figcaption')].filter(node=>!node.closest('.timestamps'))
  const visible = new Set()
  let results = [], total = 0, index = -1, pattern
  const restore = new Map()
  const highlights = window.CSS?.highlights
  function reveal(node) {
    for(let p=node;p && p!==article;p=p.parentElement){
      if(p.matches('.margin-note,figure') && !p.getClientRects().length){
        restore.set(p,()=>p.classList.remove('found'));p.classList.add('found')
      }
      if(p.matches('details:not([open])')){restore.set(p,()=>p.open=false);p.open=true}
      if(p.matches('.speech:not(.expanded)') && document.body.classList.contains('brief')){
        restore.set(p,()=>p.classList.remove('expanded'));p.classList.add('expanded')
      }
    }
  }
  function rangeFor(node, start, end) {
    const walker=document.createTreeWalker(node,NodeFilter.SHOW_TEXT)
    const range=new Range()
    let offset=0
    while(walker.nextNode()){
      const text=walker.currentNode
      const limit=offset+text.length
      if(offset<=start && start<limit)range.setStart(text,start-offset)
      if(offset<end && end<=limit){range.setEnd(text,end-offset);return range}
      offset=limit
    }
  }
  function paint() {
    if(!highlights || !pattern)return
    const marks=new Highlight()
    for(const node of visible){
      let drawn=0
      for(const match of node.textContent.matchAll(pattern)){
        marks.add(rangeFor(node,match.index,match.index+match[0].length))
        if(++drawn>=500)break
      }
    }
    highlights.set('matches',marks)
  }
  const observer=new IntersectionObserver(entries=>{
    for(const entry of entries){
      if(entry.isIntersecting)visible.add(entry.target)
      else visible.delete(entry.target)
    }
    paint()
  })
  searchable.forEach(node=>observer.observe(node))
  function jump(step){
    if(!total)return
    index=(index+step+total)%total
    const item=results.find(row=>row.first+row.count>index)
    reveal(item.node)
    let at=item.first
    for(const match of item.node.textContent.matchAll(pattern)){
      if(at++!==index)continue
      const range=rangeFor(item.node,match.index,match.index+match[0].length)
      if(highlights)highlights.set('current',new Highlight(range))
      else{const selection=getSelection();selection.removeAllRanges();selection.addRange(range)}
      const rect=range.getBoundingClientRect()
      window.scrollBy(0,rect.top-window.innerHeight/2)
      break
    }
    get('count').value=(index+1)+' / '+total
  }
  function search(){
    restore.forEach(fn=>fn());restore.clear()
    results=[];total=0;index=-1;pattern=null
    highlights?.delete('current');highlights?.delete('matches')
    const needle=query.value.trim()
    if(needle){
      pattern=new RegExp(Array.from(needle,c=>'\\u{'+c.codePointAt(0).toString(16)+'}').join(''),'giu')
      for(const node of searchable){
        let count=0
        for(const _ of node.textContent.matchAll(pattern))count++
        if(count){results.push({node,count,first:total});total+=count}
      }
      paint()
    }
    get('prev').hidden=get('next').hidden=!total
    get('count').value=needle?String(total):''
    jump(1)
  }
  query.oninput=search
  get('prev').onclick=()=>jump(-1)
  get('next').onclick=()=>jump(1)
  query.onkeydown=e=>{if(e.key==='Enter')jump(e.shiftKey?-1:1)}
  let closed=[],scroll=0
  window.addEventListener('beforeprint',()=>{
    scroll=window.scrollY
    closed=[...article.querySelectorAll('details:not([open])')]
    closed.forEach(node=>node.open=true)
    article.querySelectorAll('img').forEach(img=>img.loading='eager')
  })
  window.addEventListener('afterprint',()=>{closed.forEach(node=>node.open=false);window.scrollTo(0,scroll)})
  get('print').onclick=()=>{showOptions(false);get('more').focus();window.print()}
})()
