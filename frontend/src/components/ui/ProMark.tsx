import { Crown } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ProMarkProps {
  className?: string
  size?: 'sm' | 'md' | 'lg'
  showIcon?: boolean
  inline?: boolean
}

const sizes = {
  sm: 'text-[0.65em] px-1.5 py-0.5 gap-0.5',
  md: 'text-xs px-2 py-0.5 gap-1',
  lg: 'text-sm px-2.5 py-1 gap-1.5',
}

export function ProMark({ className, size = 'md', showIcon = true, inline = true }: ProMarkProps) {
  return (
    <span
      className={cn(
        inline ? 'inline-flex' : 'flex',
        'items-center align-middle font-bold uppercase tracking-wider',
        'rounded-md border border-amber-400/40',
        'bg-gradient-to-r from-amber-500/25 via-yellow-500/20 to-amber-400/25',
        'text-amber-200 shadow-[0_0_20px_rgba(251,191,36,0.25)]',
        'ring-1 ring-amber-400/20',
        sizes[size],
        className,
      )}
    >
      {showIcon && <Crown size={size === 'sm' ? 10 : size === 'md' ? 12 : 14} className="text-amber-300 shrink-0" />}
      Pro
    </span>
  )
}
