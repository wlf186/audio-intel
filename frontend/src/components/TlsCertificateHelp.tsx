import {useEffect,useState} from 'react'
import {Download,LoaderCircle,ShieldCheck} from 'lucide-react'
import {api} from '../lib/api'
import type {TlsBootstrap} from '../lib/types'
import {Modal} from './Modal'

export function TlsCertificateHelp(){
 const [resource,setResource]=useState<{data?:TlsBootstrap;error?:string}>({})
 useEffect(()=>{let active=true;void api.tlsBootstrap().then(data=>{if(active)setResource({data})}).catch(error=>{if(active)setResource({error:(error as Error).message})});return()=>{active=false}},[])
 if(resource.error)return <div className="tls-help-state error" role="alert">证书信息加载失败：{resource.error}<button type="button" className="button" onClick={()=>location.reload()}>重新加载</button></div>
 if(!resource.data)return <div className="tls-help-state" role="status"><LoaderCircle className="spin"/>正在读取 HTTPS 证书信息…</div>
 const data=resource.data
 if(data.protocol!=='https')return <div className="tls-help-content"><p className="tls-notice"><ShieldCheck/>当前服务使用 HTTP。局域网 IP 上的浏览器录音需要将服务配置为 HTTPS。</p><p>生成证书后设置 <code>AUDIO_INTEL_PROTOCOL=https</code>，再重新启动服务。完整步骤见项目文档。</p></div>
 if(!data.ca_installation_available||!data.ca_download_urls)return <div className="tls-help-content"><p className="tls-notice warning">当前已使用 HTTPS，但服务端未配置可下载的根 CA。你仍可在桌面 Chrome/Edge 的证书警告页选择继续访问，但这不验证服务器身份。</p></div>
 return <div className="tls-help-content">
  <p className="tls-safety">在输入 API Key 或上传音频前，建议安装根证书并核对指纹。下载的是公开证书，不包含私钥。</p>
  <div className="tls-downloads"><a className="primary" href={data.ca_download_urls.cer} download><Download/>下载 Windows / iOS 证书</a><a className="button" href={data.ca_download_urls.pem} download><Download/>下载 PEM</a></div>
  <div className="tls-fingerprint"><small>ROOT CA · SHA-256</small><code>{data.ca_sha256_fingerprint}</code></div>
  <p>请将上方指纹与服务端运行 <code>./service.sh tls fingerprint</code>（Windows 为 <code>service.cmd tls fingerprint</code>）显示的值通过可信渠道比对。</p>
  <ol>
   <li><b>Windows：</b>打开 .cer，安装到“受信任的根证书颁发机构”；可选“当前用户”。重启 Chrome/Edge。</li>
   <li><b>iOS：</b>打开 .cer 并安装描述文件；再到“设置 → 通用 → 关于本机 → 证书信任设置”启用完全信任，然后重启浏览器。</li>
  </ol>
  <p className="tls-quick-mode"><b>临时快速访问：</b>桌面 Chrome/Edge 可在证书警告页选择“高级 → 继续”。连接仍加密，但服务器身份未验证，主动中间人仍可能截获 API Key 和音频；不承诺 Safari、iOS 或 Firefox 可用。</p>
 </div>
}

export function TlsCertificateModal({onClose}:{onClose:()=>void}){
 return <Modal title="HTTPS 证书与浏览器录音" closeLabel="关闭 HTTPS 证书帮助" onClose={onClose}><TlsCertificateHelp/></Modal>
}
