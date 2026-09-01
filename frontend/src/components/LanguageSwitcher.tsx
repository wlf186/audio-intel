import {Languages} from 'lucide-react'
import {useTranslation} from 'react-i18next'
import {persistLocale,resolvedLocale,type SupportedLocale} from '../i18n'

export function LanguageSwitcher({placement='header'}:{placement?:'header'|'auth'}){
 const {t,i18n}=useTranslation()
 const locale=resolvedLocale()
 const change=(next:SupportedLocale)=>{persistLocale(next);void i18n.changeLanguage(next)}
 return <label className={`language-switcher ${placement}`} title={t('common.language.label')}>
  <Languages aria-hidden="true" size={17}/><span className="sr-only">{t('common.language.label')}</span>
  <select aria-label={t('common.language.label')} value={locale} onChange={event=>change(event.target.value as SupportedLocale)}>
   <option value="zh-CN">{t('common.language.zh-CN')}</option>
   <option value="en-US">{t('common.language.en-US')}</option>
  </select>
 </label>
}
