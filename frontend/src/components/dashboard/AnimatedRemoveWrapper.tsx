import type { ReactNode } from 'react'
import { motion } from 'framer-motion'
import { cn } from '@/lib/utils'

interface AnimatedRemoveWrapperProps {
  removing: boolean
  children: ReactNode
  className?: string
}

export function AnimatedRemoveWrapper({ removing, children, className }: AnimatedRemoveWrapperProps) {
  return (
    <motion.div
      layout
      initial={false}
      animate={
        removing
          ? {
              opacity: 0,
              scale: 0.97,
              x: 36,
              filter: 'blur(3px)',
              height: 0,
              marginTop: 0,
              marginBottom: 0,
            }
          : {
              opacity: 1,
              scale: 1,
              x: 0,
              filter: 'blur(0px)',
              height: 'auto',
            }
      }
      transition={{ duration: 0.32, ease: [0.32, 0.72, 0, 1] }}
      className={cn('overflow-hidden', className)}
    >
      {children}
    </motion.div>
  )
}
