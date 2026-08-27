import { Languages } from 'lucide-react'
import { useI18n } from './I18nProvider'
import type { Locale } from './types'
import { cn } from '@/lib/utils'

interface LanguageSwitcherProps {
  className?: string
  compact?: boolean
}

export function LanguageSwitcher({ className, compact = false }: LanguageSwitcherProps) {
  const { locale, setLocale } = useI18n()

  function toggle() {
    const next: Locale = locale === 'en' ? 'ru' : 'en'
    setLocale(next)
  }

  return (
    <button
      type="button"
      onClick={toggle}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-xs font-medium text-zinc-400 hover:text-white hover:border-cyan-500/30 hover:bg-white/[0.06] transition-colors cursor-pointer',
        className,
      )}
      aria-label={locale === 'en' ? 'Switch to Russian' : 'Switch to English'}
      title={locale === 'en' ? 'Русский' : 'English'}
    >
      <Languages size={14} className="text-cyan-400/80 shrink-0" />
      <span className="uppercase tracking-wide">{locale === 'en' ? 'EN' : 'RU'}</span>
      {!compact && (
        <span className="text-zinc-600 hidden sm:inline">/{locale === 'en' ? 'RU' : 'EN'}</span>
      )}
    </button>
  )
}
