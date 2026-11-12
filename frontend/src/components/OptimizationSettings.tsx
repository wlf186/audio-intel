import {useEffect,useRef,useState} from 'react'
import {useTranslation} from 'react-i18next'
import {request} from '../lib/api'
import {optimizationSettingsPath as root,needsServiceNotice,type OptimizationConfiguration} from '../lib/optimization'
import './optimization.css'

export function OptimizationSettings(){
 const {t}=useTranslation()
 const [config,setConfig]=useState<OptimizationConfiguration>()
 const [saved,setSaved]=useState<OptimizationConfiguration>()
 const [dirty,setDirty]=useState(false)
 const [apiKey,setApiKey]=useState('')
 const [keyChanged,setKeyChanged]=useState(false)
 const [extraText,setExtraText]=useState('{}')
 const [models,setModels]=useState<string[]>([])
 const [busy,setBusy]=useState('')
 const [error,setError]=useState('')
 const [notice,setNotice]=useState('')
 const [reload,setReload]=useState(0)
 const controller=useRef<AbortController|undefined>(undefined)
 useEffect(()=>{
  const active=new AbortController();controller.current=active
  void request<OptimizationConfiguration>(root,{signal:active.signal}).then(value=>{setConfig({...value,context_length:value.context_length??16384,max_llm_calls:value.max_llm_calls??300,max_output_tokens:value.max_output_tokens??4096});setSaved(value);setApiKey('');setKeyChanged(false);setExtraText(JSON.stringify(value.extra_body||{},null,2));setDirty(false);setModels(value.model?[value.model]:[]);setError('')}).catch(cause=>{if(!active.signal.aborted)setError((cause as Error).message)})
  return()=>controller.current?.abort()
 },[reload])
 const edit=(value:Partial<OptimizationConfiguration>)=>{controller.current?.abort();setBusy('');setConfig(current=>current?{...current,...value,verified_at:undefined,verification_token:undefined,observed_context_length:undefined}:current);setDirty(true);if(value.base_url!==undefined||value.use_proxy!==undefined)setModels([]);setNotice('');setError('')}
 let extraBody:Record<string,unknown>={}
 let validExtra=true
 try{const value:unknown=JSON.parse(extraText);if(!value||typeof value!=='object'||Array.isArray(value))validExtra=false;else extraBody=value as Record<string,unknown>}catch{validExtra=false}
 const validCalls=Number.isSafeInteger(config?.max_llm_calls)&&(config?.max_llm_calls??0)>=1
 const validOutput=Number.isSafeInteger(config?.max_output_tokens)&&(config?.max_output_tokens??0)>=1024&&(config?.max_output_tokens??0)+1024<(config?.context_length??0)
 const validContext=Number.isSafeInteger(config?.context_length)&&(config?.context_length??0)>=16384
 async function run(kind:'discover'|'verify'|'save'){
  if(!config)return
  controller.current?.abort();const active=new AbortController();controller.current=active
  setBusy(kind);setError('');setNotice('')
  const next={...config,extra_body:extraBody}
  const credentials=keyChanged?{api_key:apiKey}:{}
  setConfig(next)
  try{
   if(kind==='discover'){
    const value=await request<{models:{name:string}[]}>(root+'/discover',{method:'POST',signal:active.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({base_url:next.base_url,use_proxy:next.use_proxy,...credentials})})
    if(!active.signal.aborted){setModels(value.models.map(m=>m.name));setNotice(t(value.models.length?'optimization.connected':'optimization.noModels'))}
   }else if(kind==='verify'){
    const value=await request<OptimizationConfiguration>(root+'/verify',{method:'POST',signal:active.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({base_url:next.base_url,use_proxy:next.use_proxy,...credentials,model:next.model,context_length:next.context_length,max_output_tokens:next.max_output_tokens,extra_body:next.extra_body})})
    if(!active.signal.aborted){setConfig({...value,enabled:next.enabled,max_llm_calls:next.max_llm_calls??300,max_output_tokens:value.max_output_tokens??next.max_output_tokens??4096});setDirty(true);setNotice(t('optimization.verified'))}
   }else{
    const value=await request<OptimizationConfiguration>(root,{method:'PUT',signal:active.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({base_url:next.base_url,use_proxy:next.use_proxy,...credentials,model:next.model,context_length:next.context_length,max_output_tokens:next.max_output_tokens,max_llm_calls:next.max_llm_calls,extra_body:next.extra_body,enabled:next.enabled,verification_token:next.verification_token})})
    if(!active.signal.aborted){setConfig(value);setSaved(value);setApiKey('');setKeyChanged(false);setDirty(false);setNotice(t('optimization.saved'))}
   }
  }catch(cause){if(!active.signal.aborted)setError((cause as Error).message)}
  finally{if(!active.signal.aborted)setBusy('')}
 }
 return <section className="optimization-settings" id="expression-optimization"><h2>{t('optimization.title')}</h2><p className="muted">{t('optimization.settingsHelp')}</p>
  {!config?error?<p role="alert">{error} <button className="button" onClick={()=>setReload(n=>n+1)}>{t('common.actions.retry')}</button></p>:<p role="status">{t('common.states.loading')}</p>:<>
   <p className="optimization-config-status" role="status">{t(dirty?'optimization.unsavedConfiguration':'optimization.savedConfiguration')} {saved?.model?<span> · {saved.model} · {t(saved.enabled?'optimization.enabledState':'optimization.disabledState')}</span>:null}</p>
   <div className="optimization-connection"><label>{t('optimization.url')}<input type="url" placeholder="http://localhost:11434/v1" aria-describedby="optimization-address-help" value={config.base_url} onChange={event=>{setApiKey('');setKeyChanged(false);edit({base_url:event.target.value,model:'',has_api_key:false})}}/></label><button className="button" disabled={!config.base_url||Boolean(busy)} onClick={()=>void run('discover')}>{t(busy==='discover'?'optimization.connecting':'optimization.discover')}</button></div>
   <small id="optimization-address-help" className="muted">{t('optimization.addressHelp')}</small>
   {needsServiceNotice(config.base_url)?<p className="optimization-service-notice" role="note">{t('optimization.serviceNotice')}</p>:null}
   <label className="optimization-check"><input type="checkbox" checked={config.use_proxy} onChange={event=>edit({use_proxy:event.target.checked})}/>{t('optimization.proxy')}</label><small className="muted">{t('optimization.proxyHelp')}</small>
   <div className="optimization-connection"><label>{t('optimization.apiKey')}<input type="password" autoComplete="new-password" value={apiKey} placeholder={t(config.has_api_key&&!keyChanged?'optimization.keyStored':'optimization.keyOptional')} onChange={event=>{setApiKey(event.target.value);setKeyChanged(true);edit({})}}/></label><button className="button" onClick={()=>{setApiKey('');setKeyChanged(true);edit({has_api_key:false})}}>{t('optimization.clearKey')}</button></div>
   <div className="optimization-connection"><label>{t('optimization.model')}<input type="text" list="optimization-models" value={config.model} placeholder={t('optimization.modelPlaceholder')} onChange={event=>edit({model:event.target.value})}/><datalist id="optimization-models">{models.map(model=><option key={model} value={model}/>)}</datalist></label></div>
   <details><summary>{t('optimization.advanced')}</summary><label>{t('optimization.extraBody')}<textarea aria-label={t('optimization.extraBody')} rows={5} spellCheck={false} aria-invalid={!validExtra} value={extraText} onChange={event=>{setExtraText(event.target.value);edit({})}}/></label><small className="muted">{t('optimization.extraHelp')}</small>{!validExtra?<p role="alert">{t('optimization.invalidExtra')}</p>:null}</details>
   <div className="optimization-connection"><label>{t('optimization.contextLength')}<input type="number" min={16} step="any" required aria-describedby="optimization-context-help" aria-invalid={!validContext} value={config.context_length===undefined?'':config.context_length/1024} onChange={event=>edit({context_length:event.target.value===''?undefined:Number(event.target.value)*1024})}/></label>{config.model?<button className="button" disabled={Boolean(busy)||!validContext||!validOutput||!validCalls||!validExtra} onClick={()=>void run('verify')}>{t('optimization.verify')}</button>:null}</div>
   <div className="optimization-connection"><label>{t('optimization.outputLength')}<input type="number" min={1} step="any" aria-invalid={!validOutput} value={config.max_output_tokens===undefined?'':config.max_output_tokens/1024} onChange={event=>{const value=Number(event.target.value)*1024;const advanced={...extraBody};for(const key of ['max_tokens','max_completion_tokens'])if(key in advanced)advanced[key]=value;setExtraText(JSON.stringify(advanced,null,2));edit({max_output_tokens:value})}}/></label><label>{t('optimization.defaultCallLimit')}<input type="number" min={1} step={1} aria-invalid={!validCalls} value={config.max_llm_calls??''} onChange={event=>{setConfig({...config,max_llm_calls:Number(event.target.value)});setDirty(true)}}/></label></div>
   <small className="muted">{t('optimization.outputHelp')}</small>
   <small id="optimization-context-help" className="muted">{t('optimization.contextHelp')}{validContext?` ${t('optimization.requestedWindow',{value:(config.context_length??0)/1024})}`:''}</small>
   {busy==='verify'?<p role="status">{t('optimization.verifying')}</p>:config.verified_at?<details className="optimization-verified"><summary>{t('optimization.connectionDetails')}</summary><p>{t('optimization.budgetNotice')}</p><p>{t('optimization.verifiedAt',{time:config.verified_at})}</p></details>:null}
   <div className="optimization-settings-actions"><label className="optimization-check"><input type="checkbox" checked={config.enabled} disabled={(!config.verified_at&&!config.enabled)||Boolean(busy)} onChange={event=>{setConfig({...config,enabled:event.target.checked});setDirty(true)}}/>{t('optimization.enable')}</label><button className="primary" disabled={!config.model||!validContext||!validOutput||!validCalls||!validExtra||Boolean(busy)||config.enabled&&!config.verified_at} onClick={()=>void run('save')}>{t(busy==='save'?'optimization.saving':config.enabled?'optimization.saveEnable':'optimization.save')}</button></div>
   {error?<p className="error" role="alert">{error}</p>:notice?<p role="status">{notice}</p>:null}
  </>}
 </section>
}
