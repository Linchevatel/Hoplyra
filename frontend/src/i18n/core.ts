import type { Locale, TranslationTree } from './types'
export type { Locale }
import { en } from './locales/en'
import { ru } from './locales/ru'

const STORAGE_KEY = 'hoplyra_locale'

const catalogs: Record<Locale, TranslationTree> = { en, ru }

let locale: Locale = 'en'
let listeners = new Set<() => void>()

function readStoredLocale(): Locale {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'en' || stored === 'ru') return stored
  } catch {
    /* ignore */
  }
  return 'en'
}

export function initI18n(): Locale {
  locale = readStoredLocale()
  if (typeof document !== 'undefined') {
    document.documentElement.lang = locale
  }
  return locale
}

export function getLocale(): Locale {
  return locale
}

export function setLocale(next: Locale): void {
  if (next === locale) return
  locale = next
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch {
    /* ignore */
  }
  if (typeof document !== 'undefined') {
    document.documentElement.lang = next
  }
  listeners.forEach((fn) => fn())
}

export function subscribeLocale(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function resolve(tree: TranslationTree, path: string): string | string[] | undefined {
  const res = path.split('.').reduce<TranslationTree | string | string[] | undefined>((acc, key) => {
    if (acc == null || typeof acc === 'string' || Array.isArray(acc)) return undefined
    return acc[key]
  }, tree)
  if (res != null && typeof res !== 'string' && !Array.isArray(res)) return undefined
  return res
}

export function t(path: string, vars?: Record<string, string | number>): string {
  const value = resolve(catalogs[locale], path)
  if (typeof value !== 'string') return path
  if (!vars) return value
  return value.replace(/\{(\w+)\}/g, (_, key: string) => String(vars[key] ?? `{${key}}`))
}

export function tList(path: string): string[] {
  const value = resolve(catalogs[locale], path)
  return Array.isArray(value) ? value.map(String) : []
}

export function pluralServers(count: number): string {
  if (locale === 'en') return count === 1 ? t('plural.server.one') : t('plural.server.many')
  const mod10 = count % 10
  const mod100 = count % 100
  if (mod10 === 1 && mod100 !== 11) return t('plural.server.one')
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return t('plural.server.few')
  return t('plural.server.many')
}

export function pluralHistory(count: number): string {
  if (locale === 'en') return count === 1 ? t('plural.history.one') : t('plural.history.many')
  const mod10 = count % 10
  const mod100 = count % 100
  if (mod10 === 1 && mod100 !== 11) return t('plural.history.one')
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return t('plural.history.few')
  return t('plural.history.many')
}

export function formatRelativeTime(iso: string): string {
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60_000)
  if (mins < 1) return t('time.now')
  if (mins < 60) return t('time.minutes', { n: mins })
  const hours = Math.floor(mins / 60)
  if (hours < 24) return t('time.hours', { n: hours })
  const days = Math.floor(hours / 24)
  return t('time.days', { n: days })
}
