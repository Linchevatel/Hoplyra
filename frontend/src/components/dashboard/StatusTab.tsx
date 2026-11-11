import { AppIcon, type AppIconName } from '@/components/ui/AppIcon'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { PageHeader } from '@/components/ui/PageHeader'
import {
  Monitor,
  Radio,
} from 'lucide-react'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { DonutChart, LoadBars, MetricRingsRow, NetworkThroughput } from '@/components/ui/MetricCharts'
import { useDashboard } from '@/lib/dashboard-context'
import { useI18n } from '@/i18n/i18n-context'
import * as api from '@/lib/api'
import type { ServerMetrics } from '@/lib/types'
import {
  diskUsedPercent,
  formatBytes,
  formatBitrate,
  formatUptime,
  hasLiveMetrics,
  isControlMetric,
  loadMetricsCache,
  memoryUsedPercent,
  metricColor,
  reconcileMetrics,
  saveMetricsCache,
} from '@/lib/server-metrics'
import { getLocationFlag } from '@/lib/country-utils'
import { cn, formatLatencyMs } from '@/lib/utils'

const POLL_MS = 1_000

function SectionDivider() {
  return <div className="h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />
}

function ServerMetricsCard({
  metrics,
  serverStatus,
  location,
  isControl = false,
}: {
  metrics: ServerMetrics
  serverStatus?: string
  location?: string
  isControl?: boolean
}) {
  const { t } = useI18n()
  const hasData = hasLiveMetrics(metrics)
  const cpu = metrics.cpuPercent ?? 0
  const memPct = memoryUsedPercent(metrics)
  const diskPct = diskUsedPercent(metrics)
  const ringSize = isControl ? 96 : 88

  return (
    <Card
      className={cn(
        'overflow-hidden border-white/[0.06]',
        isControl && 'border-accent-cyan/25 shadow-[inset_3px_0_0_0_color-mix(in_srgb,var(--color-accent-cyan)_55%,transparent)]',
      )}
    >
      <div
        className={cn(
          'flex items-start justify-between gap-3 px-5 pt-5 pb-4 border-b border-white/[0.06]',
          isControl && 'bg-accent-cyan/[0.04]',
        )}
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2.5">
            <div
              className={cn(
                'flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-white/[0.06] bg-white/[0.04]',
                isControl && 'border-accent-cyan/20 bg-accent-cyan/10',
              )}
            >
              {isControl ? (
                <Monitor size={16} className="text-accent-cyan" />
              ) : (
                <span className="text-base leading-none">{getLocationFlag(location)}</span>
              )}
            </div>
            <div className="min-w-0">
              <h3 className="font-semibold truncate">
                {isControl ? t('dashboard.status.controlHost') : metrics.name}
              </h3>
              <p className="text-xs text-text-muted font-mono mt-0.5 truncate">{metrics.host}</p>
            </div>
          </div>
        </div>
        <Badge variant={hasData ? 'success' : metrics.error ? 'error' : 'warning'}>
          {metrics.cached || (metrics.online && !hasData && !metrics.error) ? t('ui.cached') : hasData ? t('status.online') : t('status.offline')}
        </Badge>
      </div>

      <div className="p-5 space-y-4">
        {!hasData ? (
          <div className="rounded-xl border border-rose-500/15 bg-rose-500/[0.04] px-4 py-6 text-center">
            <p className="text-sm text-rose-200/90">
              {metrics.error || serverStatus || t('dashboard.status.unavailable')}
            </p>
          </div>
        ) : (
          <>
            <MetricRingsRow>
              <DonutChart
                value={cpu}
                label={t('dashboard.status.cpu')}
                color="cyan"
                size={ringSize}
                stroke={9}
                decimals={1}
                sublabel={`${cpu.toFixed(1)}%`}
              />
              <DonutChart
                value={memPct}
                label={t('dashboard.status.memory')}
                color="violet"
                size={ringSize}
                stroke={9}
                sublabel={formatBytes(metrics.memoryUsedBytes)}
              />
              <DonutChart
                value={diskPct}
                label={t('dashboard.status.disk')}
                color={metricColor(diskPct)}
                size={ringSize}
                stroke={9}
                sublabel={formatBytes(metrics.diskUsedBytes)}
              />
            </MetricRingsRow>

            <SectionDivider />

            <LoadBars
              load1={metrics.load1}
              load5={metrics.load5}
              load15={metrics.load15}
              label={t('dashboard.status.loadAverage')}
            />

            <NetworkThroughput
              rxBps={metrics.networkRxBps}
              txBps={metrics.networkTxBps}
              label={t('dashboard.status.network')}
              downLabel={t('dashboard.status.networkDown')}
              upLabel={t('dashboard.status.networkUp')}
              format={formatBitrate}
            />

            <SectionDivider />

            <div
              className={cn(
                'grid gap-2.5',
                isControl ? 'grid-cols-3' : 'grid-cols-2 sm:grid-cols-4',
              )}
            >
              <StatPill
                iconName="activity2"
                label={t('dashboard.status.uptime')}
                value={formatUptime(metrics.uptimeSeconds)}
              />
              {!isControl && (
                <StatPill
                  iconName="latency2"
                  label={t('dashboard.status.latency')}
                  value={formatLatencyMs(metrics.latencyMs)}
                />
              )}
              <StatPill
                iconName="servers"
                label={t('dashboard.status.containers')}
                value={metrics.containerCount != null ? String(metrics.containerCount) : '—'}
              />
              <StatPill
                iconName="bandwidth2"
                label={t('dashboard.status.diskFree')}
                value={formatBytes(metrics.diskAvailableBytes)}
              />
            </div>
          </>
        )}
      </div>
    </Card>
  )
}

