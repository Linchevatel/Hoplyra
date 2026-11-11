import { I18nContext, type I18nContextValue } from './i18n-context'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
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
