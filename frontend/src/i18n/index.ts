import i18n from 'i18next'
import {initReactI18next} from 'react-i18next'
import enUS from './locales/en-US.json'
import zhCN from './locales/zh-CN.json'

export const supportedLocales=['zh-CN','en-US'] as const
export type SupportedLocale=typeof supportedLocales[number]
export const localeStorageKey='audio-intel:ui-locale:v1'

export function normalizeLocale(value?:string|null):SupportedLocale|undefined{
 if(!value)return undefined
 if(value==='zh-CN'||value.toLocaleLowerCase().startsWith('zh-')||value.toLocaleLowerCase()==='zh')return 'zh-CN'
 if(value==='en-US'||value.toLocaleLowerCase().startsWith('en-')||value.toLocaleLowerCase()==='en')return 'en-US'
 return undefined
}

function storedLocale(){
 try{return normalizeLocale(localStorage.getItem(localeStorageKey))}catch{return undefined}
}

export function browserLocale():SupportedLocale{
 if(typeof navigator==='undefined')return 'zh-CN'
 const candidates=[...(navigator.languages||[]),navigator.language]
 for(const candidate of candidates){const locale=normalizeLocale(candidate);if(locale)return locale}
 return 'zh-CN'
}

export function initialLocale():SupportedLocale{return storedLocale()||browserLocale()}

export function persistLocale(locale:SupportedLocale){
 try{localStorage.setItem(localeStorageKey,locale)}catch{}
}

function applyDocumentLocale(locale:SupportedLocale){
 document.documentElement.lang=locale
 document.documentElement.dir=i18n.dir(locale)
}

export async function initializeI18n(){
 const locale=initialLocale()
 await i18n.use(initReactI18next).init({
  resources:{'zh-CN':{translation:zhCN},'en-US':{translation:enUS}},
  lng:locale,
  fallbackLng:'zh-CN',
  supportedLngs:[...supportedLocales],
  load:'currentOnly',
  returnNull:false,
  returnEmptyString:false,
  interpolation:{escapeValue:false},
  react:{useSuspense:false},
 })
 applyDocumentLocale(locale)
 i18n.on('languageChanged',value=>applyDocumentLocale(normalizeLocale(value)||'zh-CN'))
 return i18n
}

export function resolvedLocale():SupportedLocale{return normalizeLocale(i18n.resolvedLanguage)||'zh-CN'}

export default i18n
