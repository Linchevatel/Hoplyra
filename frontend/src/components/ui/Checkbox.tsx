import { Check } from 'lucide-react'
import type { PointerEvent } from 'react'
import { cn } from '@/lib/utils'

type CheckboxAccent = 'violet' | 'cyan' | 'orange'

interface CheckboxProps {
  checked: boolean
  onChange: (checked: boolean) => void
  label?: string
  description?: string
  size?: 'sm' | 'md'
  accent?: CheckboxAccent
  className?: string
  onPointerDown?: (e: PointerEvent) => void
  disabled?: boolean
}

const accentStyles: Record<
  CheckboxAccent,
  { boxChecked: string; boxHover: string; icon: string; ring: string }
> = {
  violet: {
    boxChecked: 'border-accent-violet/60 bg-accent-violet/25 shadow-[0_0_12px_color-mix(in_srgb,var(--color-accent-violet)_25%,transparent)]',
    boxHover: 'group-hover:border-accent-violet/40 group-hover:bg-accent-violet/10',
    icon: 'text-accent-violet',
    ring: 'peer-focus-visible:ring-accent-violet/40',
  },
  cyan: {
    boxChecked: 'border-accent-cyan/60 bg-accent-cyan/25 shadow-[0_0_12px_color-mix(in_srgb,var(--color-accent-cyan)_25%,transparent)]',
    boxHover: 'group-hover:border-accent-cyan/40 group-hover:bg-accent-cyan/10',
    icon: 'text-accent-cyan',
    ring: 'peer-focus-visible:ring-accent-cyan/40',
  },
  orange: {
    boxChecked: 'border-orange-400/60 bg-orange-500/25 shadow-none',
    boxHover: 'group-hover:border-orange-400/40 group-hover:bg-orange-500/10',
    icon: 'text-orange-200',
    ring: 'peer-focus-visible:ring-orange-500/40',
  },
}

export function Checkbox({
  checked,
  onChange,
  label,
  description,
  size = 'md',
  accent = 'violet',
  className,
  onPointerDown,
  disabled = false,
}: CheckboxProps) {
  const styles = accentStyles[accent]
  const boxSize = size === 'sm' ? 'h-4 w-4 rounded-md' : 'h-5 w-5 rounded-lg'
  const iconSize = size === 'sm' ? 10 : 12

  return (
    <label
      className={cn(
        'group flex items-start gap-3 cursor-pointer select-none',
        disabled && 'opacity-50 cursor-not-allowed',
        className,
      )}
      onPointerDown={onPointerDown}
    >
      <span className="relative shrink-0 mt-0.5">
        <input
          type="checkbox"
          checked={checked}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
          className={cn(
            'peer sr-only',
            styles.ring,
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950',
          )}
        />
        <span
          aria-hidden
          className={cn(
            'flex items-center justify-center border transition-all duration-200',
            boxSize,
            checked
              ? styles.boxChecked
              : cn('border-white/15 bg-white/[0.04]', styles.boxHover),
          )}
        >
          <Check
            size={iconSize}
            strokeWidth={3}
            className={cn(
              'transition-all duration-200',
              styles.icon,
              checked ? 'scale-100 opacity-100' : 'scale-75 opacity-0',
            )}
          />
        </span>
      </span>
      {(label || description) && (
        <span className="min-w-0">
          {label && (
            <span
              className={cn(
                'block font-medium transition-colors',
                size === 'sm' ? 'text-xs text-zinc-300' : 'text-sm text-zinc-200',
                !disabled && 'group-hover:text-white',
              )}
            >
              {label}
            </span>
          )}
          {description && (
            <span className={cn('block text-text-muted mt-1', size === 'sm' ? 'text-[11px]' : 'text-xs')}>
              {description}
            </span>
          )}
        </span>
      )}
    </label>
  )
}
