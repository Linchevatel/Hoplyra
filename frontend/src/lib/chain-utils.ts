import { t } from '@/i18n/core'
import type { ChainHop, Protocol, VpnConfig, VpsServer, UserChainTemplate } from './types'
import { PROTOCOLS } from './constants'
import type { ChainTemplate } from './constants'
import { generateId } from './utils'

export function findActiveChainConfig(serverId: string, configs: VpnConfig[]): VpnConfig | undefined {
  return configs.find(
    (c) =>
      c.hops &&
      c.hops.length >= 2 &&
      (c.status === 'active' || c.status === 'deploying') &&
      c.hops.some((h) => h.serverId === serverId),
  )
}

export function isActiveChainConfig(config: VpnConfig): boolean {
  return Boolean(config.hops && config.hops.length >= 2 && config.status === 'active')
}

export function getChainLockedServerIds(configs: VpnConfig[]): Set<string> {
  const locked = new Set<string>()
  for (const config of configs) {
    if (!config.hops || config.hops.length < 2) continue
    if (config.status !== 'active') continue
    for (const hop of config.hops) {
      if (hop.serverId) locked.add(hop.serverId)
    }
  }
  return locked
}

export function isServerLockedByActiveChain(serverId: string, configs: VpnConfig[]): boolean {
  return getChainLockedServerIds(configs).has(serverId)
}

export function getHopRole(hops: ChainHop[], serverId: string): 'entry' | 'relay' | 'exit' | null {
  const index = hops.findIndex((h) => h.serverId === serverId)
  if (index === -1) return null
  if (index === 0) return 'entry'
  if (index === hops.length - 1) return 'exit'
  return 'relay'
}

export function createHop(protocol: Protocol, serverId: string): ChainHop {
  return {
    id: generateId(),
    protocol,
    serverId,
    ...(protocol === 'xray' ? { xrayBypass: true } : {}),
  }
}

export function formatHop(hop: ChainHop, servers: VpsServer[]): string {
  const server = servers.find((s) => s.id === hop.serverId)
  return `${PROTOCOLS[hop.protocol].shortLabel} · ${server?.name ?? '—'}`
}

export function formatChainHops(hops: ChainHop[], servers: VpsServer[]): string {
  return hops.map((hop) => formatHop(hop, servers)).join(' → ')
}

export function formatChainProtocols(hops: ChainHop[]): string {
  return hops.map((hop) => PROTOCOLS[hop.protocol].shortLabel).join(' → ')
}

export function getChainEndpointHosts(
  hops: ChainHop[],
  servers: VpsServer[],
): { entry: string; exit: string } {
  if (hops.length === 0) return { entry: '—', exit: '—' }
  const entryServer = servers.find((s) => s.id === hops[0].serverId)
  const exitServer = servers.find((s) => s.id === hops[hops.length - 1].serverId)
  return {
    entry: entryServer?.host ?? '—',
    exit: exitServer?.host ?? '—',
  }
}

/** Два соседних звена на одном VPS — только если хотя бы одно из них Tor */
export function isSameServerPairAllowed(a: ChainHop, b: ChainHop): boolean {
  if (a.serverId !== b.serverId) return true
  return a.protocol === 'tor' || b.protocol === 'tor'
}

export function getInvalidConsecutivePairs(hops: ChainHop[]): number[] {
  const invalid: number[] = []
  for (let i = 0; i < hops.length - 1; i++) {
    if (!isSameServerPairAllowed(hops[i], hops[i + 1])) {
      invalid.push(i)
    }
  }
  return invalid
}

export function getBlockedProtocolsForHop(hopIndex: number, _total: number): Protocol[] {
  if (hopIndex === 0) return ['tor']
  return []
}

export function getDisabledServersForHop(hops: ChainHop[], hopIndex: number): string[] {
  const hop = hops[hopIndex]
  const disabled: string[] = []

  const prev = hops[hopIndex - 1]
  if (prev?.serverId && prev.protocol !== 'tor' && hop.protocol !== 'tor') {
    disabled.push(prev.serverId)
  }

  const next = hops[hopIndex + 1]
  if (next?.serverId && next.protocol !== 'tor' && hop.protocol !== 'tor') {
    disabled.push(next.serverId)
  }

  return disabled
}

export function pickDefaultServerForNewHop(
  hops: ChainHop[],
  onlineServers: VpsServer[],
  protocol: Protocol,
  chainLockedServerIds: Set<string> = new Set(),
): string {
  const unlocked = onlineServers.filter((s) => !chainLockedServerIds.has(s.id))
  const pool = hops.length === 0 ? onlineServers : unlocked.length > 0 ? unlocked : onlineServers
  if (pool.length === 0) return ''
  if (hops.length === 0) return pool[0].id

  const prev = hops[hops.length - 1]
  if (protocol === 'tor' || prev.protocol === 'tor') {
    const sameServer =
      pool.find((s) => s.id === prev.serverId) ??
      unlocked.find((s) => s.id === prev.serverId)
    if (sameServer) return sameServer.id
    return pool[0]?.id ?? ''
  }

  const alternative = pool.find((s) => s.id !== prev.serverId)
  return alternative?.id ?? pool[0].id
}

