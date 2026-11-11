import type { VpnConfig, VpsServer } from './types'
import { isLocalHost } from './geo-utils'

export function isDemoId(id: string): boolean {
  return id.startsWith('demo-')
}

export function filterRemoteServers(servers: VpsServer[]): VpsServer[] {
  return servers.filter((s) => !isLocalHost(s.host))
}

export function filterRemoteConfigs(configs: VpnConfig[], servers: VpsServer[]): VpnConfig[] {
  const remoteIds = new Set(filterRemoteServers(servers).map((s) => s.id))
  return configs.filter((c) => {
    const ids = c.hops?.length ? c.hops.map((h) => h.serverId) : [c.serverId]
    return ids.every((id) => remoteIds.has(id))
  })
}

export function stripDemoData(servers: VpsServer[], configs: VpnConfig[]) {
  const cleanServers = servers.filter((s) => !isDemoId(s.id))
  const cleanConfigs = configs.filter((c) => {
    if (isDemoId(c.id)) return false
    const ids = c.hops?.length ? c.hops.map((h) => h.serverId) : [c.serverId]
    return ids.every((id) => !isDemoId(id))
  })
  return sanitizeDashboardData(cleanServers, cleanConfigs)
}

export function sanitizeDashboardData(servers: VpsServer[], configs: VpnConfig[]) {
  const remoteServers = filterRemoteServers(servers)
  const remoteConfigs = filterRemoteConfigs(configs, servers)
  return { servers: remoteServers, configs: remoteConfigs }
}
