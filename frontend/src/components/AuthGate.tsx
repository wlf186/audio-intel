import {useState,type FormEvent} from 'react'
import {KeyRound,LoaderCircle} from 'lucide-react'

export function AuthGate({loading,error,onLogin}:{loading:boolean;error:string;onLogin:(key:string)=>Promise<void>}){
 const [key,setKey]=useState('')
 const [busy,setBusy]=useState(false)
 const [localError,setLocalError]=useState('')
 const submit=async(event:FormEvent)=>{event.preventDefault();if(!key.trim())return;setBusy(true);setLocalError('');try{await onLogin(key.trim());setKey('')}catch(cause){setLocalError((cause as Error).message)}finally{setBusy(false)}}
 return <div className="auth-backdrop"><form className="auth-card" role="dialog" aria-modal="true" aria-labelledby="auth-title" onSubmit={submit}><KeyRound size={30}/><small>LOCAL ACCESS CONTROL</small><h1 id="auth-title">访问验证</h1>{loading?<p><LoaderCircle className="spin"/>正在检查本地服务…</p>:<><p>此服务已启用 API Key。密钥只用于本次登录交换，不会保存在浏览器存储或 URL 中。</p><label>API Key<input autoFocus type="password" autoComplete="current-password" value={key} onChange={event=>setKey(event.target.value)} placeholder="输入 AUDIO_INTEL_API_KEY"/></label>{localError||error?<p className="error" role="alert">{localError||error}</p>:null}<button className="primary" disabled={busy||!key.trim()}>{busy?'正在验证…':'进入工作台'}</button></>}</form></div>
}
