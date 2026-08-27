import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  formatRelativeTime,
  getLocale,
  initI18n,
  pluralHistory,
  pluralServers,
  setLocale,
  subscribeLocale,
  t,
  tList,
  type Locale,
} from './core'

interface I18nContextValue {
  locale: Locale
  setLocale: (locale: Locale) => void
  t: typeof t
  tList: typeof tList
  pluralServers: typeof pluralServers
  pluralHistory: typeof pluralHistory
  formatRelativeTime: typeof formatRelativeTime
}

const I18nContext = createContext<I18nContextValue | null>(null)

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(() => initI18n())

  useEffect(() => subscribeLocale(() => setLocaleState(getLocale())), [])

  const value = useMemo<I18nContextValue>(
    () => ({
      locale,
      setLocale,
      t,
      tList,
      pluralServers,
      pluralHistory,
      formatRelativeTime,
    }),
    [locale],
  )

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext)
  if (!ctx) throw new Error('useI18n must be used within I18nProvider')
  return ctx
}
