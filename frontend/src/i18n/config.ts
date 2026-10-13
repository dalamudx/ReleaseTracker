import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

type AppLanguage = 'zh' | 'en'

// Each base dictionary is ~85 KB. Load only the active one instead of shipping both in the entry chunk.
const loaders: Record<AppLanguage, () => Promise<{ default: Record<string, unknown> }>> = {
    zh: () => import('./locales/zh.json'),
    en: () => import('./locales/en.json'),
}
const loadedLanguages = new Set<AppLanguage>()

function toAppLanguage(language: string): AppLanguage {
    return language.startsWith('en') ? 'en' : 'zh'
}

function readInitialLanguage(): AppLanguage {
    try {
        return localStorage.getItem('language') === 'en' ? 'en' : 'zh'
    } catch {
        return 'zh'
    }
}

function syncDocumentLanguage(language: string) {
    document.documentElement.lang = language.startsWith('zh') ? 'zh-CN' : 'en'
}

export async function loadLanguage(language: string) {
    const target = toAppLanguage(language)
    if (loadedLanguages.has(target)) return
    const { default: translation } = await loaders[target]()
    // Route modules may already have merged their own keys into this namespace; keep them.
    i18n.addResourceBundle(target, 'translation', translation, true, false)
    loadedLanguages.add(target)
}

/** Switch language only after its dictionary is present, so the UI never flashes raw keys. */
export async function changeAppLanguage(language: string) {
    await loadLanguage(language)
    await i18n.changeLanguage(language)
}

void i18n.use(initReactI18next).init({
    resources: {},
    lng: readInitialLanguage(),
    fallbackLng: 'zh',
    showSupportNotice: false,
    interpolation: { escapeValue: false },
})

i18n.on('languageChanged', syncDocumentLanguage)

/** Resolves once the initial dictionary is loaded; the app renders after this. */
export const i18nReady = changeAppLanguage(readInitialLanguage()).catch((error: unknown) => {
    console.error('Failed to load translations', error)
})

export default i18n
