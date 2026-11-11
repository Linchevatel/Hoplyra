import { createContext, useContext } from 'react'
import { formatRelativeTime, pluralHistory, pluralServers, t, tList, type Locale } from './core'
export interface I18nContextValue {
  locale: Locale
  setLocale: (locale: Locale) => void
  t: typeof t
  tList: typeof tList
  pluralServers: typeof pluralServers
  pluralHistory: typeof pluralHistory
  formatRelativeTime: typeof formatRelativeTime
}

export const I18nContext = createContext<I18nContextValue | null>(null)

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext)
  if (!ctx) throw new Error('useI18n must be used within I18nProvider')
  return ctx
}
