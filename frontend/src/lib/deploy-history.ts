import { t } from '@/i18n/core'
import type {
  AppNotification,
  ChainHop,
  DeployHistoryEntry,
  DeployHistoryStatus,
  VpnConfig,
  VpsServer,
} from './types'
import { formatChainHops } from './chain-utils'
import { PROTOCOLS } from './constants'
import { generateId } from './utils'

const ORPHAN_STALE_MS = 60 * 60 * 1000

function hopsSignature(hops?: ChainHop[]): string | null {
  if (!hops || hops.length < 2) return null
  return hops.map((h) => `${h.serverId}:${h.protocol}:${h.transport ?? ''}`).join('|')
}

export function deployHistoryIdentity(
  entry: Pick<DeployHistoryEntry, 'type' | 'serverId' | 'protocol' | 'hops'>,
): string {
  return `${entry.type}|${entry.serverId}|${entry.protocol}|${hopsSignature(entry.hops) ?? ''}`
}

export function historyMatchesDeploy(
  a: Pick<DeployHistoryEntry, 'type' | 'serverId' | 'protocol' | 'hops'>,
  b: Pick<DeployHistoryEntry, 'type' | 'serverId' | 'protocol' | 'hops'>,
): boolean {
  return deployHistoryIdentity(a) === deployHistoryIdentity(b)
}

function findMatchingConfig(entry: DeployHistoryEntry, configs: VpnConfig[]): VpnConfig | undefined {
  const candidates = configs.filter((c) => {
    const isChain = entry.type === 'chain'
    const cIsChain = Boolean(c.hops && c.hops.length >= 2)
    if (isChain !== cIsChain) return false
    if (c.serverId !== entry.serverId) return false
    if (!isChain && c.protocol !== entry.protocol) return false
    if (isChain && hopsSignature(entry.hops) !== hopsSignature(c.hops)) return false
    return true
  })

  return candidates.sort(
    (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
  )[0]
}

function finalStatusFromConfig(config: VpnConfig): {
  status: DeployHistoryStatus
  message: string
} {
  if (config.status === 'active') {
    const isChain = Boolean(config.hops && config.hops.length >= 2)
    return {
      status: 'success',
      message: isChain ? t('dashboard.notify.chainActive') : t('dashboard.notify.deployComplete'),
    }
  }
  if (config.status === 'error') {
    return {
      status: 'error',
      message: config.statusMessage ?? t('dashboard.notify.deployError'),
    }
  }
  if (config.status === 'inactive') {
    return { status: 'stopped', message: t('dashboard.notify.stoppedByUser') }
  }
  return { status: 'deploying', message: '' }
}

function reconcileDeployingEntry(
  entry: DeployHistoryEntry,
  configs: VpnConfig[],
  now: number,
): DeployHistoryEntry {
  if (entry.status !== 'deploying') return entry

  const config = findMatchingConfig(entry, configs)
  if (config) {
    const next = finalStatusFromConfig(config)
    if (next.status !== 'deploying') {
      return { ...entry, status: next.status, message: next.message || entry.message }
    }
    return entry
  }

  const age = now - new Date(entry.createdAt).getTime()
  if (age >= ORPHAN_STALE_MS) {
    return {
      ...entry,
      status: 'error',
      message: t('dashboard.history.stale'),
    }
  }

  return entry
}

export function upsertDeployHistory(
  prev: DeployHistoryEntry[],
  partial: Omit<DeployHistoryEntry, 'id' | 'createdAt'>,
): DeployHistoryEntry[] {
  if (partial.status === 'deploying') {
    return [createDeployHistoryEntry(partial), ...prev].slice(0, 100)
  }

  const idx = prev.findIndex(
    (h) => h.status === 'deploying' && historyMatchesDeploy(h, partial),
  )
  if (idx !== -1) {
    const next = [...prev]
    next[idx] = {
      ...next[idx],
      ...partial,
      id: next[idx].id,
      createdAt: next[idx].createdAt,
    }
    return next
  }

  return [createDeployHistoryEntry(partial), ...prev].slice(0, 100)
}

export function reconcileDeployHistory(
  history: DeployHistoryEntry[],
  configs: VpnConfig[],
): DeployHistoryEntry[] {
  const now = Date.now()
  let changed = false

  const reconciled = history.map((entry) => {
    const updated = reconcileDeployingEntry(entry, configs, now)
    if (updated !== entry) changed = true
    return updated
  })

  const deduped = reconciled.filter((entry, index) => {
    if (entry.status !== 'deploying') return true
    const superseded = reconciled
      .slice(0, index)
      .some(
        (other) =>
          other.status !== 'deploying' && historyMatchesDeploy(entry, other),
      )
    if (superseded) changed = true
    return !superseded
  })

  return changed ? deduped : history
}

export function createDeployHistoryEntry(
  partial: Omit<DeployHistoryEntry, 'id' | 'createdAt'>,
): DeployHistoryEntry {
  return {
    ...partial,
    id: generateId(),
    createdAt: new Date().toISOString(),
  }
}

export function createNotification(
  partial: Omit<AppNotification, 'id' | 'read' | 'createdAt'>,
): AppNotification {
  return {
    ...partial,
    id: generateId(),
    read: false,
    createdAt: new Date().toISOString(),
  }
}

export function historyLabel(
  entry: DeployHistoryEntry,
  servers: VpsServer[],
): string {
  if (entry.hops && entry.hops.length >= 2) {
    return formatChainHops(entry.hops, servers)
  }
  const server = servers.find((s) => s.id === entry.serverId)
  return `${PROTOCOLS[entry.protocol].label} · ${server?.name ?? '—'}`
}

export function statusLabel(status: DeployHistoryStatus): string {
  return t(`dashboard.history.${status}`)
}

export function notificationForDeploy(
  config: VpnConfig,
  servers: VpsServer[],
  success: boolean,
): AppNotification {
  const server = servers.find((s) => s.id === config.serverId)
  const isChain = config.hops && config.hops.length >= 2
  return createNotification({
    type: success ? 'deploy_complete' : 'deploy_error',
    title: success
      ? isChain
        ? t('dashboard.history.chainDeployed')
        : t('dashboard.history.vpnDeployed')
      : isChain
        ? t('dashboard.history.chainDeployError')
        : t('dashboard.history.vpnDeployError'),
    message: success
      ? `${server?.name ?? t('dashboard.history.server')} — ${isChain ? formatChainHops(config.hops!, servers) : PROTOCOLS[config.protocol].label}`
      : config.statusMessage
        ? `${server?.name ?? t('dashboard.history.server')}: ${config.statusMessage}`
        : t('dashboard.history.deployFailed', { name: server?.name ?? t('dashboard.history.server') }),
    configId: config.id,
    serverId: config.serverId,
  })
}

export function notificationForOffline(server: VpsServer): AppNotification {
  return createNotification({
    type: 'server_offline',
    title: t('dashboard.history.serverUnavailable'),
    message: t('dashboard.history.wentOffline', { name: server.name, host: server.host }),
    serverId: server.id,
  })
}

export function getInvolvedServerIds(config: VpnConfig): string[] {
  if (config.hops?.length) return [...new Set(config.hops.map((h) => h.serverId))]
  return [config.serverId]
}

export function configUsesAwg(config: VpnConfig): boolean {
  if (config.protocol === 'awg') return true
  return config.hops?.some((h) => h.protocol === 'awg') ?? false
}