function StatPill({
  iconName,
  label,
  value,
}: {
  iconName: AppIconName
  label: string
  value: string
}) {
  return (
    <div className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-text-muted mb-1">
        <AppIcon name={iconName} size={28} />
        <span className="text-[10px] uppercase tracking-wide">{label}</span>
      </div>
      <p className="text-sm font-semibold tabular-nums text-zinc-200 truncate">{value}</p>
    </div>
  )
}

export function StatusTab() {
  const { t } = useI18n()
  const { servers } = useDashboard()
  const [metrics, setMetrics] = useState<ServerMetrics[]>(loadMetricsCache)
  const metricsRef = useRef(metrics)
  const [streamLive, setStreamLive] = useState(false)
  const [now, setNow] = useState(Date.now)
  // Cache freshness must expire even when a metrics request stalls.
  useEffect(() => {
    let timer: number
    const tick = () => { setNow(Date.now()); timer = window.setTimeout(tick, POLL_MS) }
    timer = window.setTimeout(tick, POLL_MS)
    return () => window.clearTimeout(timer)
  }, [])

  const ingestMetrics = useCallback(
    (rows: ServerMetrics[], allowMock = false) => {
      const merged = reconcileMetrics(rows, metricsRef.current, servers, allowMock)
      metricsRef.current = merged
      saveMetricsCache(merged)
      setMetrics(merged)
    },
    [servers],
  )

  useEffect(() => {
    let closed = false
    let inFlight = false
    let revision = 0
    let pollTimer: number | undefined
    const refreshOnce = async () => {
      if (closed || inFlight) return
      inFlight = true
      const startedRevision = revision
      try {
        const rows = await api.fetchServersMetrics()
        if (!closed && startedRevision === revision) ingestMetrics(rows, false)
      } catch {
        if (!closed && startedRevision === revision) ingestMetrics([], false)
      } finally {
        inFlight = false
        if (!closed) pollTimer = window.setTimeout(() => void refreshOnce(), POLL_MS)
      }
    }
    let unsubscribe: (() => void) | undefined
    void api.apiHealth().then((ok) => {
      if (closed) return
      void refreshOnce()
      if (!ok) return
      unsubscribe = api.subscribeMetricsStream(
      (payload) => {
        if (closed) return
        revision += 1
        setStreamLive(true)
        ingestMetrics(payload.metrics, false)
      },
      () => { if (!closed) setStreamLive(false) },
      )
    })
    return () => {
      closed = true
      unsubscribe?.()
      if (pollTimer) window.clearTimeout(pollTimer)
    }
  }, [servers, ingestMetrics])

  const controlMetric = useMemo(
    () => metrics.find(isControlMetric) ?? null,
    [metrics],
  )
  const vpsMetrics = useMemo(
    () => metrics.filter((row) => !isControlMetric(row)),
    [metrics],
  )

  const summary = useMemo(() => {
    const nodes = metrics.filter((m) => hasLiveMetrics(m, now) && !m.cached)
    const online = nodes.length
    const avgCpu =
      nodes.length > 0
        ? nodes.reduce((acc, m) => acc + (m.cpuPercent ?? 0), 0) / nodes.length
        : 0
    const avgMem =
      nodes.length > 0
        ? nodes.reduce((acc, m) => acc + memoryUsedPercent(m), 0) / nodes.length
        : 0
    const totalNodes = (controlMetric ? 1 : 0) + servers.length
    return { online, totalNodes, avgCpu, avgMem }
  }, [metrics, controlMetric, servers.length, now])

  const statusById = useMemo(
    () => Object.fromEntries(servers.map((s) => [s.id, s.status])),
    [servers],
  )
  const locationById = useMemo(
    () => Object.fromEntries(servers.map((s) => [s.id, s.location])),
    [servers],
  )

  return (
    <div className="dashboard-page">
      <PageHeader title={t('nav.status')} subtitle={t('dashboard.status.subtitle')}>
        {streamLive && (
          <span className="inline-flex items-center gap-1.5 text-xs text-emerald-400">
            <Radio size={12} className="animate-pulse" />
            {t('dashboard.status.liveStream')}
          </span>
        )}
      </PageHeader>

      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        <SummaryTile
          iconName="security2"
          label={t('dashboard.status.serversOnline')}
          value={`${summary.online}/${summary.totalNodes}`}
          color="text-accent-cyan"
        />
        <SummaryTile
          iconName="activity2"
          label={t('dashboard.status.avgCpu')}
          value={`${summary.avgCpu.toFixed(1)}%`}
          color="text-accent-violet"
        />
        <SummaryTile
          iconName="bandwidth2"
          label={t('dashboard.status.avgMemory')}
          value={`${summary.avgMem.toFixed(0)}%`}
          color="text-emerald-400"
        />
      </div>

      {servers.length === 0 && !controlMetric ? (
        <Card className="empty-state p-10"><AppIcon name="activity2" size={28} /><p>{t('dashboard.status.noServers')}</p><p className="text-xs">{t('dashboard.status.subtitle')}</p></Card>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {controlMetric && (
            <ServerMetricsCard metrics={controlMetric} isControl />
          )}
          {vpsMetrics.map((row) => (
            <ServerMetricsCard
              key={row.serverId}
              metrics={row}
              serverStatus={statusById[row.serverId]}
              location={locationById[row.serverId]}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function SummaryTile({
  iconName,
  label,
  value,
  color,
}: {
  iconName: AppIconName
  label: string
  value: string
  color: string
}) {
  return (
    <Card className="p-4">
      <div className="flex items-center gap-3">
        <div className={cn('flex h-10 w-10 items-center justify-center rounded-xl bg-white/[0.04]', color)}>
          <AppIcon name={iconName} size={32} />
        </div>
        <div>
          <p className="text-xs text-text-muted">{label}</p>
          <p className="text-lg font-bold tabular-nums">{value}</p>
        </div>
      </div>
    </Card>
  )
}
