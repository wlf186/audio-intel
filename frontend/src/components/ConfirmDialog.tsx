import {LoaderCircle} from 'lucide-react'
import {Modal} from './Modal'
import {useTranslation} from 'react-i18next'

type Props={title:string;description:string;confirmLabel:string;busy?:boolean;danger?:boolean;onConfirm:()=>void;onClose:()=>void}

export function ConfirmDialog({title,description,confirmLabel,busy=false,danger=false,onConfirm,onClose}:Props){
 const {t}=useTranslation()
 return <Modal title={title} closeLabel={t('common.dialog.closeNamed',{title})} onClose={onClose}>
  <p>{description}</p>
  <div className="modal-actions"><button className="button" disabled={busy} onClick={onClose}>{t('common.actions.cancel')}</button><button className={danger?'button danger-action':'primary'} disabled={busy} onClick={onConfirm}>{busy?<LoaderCircle className="spin"/>:null}{busy?t('common.states.processing'):confirmLabel}</button></div>
 </Modal>
}
