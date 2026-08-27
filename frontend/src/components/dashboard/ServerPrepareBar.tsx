import { motion } from 'framer-motion'
import { Loader2 } from 'lucide-react'
import type { ServerPrepareProgress } from '@/lib/types'
import { useI18n } from '@/i18n/I18nProvider'
import { cn } from '@/lib/utils'

export function ServerPrepareBar({ progress }: { progress: ServerPrepareProgress }) {
  const { t } = useI18n()
  const percent = Math.min(100, Math.max(0, progress.percent))
  const isError = progress.status === 'error'

  return (
    <div className="mt-3 rounded-xl border border-amber-500/20 bg-amber-500/[0.06] px-3 py-2.5">
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 min-w-0">
          {!isError && <Loader2 size={14} className="text-amber-400 shrink-0 animate-spin" />}
          <span className={cn('text-xs font-medium truncate', isError ? 'text-red-400' : 'text-amber-300/90')}>
            {isError ? progress.message : progress.message || t('dashboard.servers.prepareTitle')}
          </span>
        </div>
        {!isError && <span className="text-[11px] tabular-nums text-amber-400/80 shrink-0">{percent}%</span>}
      </div>
      {!isError && (
        <div className="relative h-1.5 rounded-full overflow-hidden bg-white/5">
          <motion.div
            className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-amber-500 to-amber-300"
            initial={{ width: '0%' }}
            animate={{ width: `${percent}%` }}
            transition={{ duration: 0.45, ease: 'easeOut' }}
          />
          {progress.status === 'running' && percent < 95 && (
            <motion.div
              className="absolute inset-y-0 w-10 rounded-full bg-gradient-to-r from-transparent via-amber-200/35 to-transparent"
              animate={{ x: ['-100%', '280%'] }}
              transition={{ duration: 1.6, repeat: Infinity, ease: 'linear' }}
            />
          )}
        </div>
      )}
      {!isError && progress.status === 'running' && (
        <p className="mt-2 text-[11px] text-zinc-500">{t('dashboard.servers.prepareHint')}</p>
      )}
    </div>
  )
}
