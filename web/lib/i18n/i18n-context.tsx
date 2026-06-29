'use client'

import React, { createContext, useContext, useState, useEffect } from 'react'
import { translations, Language, TranslationKey } from './translations'

interface I18nContextType {
  language: Language
  setLanguage: (lang: Language) => void
  t: (key: TranslationKey) => string
}

const I18nContext = createContext<I18nContextType | undefined>(undefined)

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [language, setLanguageState] = useState<Language>('zh')

  // Load language from localStorage on mount
  useEffect(() => {
    const savedLanguage = localStorage.getItem('soulsearcher-language') as Language
    if (savedLanguage && ['en', 'zh', 'ja', 'ko'].includes(savedLanguage)) {
      setLanguageState(savedLanguage)
    }
  }, [])

  // Save language to localStorage when it changes
  const setLanguage = (lang: Language) => {
    setLanguageState(lang)
    localStorage.setItem('soulsearcher-language', lang)
  }

  // Translation function
  const t = (key: TranslationKey): string => {
    // Other languages may be partial. Fall back to English.
    const table = translations[language] as Partial<Record<TranslationKey, string>>
    return table[key] || translations.en[key] || key
  }

  return (
    <I18nContext.Provider value={{ language, setLanguage, t }}>
      {children}
    </I18nContext.Provider>
  )
}

export function useI18n() {
  const context = useContext(I18nContext)
  if (context === undefined) {
    throw new Error('useI18n must be used within an I18nProvider')
  }
  return context
}
