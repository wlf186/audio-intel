import {useTranslation} from 'react-i18next'
import {useAudioWaveform} from './useAudioWaveform'
import {ResourceStatePanel} from './ResourceStatePanel'
import {Waveform} from './Waveform'

export function EnhancedWaveform({src,peaks,currentTime,duration,onSeek}:{src:string;peaks?:number[];currentTime:number;duration:number;onSeek:(time:number)=>void}){
 const {t}=useTranslation()
 const resource=useAudioWaveform(src,Boolean(peaks?.length),true)
 const values=peaks?.length?peaks:resource.data?.waveform
 return <div ref={resource.container} className="asr-enhanced-waveform">
  {values?.length?<Waveform peaks={values} currentTime={currentTime} duration={duration} onSeek={onSeek}/>:<fieldset className="waveform-resource" disabled={resource.retryDisabled}>
   <ResourceStatePanel state={resource.error?'error':'loading'} loadingLabel={t('asr.enhancement.waveformLoading')} errorLabel={t('asr.enhancement.waveformFailed')} retry={resource.retry}/>
  </fieldset>}
 </div>
}
