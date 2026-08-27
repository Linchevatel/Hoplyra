import { cn } from '@/lib/utils'

interface BadgeProps {
  children: React.ReactNode
  variant?: 'default' | 'success' | 'warning' | 'error' | 'pro' | 'protocol'
  className?: string
}

export function Badge({ children, variant = 'default', className }: BadgeProps) {
  const variants = {
    default: 'surface-panel text-zinc-400',
    success: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
    warning: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
    error: 'bg-red-500/10 text-red-400 border-red-500/20',
    pro: 'bg-gradient-to-r from-amber-500/25 via-yellow-500/20 to-amber-400/25 text-amber-200 border-amber-400/40 shadow-[0_0_16px_rgba(251,191,36,0.2)] font-bold uppercase tracking-wider',
    protocol: 'surface-panel text-zinc-300 font-mono text-xs',
  }

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium border',
        variants[variant],
        className,
      )}
    >
      {children}
    </span>
  )
}
