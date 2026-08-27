import { Fragment } from 'react'
import { motion } from 'framer-motion'
import { Check, X } from 'lucide-react'
import type { ChainHop, HopDeployStatus, VpsServer } from '@/lib/types'
import { PROTOCOLS } from '@/lib/constants'
import { ProtocolIcon } from '@/components/ui/ProtocolIcon'
import { useI18n } from '@/i18n/I18nProvider'
import { cn } from '@/lib/utils'

function segmentClass(left: HopDeployStatus, right: HopDeployStatus): string {
  if (left === 'error' || right === 'error') return 'bg-red-500/70'
  if (left === 'done' && (right === 'done' || right === 'deploying')) return 'bg-emerald-500'
  if (left === 'deploying' || right === 'deploying') return 'bg-amber-400'
  return 'bg-amber-400/25'
}

function SegmentLine({ left, right }: { left: HopDeployStatus; right: HopDeployStatus }) {
  const isActive = left === 'deploying' || right === 'deploying'
  const isDone = left === 'done' && right === 'done'
  const isError = left === 'error' || right === 'error'

  return (
    <div className="relative flex-1 h-1 mx-1 min-w-[2rem] rounded-full overflow-hidden bg-white/5">
      <motion.div
        className={cn('absolute inset-y-0 left-0 rounded-full', segmentClass(left, right))}
        initial={{ width: '0%' }}
        animate={{ width: isDone || left === 'done' ? '100%' : isActive ? '65%' : '0%' }}
        transition={{ duration: isActive ? 1.2 : 0.45, ease: 'easeInOut', repeat: isActive ? Infinity : 0, repeatType: 'reverse' }}
      />
      {isActive && !isError && (
        <motion.div
          className="absolute inset-y-0 w-8 rounded-full bg-gradient-to-r from-transparent via-amber-200/40 to-transparent"
          animate={{ x: ['-100%', '200%'] }}
          transition={{ duration: 1.4, repeat: Infinity, ease: 'linear' }}
        />
      )}
    </div>
  )
}

function HopNode({
  hop,
  server,
  status,
  index,
}: {
  hop: ChainHop
  server?: VpsServer
  status: HopDeployStatus
  index: number
}) {
  const { t } = useI18n()

  const labels: Record<HopDeployStatus, string> = {
    waiting: t('common.waiting'),
    deploying: t('common.installing'),
    done: t('common.done'),
    error: t('common.error'),
  }

  return (
    <div className="flex flex-col items-center shrink-0 min-w-[4.25rem] sm:min-w-[5rem] px-0.5">
      <div className="relative flex items-center justify-center">
        {status === 'deploying' && (
          <motion.span
            className="absolute inset-0 rounded-full bg-amber-400/30"
            animate={{ scale: [1, 1.8], opacity: [0.5, 0] }}
            transition={{ duration: 1.2, repeat: Infinity }}
          />
        )}
        {status === 'waiting' && (
          <motion.span
            className="absolute inset-0 rounded-full bg-amber-400/20"
            animate={{ opacity: [0.35, 0.75, 0.35] }}
            transition={{ duration: 1.6, repeat: Infinity }}
          />
        )}
        <motion.div
          className={cn(
            'relative z-10 w-4 h-4 rounded-full border-2 flex items-center justify-center',
            status === 'done' && 'bg-emerald-400 border-emerald-300 shadow-[0_0_12px_rgba(52,211,153,0.55)]',
            status === 'deploying' && 'bg-amber-400 border-amber-300 shadow-[0_0_14px_rgba(251,191,36,0.5)]',
            status === 'waiting' && 'bg-amber-400/35 border-amber-400/50',
            status === 'error' && 'bg-red-500 border-red-400 shadow-[0_0_12px_rgba(239,68,68,0.5)]',
          )}
          animate={
            status === 'done'
              ? { scale: [1, 1.15, 1] }
              : status === 'error'
                ? { scale: [1, 1.1, 1] }
                : undefined
          }
          transition={{ duration: 0.35 }}
          key={`${hop.id}-${status}`}
        >
          {status === 'done' && <Check size={10} className="text-black" strokeWidth={3} />}
          {status === 'error' && <X size={10} className="text-white" strokeWidth={3} />}
        </motion.div>
      </div>

      <div className="mt-2 flex flex-col items-center gap-0.5 w-full">
        <ProtocolIcon protocol={hop.protocol} size="xs" withGradient title={PROTOCOLS[hop.protocol].label} />
        <span
          className="text-[10px] text-zinc-500 truncate max-w-full text-center leading-tight"
          title={server?.name}
        >
          {server?.name.split(' ')[0] ?? `#${index + 1}`}
        </span>
        <span
          className={cn(
            'text-[10px] font-medium whitespace-nowrap leading-none',
            status === 'done' && 'text-emerald-400',
            status === 'deploying' && 'text-amber-300',
            status === 'waiting' && 'text-amber-400/70',
            status === 'error' && 'text-red-400',
          )}
        >
          {labels[status]}
        </span>
      </div>
    </div>
  )
}

export function ChainDeployStatusBar({
  hops,
  servers,
  hopStatuses,
  className,
}: {
  hops: ChainHop[]
  servers: VpsServer[]
  hopStatuses: HopDeployStatus[]
  className?: string
}) {
  const { t } = useI18n()

  if (hops.length < 2) return null

  return (
    <div className={cn('rounded-xl border border-white/5 bg-black/20 px-3 py-4 sm:px-4', className)}>
      <p className="text-[10px] font-semibold uppercase tracking-widest text-zinc-500 mb-3 text-center">
        {t('dashboard.deployBar.title')}
      </p>
      <div className="overflow-x-auto pb-1 -mx-1 px-1">
        <div className="flex items-start w-max min-w-full mx-auto justify-center gap-0.5">
        {hops.map((hop, i) => {
          const status = hopStatuses[i] ?? 'waiting'
          const server = servers.find((s) => s.id === hop.serverId)
          return (
            <Fragment key={hop.id}>
              {i > 0 && (
                <div className="flex items-center pt-2 w-10 sm:w-14 shrink-0">
                  <SegmentLine left={hopStatuses[i - 1] ?? 'waiting'} right={status} />
                </div>
              )}
              <HopNode hop={hop} server={server} status={status} index={i} />
            </Fragment>
          )
        })}
        </div>
      </div>
    </div>
  )
}
