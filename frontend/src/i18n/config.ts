import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import zhTranslation from './locales/zh.json'
import enTranslation from './locales/en.json'

const resources = {
    zh: { translation: zhTranslation },
    en: { translation: enTranslation },
}

function readInitialLanguage(): 'zh' | 'en' {
    try {
        return localStorage.getItem('language') === 'en' ? 'en' : 'zh'
    } catch {
        return 'zh'
    }
}

function syncDocumentLanguage(language: string) {
    document.documentElement.lang = language.startsWith('zh') ? 'zh-CN' : 'en'
}

void i18n
    .use(initReactI18next)
    .init({
        resources,
        lng: readInitialLanguage(),
        fallbackLng: 'zh',
        showSupportNotice: false,
        interpolation: { escapeValue: false },
    })
    .then(() => syncDocumentLanguage(i18n.resolvedLanguage ?? i18n.language))

i18n.on('languageChanged', syncDocumentLanguage)

export default i18n
