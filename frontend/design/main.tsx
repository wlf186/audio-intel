import {useState} from 'react'
import {createRoot} from 'react-dom/client'
import {ActionPopover} from '../src/components/ActionPopover'
import {ConfirmDialog} from '../src/components/ConfirmDialog'
import {SettingToggleRow} from '../src/components/SettingToggleRow'
import {ResourceStatePanel} from '../src/components/ResourceStatePanel'
import {initializeI18n} from '../src/i18n'
import '../src/styles.css'
import '../src/workspace.css'
import './preview.css'

function Preview(){
 const [enabled,setEnabled]=useState(true)
 const [confirm,setConfirm]=useState(false)
 const [failed,setFailed]=useState(true)
 const [width,setWidth]=useState('1440')
 return <main className="design-preview">
  <h1>工作台设计样板</h1>
  <section><h2>控件与状态</h2>
   <SettingToggleRow id="preview-switch" label="表达优化" description="处理完成后生成优化稿" checked={enabled} onChange={setEnabled}/>
   <div className="preview-actions"><button className="primary" disabled={!enabled} onClick={()=>setConfirm(true)}>开始处理</button>
    <ActionPopover label="更多操作" closeOnAction><button className="button" onClick={()=>setFailed(true)}>模拟加载失败</button></ActionPopover></div>
   <ResourceStatePanel state={failed?'error':'ready'} loadingLabel="正在加载" errorLabel="列表暂时不可用" retry={()=>setFailed(false)}/>
   {confirm&&<ConfirmDialog title="开始处理？" description="预览本次操作的确认状态。" confirmLabel="确认" onConfirm={()=>setConfirm(false)} onClose={()=>setConfirm(false)}/>}
  </section>
  <section><h2>页面样板</h2><label>视口宽度 <select value={width} onChange={event=>setWidth(event.target.value)}>{['390','1024','1440'].map(n=><option key={n}>{n}</option>)}</select></label>
   {Object.entries({reader:'转写阅读',editor:'合成编辑',library:'声纹管理'}).map(([key,title])=><figure key={key}><figcaption>{title}</figcaption><img src={`/references/${key}-${width}-linux.png`} alt={title}/></figure>)}
  </section>
 </main>
}
void initializeI18n().then(()=>createRoot(document.getElementById('root')!).render(<Preview/>))
