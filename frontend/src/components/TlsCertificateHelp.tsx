import {useEffect,useState} from 'react'
import {Download,LoaderCircle,ShieldCheck} from 'lucide-react'
import {api} from '../lib/api'
import type {TlsBootstrap} from '../lib/types'
import {Modal} from './Modal'
import {Trans,useTranslation} from 'react-i18next'

export function TlsCertificateHelp(){
 const {t}=useTranslation()
 const [resource,setResource]=useState<{data?:TlsBootstrap;error?:string}>({})
 useEffect(()=>{let active=true;void api.tlsBootstrap().then(data=>{if(active)setResource({data})}).catch(error=>{if(active)setResource({error:(error as Error).message})});return()=>{active=false}},[])
 if(resource.error)return <div className="tls-help-state error" role="alert">{t('tls.loadFailed',{message:resource.error})}<button type="button" className="button" onClick={()=>location.reload()}>{t('common.actions.reload')}</button></div>
 if(!resource.data)return <div className="tls-help-state" role="status"><LoaderCircle className="spin"/>{t('tls.loading')}</div>
 const data=resource.data
 if(data.protocol!=='https')return <div className="tls-help-content"><p className="tls-notice"><ShieldCheck/>{t('tls.httpNotice')}</p><p><Trans i18nKey="tls.httpSetup" components={{code:<code/>}}/></p></div>
 if(!data.ca_installation_available||!data.ca_download_urls)return <div className="tls-help-content"><p className="tls-notice warning">{t('tls.missingCa')}</p></div>
 return <div className="tls-help-content">
  <p className="tls-safety">{t('tls.safety')}</p>
  <div className="tls-downloads"><a className="primary" href={data.ca_download_urls.cer} download><Download/>{t('tls.downloadCer')}</a><a className="button" href={data.ca_download_urls.pem} download><Download/>{t('tls.downloadPem')}</a></div>
  <div className="tls-fingerprint"><small>ROOT CA · SHA-256</small><code>{data.ca_sha256_fingerprint}</code></div>
  <p><Trans i18nKey="tls.fingerprintCheck" components={{code:<code/>}}/></p>
  <ol>
   <li><b>Windows: </b>{t('tls.windowsStep')}</li>
   <li><b>iOS: </b>{t('tls.iosStep')}</li>
  </ol>
  <p className="tls-quick-mode"><b>{t('tls.temporaryAccessLabel')}</b>{t('tls.temporaryAccess')}</p>
 </div>
}

export function TlsCertificateModal({onClose}:{onClose:()=>void}){
 const {t}=useTranslation()
 return <Modal title={t('tls.modalTitle')} closeLabel={t('tls.closeHelp')} onClose={onClose}><TlsCertificateHelp/></Modal>
}
