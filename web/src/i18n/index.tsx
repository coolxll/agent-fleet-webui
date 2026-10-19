import React, { createContext, useContext, useState, useMemo } from 'react'
import { translations, type Language, type TranslationKey } from './locales'

interface I18nContextType {
  language: Language
  setLanguage: (lang: Language) => void
  toggleLanguage: () => void
  t: (key: TranslationKey, params?: Record<string, string | number>) => string
}

const STORAGE_KEY = 'agent_fleet_lang'

function getInitialLanguage(): Language {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'en' || saved === 'zh') return saved
    if (typeof navigator !== 'undefined' && navigator.language && navigator.language.startsWith('zh')) {
      return 'zh'
    }
  } catch {
    // fallback if localStorage not accessible
  }
  return 'zh' // Default to Chinese as user requested, or toggleable anytime
}

const I18nContext = createContext<I18nContextType>({
  language: 'zh',
  setLanguage: () => {},
  toggleLanguage: () => {},
  t: (key) => key
})

export const I18nProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [language, setLangState] = useState<Language>(getInitialLanguage)

  const setLanguage = (lang: Language) => {
    setLangState(lang)
    try {
      localStorage.setItem(STORAGE_KEY, lang)
    } catch {
      // ignore
    }
  }

  const toggleLanguage = () => {
    setLanguage(language === 'zh' ? 'en' : 'zh')
  }

  const t = useMemo(() => {
    return (key: TranslationKey, params?: Record<string, string | number>): string => {
      const dict = translations[language] || translations.en
      let str: string = (dict as any)[key] ?? (translations.en as any)[key] ?? key

      if (params) {
        for (const [k, v] of Object.entries(params)) {
          str = str.replace(new RegExp(`\\{${k}\\}`, 'g'), String(v))
        }
      }
      return str
    }
  }, [language])

  return (
    <I18nContext.Provider value={{ language, setLanguage, toggleLanguage, t }}>
      {children}
    </I18nContext.Provider>
  )
}

export function useI18n() {
  return useContext(I18nContext)
}

export type { Language, TranslationKey }
