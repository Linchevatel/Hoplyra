import { AppIcon } from '@/components/ui/AppIcon'
import { useI18n } from '@/i18n/i18n-context'
import type { ServerCensorStatus } from '@/lib/types'
import { cn } from '@/lib/utils'

interface ServerCensorBadgeProps {
  status?: ServerCensorStatus
  className?: string
}

export function ServerCensorBadge({ status, className }: ServerCensorBadgeProps) {
  const { t } = useI18n()

  if (!status) return null

  const subnetLabel = status.rknSubnet ?? 'CIDR'

  // 1. Если IP ТОЧНО в реестре РКН — этот факт первичен и подтвержден базой,
  // даже если исходящая проба не смогла связаться по SSH (ruReachability === 'unknown').
  if (status.rknBlocked) {
    const isProbeFailed = status.ruReachability === 'unknown'
    const labelKey = isProbeFailed
      ? 'dashboard.servers.censorInRegistryProbeFailed'
      : 'dashboard.servers.censorInRegistry'
    const title = isProbeFailed
      ? t('dashboard.servers.censorInRegistryProbeFailedTooltip', {
          subnet: subnetLabel,
          error: status.details?.[0]?.error || 'SSH offline',
        })
      : t('dashboard.servers.censorInRegistryTooltip', { subnet: subnetLabel })

    return (
      <span
        data-testid="censor-badge-rkn"
        className={cn(
          'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-rose-500/20 text-rose-300 border border-rose-500/40 shadow-rose-500/10 shadow-sm transition-all',
          className,
        )}
        title={title}
      >
        <AppIcon name="censor-blocked" size={18} className="drop-shadow-[0_2px_4px_rgba(0,0,0,0.6)]" />
        <span className="font-semibold tracking-tight">{t(labelKey)}</span>
      </span>
    )
  }

  // 2. Если исходящие пробы к РФ потерпели неудачу (blocked) — показываем этот факт,
  // даже если статус реестра неизвестен (rknBlocked === null). Показываем оба факта!
  if (status.ruReachability === 'blocked') {
    const isRegistryUnknown = status.rknBlocked === null || status.rknBlocked === undefined
    const labelKey = isRegistryUnknown
      ? 'dashboard.servers.censorBlockedRegistryUnknown'
      : 'dashboard.servers.censorBlocked'
    const tooltipText = isRegistryUnknown
      ? `${t('dashboard.servers.censorBlockedTooltip')}; ${t('dashboard.servers.censorUnknown')}`
      : t('dashboard.servers.censorBlockedTooltip')
    return (
      <span
        data-testid="censor-badge-blocked"
        className={cn(
          'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-rose-500/20 text-rose-300 border border-rose-500/40 shadow-rose-500/10 shadow-sm transition-all',
          className,
        )}
        title={tooltipText}
      >
        <AppIcon name="censor-blocked" size={18} className="drop-shadow-[0_2px_4px_rgba(0,0,0,0.6)]" />
        <span className="font-semibold tracking-tight">{t(labelKey)}</span>
      </span>
    )
  }

  // 3. Если исходящая проба не смогла выполниться (SSH error, unreachable, python отсутствовал)
  // или если статус реестра неизвестен (пустая база / IPv6) — показываем нейтральный серый бейдж "Не проверено",
  // а не ложно-зеленый "Чист".
  const isProbeUnknown = status.ruReachability === 'unknown'
  const isRegistryUnknown = status.rknBlocked === null || status.rknBlocked === undefined

  if (isProbeUnknown || isRegistryUnknown) {
    const errorMsg =
      status.details?.[0]?.error ||
      (status.rknBlocked === null ? t('dashboard.servers.censorRegistryUnavailable') : t('dashboard.servers.censorUnknown'))
    return (
      <span
        data-testid="censor-badge-unknown"
        className={cn(
          'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-zinc-800/60 text-zinc-400 border border-white/10 shadow-sm transition-all',
          className,
        )}
        title={errorMsg}
      >
        <span className="h-2 w-2 rounded-full bg-zinc-500" />
        <span className="font-semibold tracking-tight">{t('dashboard.servers.censorUnknown')}</span>
      </span>
    )
  }

  // 4. Нестабильная исходящая связь (высокая задержка / частичные потери)
  if (status.ruReachability === 'degraded') {
    const detailsTooltip =
      status.details?.map((d) => `${d.target}: ${d.ok ? `${d.latencyMs}ms` : 'FAIL'}`).join(' | ') ?? ''
    return (
      <span
        data-testid="censor-badge-degraded"
        className={cn(
          'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-amber-500/20 text-amber-300 border border-amber-500/40 shadow-amber-500/10 shadow-sm transition-all',
          className,
        )}
        title={`${t('dashboard.servers.censorOutbound')}: ${detailsTooltip}`}
      >
        <span className="h-2 w-2 rounded-full bg-amber-400" />
        <span className="font-semibold tracking-tight">
          {t('dashboard.servers.censorDegraded')}
          {status.ruLatencyMs ? ` · ${status.ruLatencyMs}ms` : ''}
        </span>
      </span>
    )
  }

  // 5. Чистый IP (с предупреждением о соседней /24 подсети)
  if (status.neighborWarning) {
    const latencyLabel = status.ruLatencyMs ? `${status.ruLatencyMs}ms` : 'OK'
    const neighborTooltip = t('dashboard.servers.censorNeighborTooltip', {
      latency: latencyLabel,
      subnet: status.rknSubnet ?? '',
    })
    return (
      <span
        data-testid="censor-badge-neighbor"
        className={cn(
          'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-500/15 text-emerald-300 border border-emerald-500/35 shadow-emerald-500/10 shadow-sm transition-all',
          className,
        )}
        title={neighborTooltip}
      >
        <AppIcon name="censor-clean" size={18} className="drop-shadow-[0_2px_4px_rgba(0,0,0,0.6)]" />
        <span className="font-semibold tracking-tight">
          {t('dashboard.servers.censorNeighbor')}
          {status.ruLatencyMs ? ` · ${status.ruLatencyMs}ms` : ''}
        </span>
      </span>
    )
  }

  // 6. Полностью чистый IP и подтвержденная исходящая связь
  const detailsTooltip =
    status.details?.map((d) => `${d.target}: ${d.ok ? `${d.latencyMs}ms` : 'FAIL'}`).join(' | ') ?? ''
  return (
    <span
      data-testid="censor-badge-clean"
      className={cn(
        'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-500/15 text-emerald-300 border border-emerald-500/35 shadow-emerald-500/10 shadow-sm transition-all',
        className,
      )}
      title={`${t('dashboard.servers.censorOutbound')}: ${detailsTooltip}`}
    >
      <AppIcon name="censor-clean" size={18} className="drop-shadow-[0_2px_4px_rgba(0,0,0,0.6)]" />
      <span className="font-semibold tracking-tight">
        {t('dashboard.servers.censorClean')}
        {status.ruLatencyMs ? ` · ${status.ruLatencyMs}ms` : ''}
      </span>
    </span>
  )
}
