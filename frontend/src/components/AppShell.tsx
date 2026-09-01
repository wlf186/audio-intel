import {useEffect,useRef,useState,type ReactNode} from 'react'
import {AudioLines,BookA,BookOpen,Fingerprint,ListMusic,LogOut,Mic2,MonitorCog,ShieldCheck} from 'lucide-react'
import type {Health} from '../lib/types'
import {systemPhase} from '../lib/systemStatus'
import {BrandMark} from './BrandMark'
import {LanguageSwitcher} from './LanguageSwitcher'
import {TlsCertificateModal} from './TlsCertificateHelp'
import {useTranslation} from 'react-i18next'

export type Page='asr'|'tts'|'hotwords'|'voiceprints'|'jobs'|'system'
const items:[Page,string,typeof AudioLines][]=[['asr','01',AudioLines],['tts','02',Mic2],['hotwords','03',BookA],['voiceprints','04',Fingerprint],['jobs','05',ListMusic],['system','06',MonitorCog]]
type Tone='checking'|'ready'|'warning'|'error'|'offline'
function FooterStatus({label,value,tone,className='',detail,title}:{label:string;value:string;tone:Tone;className?:string;detail?:string;title?:string}){
 return <span className={`shell-status ${className}`} data-state={tone} title={title} aria-label={`${label} ${value}`}><i aria-hidden="true"/><span>{label}</span><b>{value}</b>{detail?<span className="status-detail">{detail}</span>:null}</span>
}
export function AppShell({page,setPage,children,health,systemError,connectionError,authRequired,onLogout,onRetry}:{page:Page;setPage:(p:Page)=>void;children:ReactNode;health?:Health;systemError:string;connectionError?:string;authRequired?:boolean;onLogout?:()=>void;onRetry?:()=>void}){
 const {t}=useTranslation()
 const mainRef=useRef<HTMLElement>(null)
 const [showTlsHelp,setShowTlsHelp]=useState(false)
 useEffect(()=>{
  const label=t(`shell.pages.${page}.full`)
  document.title=`${label} · Sandevistan-Audio`
  const frame=requestAnimationFrame(()=>mainRef.current?.focus({preventScroll:true}))
  return()=>cancelAnimationFrame(frame)
 },[page,t])
 const phase=systemPhase(health,systemError)
 const mode=phase==='checking'?{tone:'checking' as const,full:'OFFLINE_MODE // CHECKING',compact:t('shell.offline.checkingCompact'),label:t('shell.offline.checkingLabel')}:phase==='error'?{tone:'error' as const,full:'LOCAL_CORE // DISCONNECTED',compact:t('shell.offline.errorCompact'),label:t('shell.offline.errorLabel')}:health?.offline?{tone:'ready' as const,full:'OFFLINE_MODE // ACTIVE',compact:t('shell.offline.activeCompact'),label:t('shell.offline.activeLabel')}:{tone:'warning' as const,full:'OFFLINE_MODE // INACTIVE',compact:t('shell.offline.inactiveCompact'),label:t('shell.offline.inactiveLabel')}
 const engine=(kind:string)=>phase==='checking'?{tone:'checking' as const,value:'CHECKING'}:phase==='error'?{tone:'error' as const,value:'UNKNOWN'}:health?.services.includes(kind)?{tone:'ready' as const,value:'READY'}:{tone:'offline' as const,value:'OFFLINE'}
 const asr=engine('asr'),tts=engine('tts')
 const dataLocal=phase==='checking'?{tone:'checking' as const,value:'CHECKING'}:phase==='error'?{tone:'error' as const,value:'UNKNOWN'}:health?.offline&&health.storage.data?{tone:'ready' as const,value:'READY'}:{tone:'warning' as const,value:'UNVERIFIED'}
 const bind=phase==='checking'?{tone:'checking' as const,value:'CHECKING'}:phase==='error'?{tone:'error' as const,value:'UNKNOWN'}:{tone:'ready' as const,value:health?.bind||'UNKNOWN'}
 return <div className="app-shell" data-page={page}><a className="skip-link" href="#main-content">{t('shell.skipToMain')}</a><header><button className="brand" aria-label={t('shell.returnToAsr')} onClick={()=>setPage('asr')}><BrandMark/></button><nav aria-label={t('shell.mainNavigation')}>{items.map(([id,index,Icon])=>{const label=t(`shell.pages.${id}.full`);return <button key={id} className={page===id?'active':''} aria-label={label} aria-current={page===id?'page':undefined} onClick={()=>setPage(id)}><span className="nav-index">{index}</span><Icon size={19}/><span className="nav-label"><span className="nav-label-full">{label}</span><span className="nav-label-short" aria-hidden="true">{t(`shell.pages.${id}.short`)}</span></span></button>})}</nav><div className="head-actions"><span className="local-mode" data-state={mode.tone} aria-live="polite" aria-label={mode.label} title={t('shell.offline.scopeHint',{label:mode.label})}><i aria-hidden="true"/><span className="full-label">{mode.full}</span><span className="compact-label" aria-hidden="true">{mode.compact}</span></span>{authRequired?<button className="logout" onClick={onLogout} title={t('shell.logout')} aria-label={t('shell.logout')}><LogOut/></button>:null}<LanguageSwitcher/><button className="cert-help-button" onClick={()=>setShowTlsHelp(true)} title={t('shell.certificate')} aria-label={t('shell.openCertificateHelp')}><ShieldCheck size={17}/></button><a className="global-docs-link" href="/docs" target="_blank" rel="noreferrer" title={t('shell.apiDocs')} aria-label={t('shell.openApiDocs')}><BookOpen size={17}/><span className="full-label">{t('shell.apiDocs')}</span><span className="compact-label" aria-hidden="true">{t('shell.docsCompact')}</span></a></div></header><main id="main-content" ref={mainRef} tabIndex={-1}>{connectionError?<div className="connection-banner" role="alert"><span>{t('shell.connectionError',{message:connectionError})}</span>{onRetry?<button type="button" onClick={onRetry}>{t('shell.reconnect')}</button>:null}</div>:null}{children}</main><footer><span className="footer-id">CORE_SYS</span><FooterStatus label="ASR_ENGINE" value={asr.value} tone={asr.tone}/><FooterStatus label="TTS_ENGINE" value={tts.value} tone={tts.tone}/><FooterStatus className="local-copy" label="DATA_LOCAL" value={dataLocal.value} tone={dataLocal.tone} detail={dataLocal.value==='READY'?t('shell.dataStoredLocally'):undefined} title={t('shell.dataReadyHint')}/><FooterStatus className="bind" label="NET_LISTEN //" value={bind.value} tone={bind.tone} title={phase==='ready'?t('shell.bindReadyHint',{bind:bind.value}):t('shell.bindUnknownHint')}/></footer>{showTlsHelp?<TlsCertificateModal onClose={()=>setShowTlsHelp(false)}/>:null}</div>
}
