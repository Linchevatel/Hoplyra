import { forwardRef, type InputHTMLAttributes, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  label?: ReactNode
  error?: string
  hint?: string
  suffix?: ReactNode
  inputSize?: 'sm' | 'md'
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, label, error, hint, id, suffix, inputSize = 'md', ...props }, ref) => {
    const inputId = id ?? (typeof label === 'string' ? label.toLowerCase().replace(/\s/g, '-') : undefined)

    return (
      <div className={cn('space-y-1.5', inputSize === 'sm' && 'space-y-1')}>
        {label && (
          <label htmlFor={inputId} className={cn('block font-medium uppercase tracking-wider text-text-secondary', inputSize === 'sm' ? 'text-xs' : 'text-sm')}>
            {label}
          </label>
        )}
        <div className="relative">
          <input
            ref={ref}
            id={inputId}
            className={cn(
              'w-full rounded-xl bg-white/[0.04] glow-border text-zinc-100 placeholder:text-text-muted',
              'focus:outline-none focus:border-accent-cyan/35 focus:ring-2 focus:ring-accent-cyan/15',
              'focus:shadow-[0_0_0_1px_color-mix(in_srgb,var(--color-accent-cyan)_10%,transparent),0_4px_24px_color-mix(in_srgb,var(--color-accent-cyan)_8%,transparent)] transition-all',
              'disabled:opacity-40 disabled:cursor-not-allowed',
              inputSize === 'sm' ? 'px-3 py-1.5 text-xs rounded-lg' : 'px-4 py-2.5 text-sm',
              suffix && 'pr-9',
              error && 'border-rose-500/50 focus:border-rose-500/50 focus:ring-red-500/20',
              className,
            )}
            {...props}
          />
          {suffix && (
            <div className="absolute right-2.5 top-1/2 -translate-y-1/2 flex items-center">
              {suffix}
            </div>
          )}
        </div>
        {error && <p role="alert" className={cn('text-rose-400', inputSize === 'sm' ? 'text-[11px]' : 'text-xs')}>{error}</p>}
        {hint && !error && <p className={cn('text-text-muted', inputSize === 'sm' ? 'text-[11px]' : 'text-xs')}>{hint}</p>}
      </div>
    )
  },
)
Input.displayName = 'Input'
