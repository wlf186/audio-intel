import {useEffect,useRef,useState,type FormEvent,type KeyboardEvent} from 'react'
import {KeyRound,LoaderCircle,ShieldCheck} from 'lucide-react'
import {TlsCertificateHelp} from './TlsCertificateHelp'

export function AuthGate({loading,error,onLogin}:{loading:boolean;error:string;onLogin:(key:string)=>Promise<void>}){
 const dialogRef=useRef<HTMLDialogElement>(null)
 const inputRef=useRef<HTMLInputElement>(null)
 const [key,setKey]=useState('')
 const [busy,setBusy]=useState(false)
 const [localError,setLocalError]=useState('')
 const [showTlsHelp,setShowTlsHelp]=useState(false)
 const submit=async(event:FormEvent)=>{event.preventDefault();if(!key.trim())return;setBusy(true);setLocalError('');try{await onLogin(key.trim());setKey('')}catch(cause){setLocalError((cause as Error).message)}finally{setBusy(false)}}
 useEffect(()=>{const dialog=dialogRef.current;if(!dialog)return;if(!dialog.open)dialog.showModal();return()=>{if(dialog.open)dialog.close();requestAnimationFrame(()=>document.querySelector<HTMLElement>('.app-shell nav [aria-current="page"]')?.focus())}},[])
 useEffect(()=>{if(!loading)inputRef.current?.focus()},[loading])
 const trapFocus=(event:KeyboardEvent<HTMLDialogElement>)=>{if(event.key!=='Tab')return;const focusable=[...event.currentTarget.querySelectorAll<HTMLElement>('input:not(:disabled),button:not(:disabled),a[href]')];if(!focusable.length)return;const first=focusable[0];const last=focusable[focusable.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}}
 return <dialog ref={dialogRef} className="auth-backdrop" aria-labelledby="auth-title" onCancel={event=>event.preventDefault()} onKeyDown={trapFocus}><form className={`auth-card ${showTlsHelp?'show-tls-help':''}`} method="dialog" onSubmit={submit}><KeyRound size={30}/><small>LOCAL ACCESS CONTROL</small><h1 id="auth-title">访问验证</h1>{loading?<p role="status"><LoaderCircle className="spin"/>正在检查本地服务…</p>:showTlsHelp?<><TlsCertificateHelp/><button type="button" className="button auth-help-back" onClick={()=>setShowTlsHelp(false)}>返回登录</button></>:<><p>此服务已启用 API Key。密钥只用于本次登录交换，不会保存在浏览器存储或 URL 中。</p><button type="button" className="auth-tls-help" onClick={()=>setShowTlsHelp(true)}><ShieldCheck/>先安装 HTTPS 根证书</button><label>API Key<input ref={inputRef} type="password" autoComplete="current-password" value={key} onChange={event=>setKey(event.target.value)} placeholder="输入 AUDIO_INTEL_API_KEY"/></label>{localError||error?<p className="error" role="alert">{localError||error}</p>:null}<button className="primary" disabled={busy||!key.trim()}>{busy?'正在验证…':'进入工作台'}</button></>}</form></dialog>
}
