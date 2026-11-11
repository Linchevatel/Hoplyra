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
        'rounded-md border border-accent-cyan/20',
        'bg-accent-cyan/10',
        'text-accent-cyan',
        'ring-0',
        sizes[size],
        className,
      )}
    >
      {showIcon && <Crown size={size === 'sm' ? 10 : size === 'md' ? 12 : 14} className="text-amber-300 shrink-0" />}
      Pro
    </span>
  )
}
