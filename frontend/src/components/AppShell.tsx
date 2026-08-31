import {useEffect,useRef,useState,type ReactNode} from 'react'
import {AudioLines,BookA,BookOpen,Fingerprint,ListMusic,LogOut,Mic2,MonitorCog,ShieldCheck} from 'lucide-react'
import type {Health} from '../lib/types'
import {systemPhase} from '../lib/systemStatus'
import {BrandMark} from './BrandMark'
import {TlsCertificateModal} from './TlsCertificateHelp'

export type Page='asr'|'tts'|'hotwords'|'voiceprints'|'jobs'|'system'
const items:[Page,string,string,string,typeof AudioLines][]=[['asr','01','转写工作台','转写',AudioLines],['tts','02','语音合成','合成',Mic2],['hotwords','03','热词库','热词',BookA],['voiceprints','04','声纹库','声纹',Fingerprint],['jobs','05','任务记录','任务',ListMusic],['system','06','系统状态','系统',MonitorCog]]
type Tone='checking'|'ready'|'warning'|'error'|'offline'
function FooterStatus({label,value,tone,className='',detail,title}:{label:string;value:string;tone:Tone;className?:string;detail?:string;title?:string}){
 return <span className={`shell-status ${className}`} data-state={tone} title={title} aria-label={`${label} ${value}`}><i aria-hidden="true"/><span>{label}</span><b>{value}</b>{detail?<span className="status-detail">{detail}</span>:null}</span>
}
export function AppShell({page,setPage,children,health,systemError,connectionError,authRequired,onLogout,onRetry}:{page:Page;setPage:(p:Page)=>void;children:ReactNode;health?:Health;systemError:string;connectionError?:string;authRequired?:boolean;onLogout?:()=>void;onRetry?:()=>void}){
 const mainRef=useRef<HTMLElement>(null)
 const [showTlsHelp,setShowTlsHelp]=useState(false)
 useEffect(()=>{
  const label=items.find(([id])=>id===page)?.[2]||'工作台'
  document.title=`${label} · Sandevistan-Audio`
  const frame=requestAnimationFrame(()=>mainRef.current?.focus({preventScroll:true}))
  return()=>cancelAnimationFrame(frame)
 },[page])
 const phase=systemPhase(health,systemError)
 const mode=phase==='checking'?{tone:'checking' as const,full:'OFFLINE_MODE // CHECKING',compact:'检查中',label:'正在检查本地离线模式'}:phase==='error'?{tone:'error' as const,full:'LOCAL_CORE // DISCONNECTED',compact:'连接中断',label:'本地服务连接中断'}:health?.offline?{tone:'ready' as const,full:'OFFLINE_MODE // ACTIVE',compact:'本地可用',label:'本地服务已连接，离线推理模式已启用'}:{tone:'warning' as const,full:'OFFLINE_MODE // INACTIVE',compact:'离线未启用',label:'本地服务已连接，离线推理模式未启用'}
 const engine=(kind:string)=>phase==='checking'?{tone:'checking' as const,value:'CHECKING'}:phase==='error'?{tone:'error' as const,value:'UNKNOWN'}:health?.services.includes(kind)?{tone:'ready' as const,value:'READY'}:{tone:'offline' as const,value:'OFFLINE'}
 const asr=engine('asr'),tts=engine('tts')
 const dataLocal=phase==='checking'?{tone:'checking' as const,value:'CHECKING'}:phase==='error'?{tone:'error' as const,value:'UNKNOWN'}:health?.offline&&health.storage.data?{tone:'ready' as const,value:'READY'}:{tone:'warning' as const,value:'UNVERIFIED'}
 const bind=phase==='checking'?{tone:'checking' as const,value:'CHECKING'}:phase==='error'?{tone:'error' as const,value:'UNKNOWN'}:{tone:'ready' as const,value:health?.bind||'UNKNOWN'}
 return <div className="app-shell" data-page={page}><a className="skip-link" href="#main-content">跳到主内容</a><header><button className="brand" aria-label="返回转写工作台" onClick={()=>setPage('asr')}><BrandMark/></button><nav aria-label="主导航">{items.map(([id,index,label,compactLabel,Icon])=><button key={id} className={page===id?'active':''} aria-label={label} aria-current={page===id?'page':undefined} onClick={()=>setPage(id)}><span className="nav-index">{index}</span><Icon size={19}/><span className="nav-label"><span className="nav-label-full">{label}</span><span className="nav-label-short" aria-hidden="true">{compactLabel}</span></span></button>)}</nav><div className="head-actions"><span className="local-mode" data-state={mode.tone} aria-live="polite" aria-label={mode.label} title={`${mode.label}；离线模式不代表服务仅监听 localhost。`}><i aria-hidden="true"/><span className="full-label">{mode.full}</span><span className="compact-label" aria-hidden="true">{mode.compact}</span></span>{authRequired?<button className="logout" onClick={onLogout} title="退出本地会话" aria-label="退出本地会话"><LogOut/></button>:null}<button className="cert-help-button" onClick={()=>setShowTlsHelp(true)} title="HTTPS 证书" aria-label="打开 HTTPS 证书帮助"><ShieldCheck size={17}/></button><a className="global-docs-link" href="/docs" target="_blank" rel="noreferrer" title="API 文档" aria-label="打开 API 文档"><BookOpen size={17}/><span className="full-label">API 文档</span><span className="compact-label" aria-hidden="true">文档</span></a></div></header><main id="main-content" ref={mainRef} tabIndex={-1}>{connectionError?<div className="connection-banner" role="alert"><span>服务连接异常：{connectionError}</span>{onRetry?<button type="button" onClick={onRetry}>重新连接</button>:null}</div>:null}{children}</main><footer><span className="footer-id">CORE_SYS</span><FooterStatus label="ASR_ENGINE" value={asr.value} tone={asr.tone}/><FooterStatus label="TTS_ENGINE" value={tts.value} tone={tts.tone}/><FooterStatus className="local-copy" label="DATA_LOCAL" value={dataLocal.value} tone={dataLocal.tone} detail={dataLocal.value==='READY'?'// 数据本地存储':undefined} title="READY 表示服务已启用离线模式并报告了本地数据目录。"/><FooterStatus className="bind" label="NET_LISTEN //" value={bind.value} tone={bind.tone} title={phase==='ready'?`服务配置的监听地址：${bind.value}。0.0.0.0 表示所有网卡。`:'当前无法确认服务监听地址。'}/></footer>{showTlsHelp?<TlsCertificateModal onClose={()=>setShowTlsHelp(false)}/>:null}</div>
}