export function getHopDisplayRole(hop: ChainHop, index: number, total: number): string {
  if (index === 0) return t('dashboard.hopRole.entry')
  if (index === total - 1 && hop.protocol === 'tor') return t('dashboard.hopRole.exitTor')
  if (index === total - 1) return t('dashboard.hopRole.exit')
  if (hop.protocol === 'tor') return t('dashboard.hopRole.transportTor')
  return t('dashboard.hopRole.relay')
}

export function chainEndsWithTor(hops: ChainHop[]): boolean {
  return hops[hops.length - 1]?.protocol === 'tor'
}

export function getChainValidationError(hops: ChainHop[], servers: VpsServer[], configs: VpnConfig[] = []): string | null {
  if (hops.length < 2) return null

  if (hops[0]?.protocol === 'tor') {
    return t('dashboard.chainValidation.torFirst')
  }

  const locked = getChainLockedServerIds(configs)
  for (const hop of hops) {
    if (hop.serverId && locked.has(hop.serverId)) {
      const name = servers.find((s) => s.id === hop.serverId)?.name ?? t('dashboard.history.server')
      return t('dashboard.chainValidation.serverLocked', { name })
    }
  }

  const invalidIndices = getInvalidConsecutivePairs(hops)
  if (invalidIndices.length === 0) return null

  const pairIndex = invalidIndices[0]
  const a = hops[pairIndex]
  const serverName = servers.find((s) => s.id === a.serverId)?.name ?? t('dashboard.history.server')

  return t('dashboard.chainValidation.sameVps', {
    a: pairIndex + 1,
    b: pairIndex + 2,
    name: serverName,
  })
}

export function isChainValid(hops: ChainHop[], servers: VpsServer[], configs: VpnConfig[] = []): boolean {
  if (hops.length < 2) return false
  if (getChainValidationError(hops, servers, configs)) return false
  const hopsValid = hops.every(
    (hop) =>
      hop.serverId &&
      hop.protocol &&
      servers.some((s) => s.id === hop.serverId && s.status === 'online'),
  )
  if (!hopsValid) return false
  return getInvalidConsecutivePairs(hops).length === 0
}

export function sanitizeChainDraft(hops: ChainHop[], servers: VpsServer[]): ChainHop[] {
  return hops.filter(
    (hop) =>
      hop.id &&
      hop.protocol &&
      hop.serverId &&
      servers.some((s) => s.id === hop.serverId),
  )
}

export function hopsFromBuiltInTemplate(
  template: ChainTemplate,
  onlineServers: VpsServer[],
  chainLockedServerIds: Set<string> = new Set(),
): ChainHop[] {
  const unlocked = onlineServers.filter((s) => !chainLockedServerIds.has(s.id))
  const pool = unlocked.length > 0 ? unlocked : onlineServers
  if (pool.length === 0) return []
  return template.hops.map((protocol, i) => {
    const idx = template.serverIndices?.[i] ?? i % pool.length
    const server = pool[idx] ?? pool[0]
    return createHop(protocol, server.id)
  })
}

export function hopsFromUserTemplate(
  template: UserChainTemplate,
  onlineServers: VpsServer[],
  chainLockedServerIds: Set<string> = new Set(),
): ChainHop[] {
  const unlocked = onlineServers.filter((s) => !chainLockedServerIds.has(s.id))
  const pool = unlocked.length > 0 ? unlocked : onlineServers
  if (pool.length === 0) return []
  return template.hops.map((hop, i) => {
    const preferred =
      pool.find((s) => s.id === hop.serverId && s.status === 'online') ??
      pool[i % pool.length]
    const created = createHop(hop.protocol, preferred.id)
    return hop.transport || hop.xrayBypass
      ? {
          ...created,
          ...(hop.transport ? { transport: hop.transport } : {}),
          ...(hop.xrayBypass ? { xrayBypass: true } : {}),
        }
      : created
  })
}

export function userTemplateFromHops(name: string, hops: ChainHop[]): UserChainTemplate {
  return {
    id: generateId(),
    name: name.trim() || formatChainProtocols(hops),
    hops: hops.map((h) => ({
      protocol: h.protocol,
      serverId: h.serverId,
      ...(h.protocol === 'openvpn' && h.transport ? { transport: h.transport } : {}),
      ...(h.protocol === 'xray' && h.xrayBypass ? { xrayBypass: true } : {}),
    })),
    createdAt: new Date().toISOString(),
  }
}

export function getServerChainLabel(
  server: VpsServer,
  servers: VpsServer[],
  configs: VpnConfig[] = [],
): string | null {
  if (server.activeChainHops?.length) {
    return formatChainHops(server.activeChainHops, servers)
  }

  const config = findActiveChainConfig(server.id, configs)
  if (config?.hops) {
    return formatChainHops(config.hops, servers)
  }

  if (server.activeChain?.length) {
    return server.activeChain.map((p) => PROTOCOLS[p].shortLabel).join(' → ')
  }

  return null
}
