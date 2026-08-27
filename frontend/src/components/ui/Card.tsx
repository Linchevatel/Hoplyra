import { cn } from '@/lib/utils'
import type { ReactNode } from 'react'

interface CardProps {
  children: ReactNode
  className?: string
  hover?: boolean
  glow?: 'cyan' | 'violet' | null
}

export function Card({ children, className, hover, glow }: CardProps) {
  return (
    <div
      className={cn(
        'glass rounded-2xl p-6',
        hover && 'glass-hover transition-all duration-300 cursor-pointer',
        glow === 'cyan' && 'glow-cyan',
        glow === 'violet' && 'glow-violet',
        className,
      )}
    >
      {children}
    </div>
  )
}
