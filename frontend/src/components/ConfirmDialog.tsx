import {LoaderCircle} from 'lucide-react'
import {Modal} from './Modal'

type Props={title:string;description:string;confirmLabel:string;busy?:boolean;danger?:boolean;onConfirm:()=>void;onClose:()=>void}

export function ConfirmDialog({title,description,confirmLabel,busy=false,danger=false,onConfirm,onClose}:Props){
 return <Modal title={title} closeLabel={`关闭${title}`} onClose={onClose}>
  <p>{description}</p>
  <div className="modal-actions"><button className="button" disabled={busy} onClick={onClose}>取消</button><button className={danger?'button danger-action':'primary'} disabled={busy} onClick={onConfirm}>{busy?<LoaderCircle className="spin"/>:null}{busy?'正在处理…':confirmLabel}</button></div>
 </Modal>
}
