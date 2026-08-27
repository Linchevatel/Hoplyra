import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity,
  Cpu,
  HardDrive,
  MemoryStick,
  Monitor,
  Server,
  Container,
  Clock,
  Radio,
} from 'lucide-react'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { DonutChart, LoadBars, MetricRingsRow, NetworkThroughput } from '@/components/ui/MetricCharts'
import { useDashboard } from '@/lib/dashboard'
import { useI18n } from '@/i18n/I18nProvider'
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
        isControl && 'border-cyan-500/25 shadow-[inset_3px_0_0_0_rgba(34,211,238,0.55)]',
      )}
    >
      <div
        className={cn(
          'flex items-start justify-between gap-3 px-5 pt-5 pb-4 border-b border-white/[0.06]',
          isControl && 'bg-cyan-500/[0.04]',
        )}
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2.5">
            <div
              className={cn(
                'flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-white/[0.06] bg-white/[0.04]',
                isControl && 'border-cyan-500/20 bg-cyan-500/10',
              )}
            >
              {isControl ? (
                <Monitor size={16} className="text-cyan-400" />
              ) : (
                <span className="text-base leading-none">{getLocationFlag(location)}</span>
              )}
            </div>
            <div className="min-w-0">
              <h3 className="font-semibold truncate">
                {isControl ? t('dashboard.status.controlHost') : metrics.name}
              </h3>
              <p className="text-xs text-zinc-500 font-mono mt-0.5 truncate">{metrics.host}</p>
            </div>
          </div>
        </div>
        <Badge variant={hasData ? 'success' : metrics.error ? 'error' : 'warning'}>
          {hasData ? t('status.online') : t('status.offline')}
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
                icon={Clock}
                label={t('dashboard.status.uptime')}
                value={formatUptime(metrics.uptimeSeconds)}
              />
              {!isControl && (
                <StatPill
                  icon={Activity}
                  label={t('dashboard.status.latency')}
                  value={formatLatencyMs(metrics.latencyMs)}
                />
              )}
              <StatPill
                icon={Container}
                label={t('dashboard.status.containers')}
                value={metrics.containerCount != null ? String(metrics.containerCount) : '—'}
              />
              <StatPill
                icon={HardDrive}
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
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Cpu
  label: string
  value: string
}) {
  return (
    <div className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-zinc-500 mb-1">
        <Icon size={12} className="text-zinc-600" />
        <span className="text-[10px] uppercase tracking-wide">{label}</span>
      </div>
      <p className="text-sm font-semibold tabular-nums text-zinc-200 truncate">{value}</p>
    </div>
  )
}

export function StatusTab() {
  const { t } = useI18n()
  const { servers } = useDashboard()
  const metricsRef = useRef<ServerMetrics[]>(loadMetricsCache())
  const [metrics, setMetrics] = useState<ServerMetrics[]>(() => metricsRef.current)
  const [streamLive, setStreamLive] = useState(false)

  const ingestMetrics = useCallback(
    (rows: ServerMetrics[], allowMock = false) => {
      const merged = reconcileMetrics(rows, metricsRef.current, servers, allowMock)
      metricsRef.current = merged
      saveMetricsCache(merged)
      setMetrics(merged)
    },
    [servers],
  )

  const refreshOnce = useCallback(async () => {
    if (servers.length === 0 && metricsRef.current.length === 0) {
      setMetrics([])
      return
    }
    try {
      const apiAvailable = await api.apiHealth()
      if (!apiAvailable) {
        ingestMetrics([], false)
        return
      }
      const rows = await api.fetchServersMetrics()
      ingestMetrics(rows, false)
    } catch {
      /* keep cached live metrics on transient errors */
    }
  }, [servers, ingestMetrics])

  useEffect(() => {
    void refreshOnce()
  }, [refreshOnce])

  useEffect(() => {
    let closed = false
    let pollTimer: number | undefined
    let unsubscribe: (() => void) | undefined

    const startPolling = () => {
      setStreamLive(false)
      pollTimer = window.setInterval(() => void refreshOnce(), POLL_MS)
    }

    void api.apiHealth().then((ok) => {
      if (closed || !ok) {
        startPolling()
        return
      }

      unsubscribe = api.subscribeMetricsStream(
        (payload) => {
          if (closed) return
          setStreamLive(true)
          ingestMetrics(payload.metrics, false)
        },
        () => {
          if (closed) return
          unsubscribe?.()
          unsubscribe = undefined
          startPolling()
        },
      )
    })

    return () => {
      closed = true
      unsubscribe?.()
      if (pollTimer) window.clearInterval(pollTimer)
    }
  }, [servers, ingestMetrics, refreshOnce])

  const controlMetric = useMemo(
    () => metrics.find(isControlMetric) ?? null,
    [metrics],
  )
  const vpsMetrics = useMemo(
    () => metrics.filter((row) => !isControlMetric(row)),
    [metrics],
  )

  const summary = useMemo(() => {
    const nodes = metrics.filter((m) => hasLiveMetrics(m))
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
  }, [metrics, controlMetric, servers.length])

  const statusById = useMemo(
    () => Object.fromEntries(servers.map((s) => [s.id, s.status])),
    [servers],
  )
  const locationById = useMemo(
    () => Object.fromEntries(servers.map((s) => [s.id, s.location])),
    [servers],
  )

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">{t('nav.status')}</h1>
          <p className="text-zinc-400 mt-1">{t('dashboard.status.subtitle')}</p>
        </div>
        <div className="flex items-center gap-2">
          {streamLive && (
            <span className="inline-flex items-center gap-1.5 text-xs text-emerald-400">
              <Radio size={12} className="animate-pulse" />
              {t('dashboard.status.liveStream')}
            </span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        <SummaryTile
          icon={Server}
          label={t('dashboard.status.serversOnline')}
          value={`${summary.online}/${summary.totalNodes}`}
          color="text-cyan-400"
        />
        <SummaryTile
          icon={Cpu}
          label={t('dashboard.status.avgCpu')}
          value={`${summary.avgCpu.toFixed(1)}%`}
          color="text-violet-400"
        />
        <SummaryTile
          icon={MemoryStick}
          label={t('dashboard.status.avgMemory')}
          value={`${summary.avgMem.toFixed(0)}%`}
          color="text-emerald-400"
        />
      </div>

      {servers.length === 0 && !controlMetric ? (
        <Card className="p-10 text-center text-zinc-500">{t('dashboard.status.noServers')}</Card>
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
  icon: Icon,
  label,
  value,
  color,
}: {
  icon: typeof Server
  label: string
  value: string
  color: string
}) {
  return (
    <Card className="p-4">
      <div className="flex items-center gap-3">
        <div className={cn('flex h-10 w-10 items-center justify-center rounded-xl bg-white/[0.04]', color)}>
          <Icon size={18} />
        </div>
        <div>
          <p className="text-xs text-zinc-500">{label}</p>
          <p className="text-lg font-bold tabular-nums">{value}</p>
        </div>
      </div>
    </Card>
  )
}
