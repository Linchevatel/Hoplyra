import type { ServerMetrics, VpsServer } from './types'

export const CONTROL_SERVER_ID = '__control__'
const METRICS_CACHE_KEY = 'hoplyra_metrics_cache_v1'

export function isControlMetric(metrics: ServerMetrics): boolean {
  return metrics.isControl === true || metrics.serverId === CONTROL_SERVER_ID
}

export function loadMetricsCache(): ServerMetrics[] {
  try {
    const raw = sessionStorage.getItem(METRICS_CACHE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as ServerMetrics[]
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export function saveMetricsCache(rows: ServerMetrics[]): void {
  try {
    sessionStorage.setItem(METRICS_CACHE_KEY, JSON.stringify(rows))
  } catch {
    /* quota / private mode */
  }
}

export function formatBytes(bytes?: number | null): string {
  if (bytes == null || bytes <= 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value >= 10 || unit === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`
}

export function formatBitrate(bps?: number | null): string {
  if (bps == null || bps < 0) return '—'
  const bitsPerSec = bps * 8
  if (bitsPerSec < 1000) return `${bitsPerSec.toFixed(0)} bit/s`
  if (bitsPerSec < 1_000_000) return `${(bitsPerSec / 1000).toFixed(1)} Kbit/s`
  if (bitsPerSec < 1_000_000_000) return `${(bitsPerSec / 1_000_000).toFixed(2)} Mbit/s`
  return `${(bitsPerSec / 1_000_000_000).toFixed(2)} Gbit/s`
}

export function formatUptime(seconds?: number | null): string {
  if (seconds == null || seconds <= 0) return '—'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const mins = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${mins}m`
  return `${mins}m`
}

export function memoryUsedPercent(metrics: ServerMetrics): number {
  if (!metrics.memoryTotalBytes || metrics.memoryUsedBytes == null) return 0
  return (metrics.memoryUsedBytes / metrics.memoryTotalBytes) * 100
}

export function diskUsedPercent(metrics: ServerMetrics): number {
  if (!metrics.diskTotalBytes || metrics.diskUsedBytes == null) return 0
  return (metrics.diskUsedBytes / metrics.diskTotalBytes) * 100
}

export function metricColor(percent: number): 'emerald' | 'amber' | 'rose' {
  if (percent >= 90) return 'rose'
  if (percent >= 75) return 'amber'
  return 'emerald'
}

export function emptyServerMetrics(server: VpsServer, error?: string | null): ServerMetrics {
  return {
    serverId: server.id,
    name: server.name,
    host: server.host,
    latencyMs: server.latencyMs,
    online: false,
    collectedAt: new Date().toISOString(),
    uptimeSeconds: null,
    load1: null,
    load5: null,
    load15: null,
    cpuPercent: null,
    memoryTotalBytes: null,
    memoryUsedBytes: null,
    memoryAvailableBytes: null,
    diskTotalBytes: null,
    diskUsedBytes: null,
    diskAvailableBytes: null,
    containerCount: null,
    networkRxBps: null,
    networkTxBps: null,
    error: error ?? server.statusMessage ?? 'offline',
  }
}

export function mockServerMetrics(server: VpsServer): ServerMetrics {
  const seed = server.id.split('').reduce((acc, ch) => acc + ch.charCodeAt(0), 0)
  const online = server.status === 'online'
  const base = online ? 18 + (seed % 45) : 0
  const memTotal = 4 * 1024 ** 3
  const memUsed = memTotal * ((base + 20) / 100)
  const diskTotal = 40 * 1024 ** 3
  const diskUsed = diskTotal * ((base + 10) / 100)

  return {
    serverId: server.id,
    name: server.name,
    host: server.host,
    latencyMs: server.latencyMs,
    online,
    collectedAt: new Date().toISOString(),
    uptimeSeconds: online ? 86400 + seed * 37 : null,
    load1: online ? 0.2 + (seed % 10) / 10 : null,
    load5: online ? 0.15 + (seed % 8) / 10 : null,
    load15: online ? 0.1 + (seed % 6) / 10 : null,
    cpuPercent: online ? base : null,
    memoryTotalBytes: online ? memTotal : null,
    memoryUsedBytes: online ? memUsed : null,
    memoryAvailableBytes: online ? memTotal - memUsed : null,
    diskTotalBytes: online ? diskTotal : null,
    diskUsedBytes: online ? diskUsed : null,
    diskAvailableBytes: online ? diskTotal - diskUsed : null,
    containerCount: online ? seed % 4 : null,
    networkRxBps: online ? (seed % 50) * 1024 * 1024 : null,
    networkTxBps: online ? (seed % 30) * 1024 * 1024 : null,
    error: online ? null : server.statusMessage ?? 'offline',
  }
}

export function hasLiveMetrics(metrics: ServerMetrics): boolean {
  return (
    metrics.cpuPercent != null ||
    metrics.memoryTotalBytes != null ||
    metrics.diskTotalBytes != null
  )
}

export function mergeMetricsWithServers(
  servers: VpsServer[],
  rows: ServerMetrics[],
  allowMock = true,
): ServerMetrics[] {
  const byId = Object.fromEntries(rows.map((row) => [row.serverId, row]))
  return servers.map((server) => {
    const row = byId[server.id]
    if (row) {
      return { ...row, name: server.name, host: server.host, latencyMs: row.latencyMs ?? server.latencyMs }
    }
    return allowMock ? mockServerMetrics(server) : emptyServerMetrics(server)
  })
}

export function reconcileMetrics(
  incoming: ServerMetrics[],
  previous: ServerMetrics[],
  servers: VpsServer[],
  allowMock = false,
): ServerMetrics[] {
  const prevById = Object.fromEntries(previous.map((row) => [row.serverId, row]))
  const incomingControl = incoming.find(isControlMetric)
  const previousControl = previous.find(isControlMetric)
  const control = incomingControl ?? previousControl

  const vpsRows = incoming.filter((row) => !isControlMetric(row))
  const mergedVps = mergeMetricsWithServers(servers, vpsRows, allowMock).map((row) => {
    if (hasLiveMetrics(row)) return row
    const cached = prevById[row.serverId]
    if (cached && hasLiveMetrics(cached)) {
      return {
        ...cached,
        name: row.name,
        host: row.host,
        latencyMs: cached.latencyMs ?? row.latencyMs,
      }
    }
    return row
  })

  return control ? [control, ...mergedVps] : mergedVps
}
