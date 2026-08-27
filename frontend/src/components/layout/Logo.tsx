import { Link } from 'react-router-dom'
import { HoplyraMark } from './HoplyraMark'
import { useI18n } from '@/i18n/I18nProvider'
import { cn } from '@/lib/utils'

interface LogoProps {
  size?: 'sm' | 'md' | 'lg'
  showText?: boolean
  showSlogan?: boolean
  to?: string
  className?: string
}

export function Logo({ size = 'md', showText = true, showSlogan = false, to = '/', className }: LogoProps) {
  const { t } = useI18n()
  const sizes = {
    sm: { mark: 22, box: 'w-10 h-10', text: 'text-xl', slogan: 'text-xs', gap: 'gap-3' },
    md: { mark: 26, box: 'w-11 h-11', text: 'text-2xl', slogan: 'text-sm', gap: 'gap-3' },
    lg: { mark: 36, box: 'w-14 h-14', text: 'text-4xl', slogan: 'text-base', gap: 'gap-4' },
  }

  const s = sizes[size]

  return (
    <Link to={to} className={cn('inline-flex items-center group', s.gap, className)}>
      <div className="relative shrink-0">
        <div className="absolute -inset-1 bg-gradient-to-r from-cyan-400 to-violet-500 rounded-xl blur-lg opacity-50 group-hover:opacity-75 transition-opacity" />
        <div
          className={cn(
            'relative flex items-center justify-center rounded-xl bg-[#07070f]/95 border border-cyan-400/30 shadow-[0_0_24px_rgba(34,211,238,0.18),inset_0_1px_0_rgba(255,255,255,0.1)]',
            s.box,
          )}
        >
          <HoplyraMark size={s.mark} />
        </div>
      </div>
      {showText && (
        <div className="flex flex-col gap-1 leading-none min-w-0">
          <span className={cn('font-bold tracking-tight', s.text)}>
            Hop<span className="gradient-text">lyra</span>
          </span>
          {showSlogan && (
            <span
              className={cn(
                'font-medium text-zinc-400 tracking-normal truncate max-w-[13rem] sm:max-w-none',
                s.slogan,
              )}
            >
              {t('brand.slogan')}
            </span>
          )}
        </div>
      )}
    </Link>
  )
}
