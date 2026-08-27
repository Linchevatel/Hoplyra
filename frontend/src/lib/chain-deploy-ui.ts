import type { ChainHop, HopDeployStatus } from './types'
import { chainHopsMatch } from './deploy-recover'
import type { VpnConfig } from './types'
import { initialHopDeployStatus, normalizeHopDeployStatus } from './chain-deploy'

export type ChainDeploySession = {
  hops: ChainHop[]
  configId: string | null
  hopDeployStatus: HopDeployStatus[]
  status: 'deploying' | 'active' | 'error'
  statusMessage?: string
}

export function createChainDeploySession(hops: ChainHop[]): ChainDeploySession {
  return {
    hops,
    configId: null,
    hopDeployStatus: initialHopDeployStatus(hops.length),
    status: 'deploying',
  }
}

export function sessionFromConfig(config: VpnConfig): ChainDeploySession | null {
  if (!config.hops || config.hops.length < 2) return null
  return {
    hops: config.hops,
    configId: config.id === 'pending-chain' ? null : config.id,
    hopDeployStatus: config.hopDeployStatus ?? config.hops.map(() => 'waiting'),
    status:
      config.status === 'active' ? 'active' : config.status === 'error' ? 'error' : 'deploying',
    statusMessage: config.statusMessage,
  }
}

function involvedServerIds(hops: ChainHop[]): Set<string> {
  return new Set(hops.map((h) => h.serverId))
}

export function mergeSocksProxySecrets(apiConfig: VpnConfig, localConfig?: VpnConfig): VpnConfig {
  const apiProxy = apiConfig.socksProxy
  const localProxy = localConfig?.socksProxy
  if (!apiProxy?.enabled || apiProxy.password || !localProxy?.password) {
    return apiConfig
  }
  if (
    localProxy.username !== apiProxy.username ||
    localProxy.host !== apiProxy.host ||
    localProxy.port !== apiProxy.port
  ) {
    return apiConfig
  }
  return {
    ...apiConfig,
    socksProxy: {
      ...apiProxy,
      password: localProxy.password,
      uri: apiProxy.uri ?? localProxy.uri,
    },
  }
}

export function mergeApiConfigsWithLocalDeploy(
  apiConfigs: VpnConfig[],
  localConfigs: VpnConfig[],
  session: ChainDeploySession | null,
): VpnConfig[] {
  let merged = [...apiConfigs]

  if (session?.status === 'deploying') {
    const involved = involvedServerIds(session.hops)
    merged = merged.filter((c) => {
      if (!c.hops || c.hops.length < 2) {
        return !involved.has(c.serverId)
      }
      const touchesInvolved = c.hops.some((h) => involved.has(h.serverId))
      if (!touchesInvolved) return true
      if (session.configId && c.id === session.configId) return true
      if (c.status === 'deploying' && chainHopsMatch(c, session.hops)) return true
      return false
    })
  }

  const seen = new Set(merged.map((c) => c.id))

  for (const local of localConfigs) {
    if (local.id === 'pending-chain') {
      if (!session || session.status !== 'deploying') continue
      if (!merged.some((c) => chainHopsMatch(c, local.hops!))) {
        merged.push(local)
      }
      continue
    }
    if (local.status !== 'deploying' || !local.hops || local.hops.length < 2) continue
    if (seen.has(local.id)) continue
    if (merged.some((c) => chainHopsMatch(c, local.hops!))) continue
    merged.push(local)
  }

  if (session?.status === 'deploying' && session.configId) {
    const idx = merged.findIndex((c) => c.id === session.configId)
    if (idx >= 0) {
      const row = merged[idx]
      const apiStatuses = row.hopDeployStatus
      const sessionStatuses = session.hopDeployStatus
      const hopCount = row.hops?.length ?? session.hops.length
      const mergedStatuses =
        apiStatuses && apiStatuses.length === hopCount
          ? apiStatuses
          : sessionStatuses.length === hopCount
            ? sessionStatuses
            : initialHopDeployStatus(hopCount)
      merged[idx] = {
        ...row,
        hops: row.hops ?? session.hops,
        hopDeployStatus: normalizeHopDeployStatus(mergedStatuses),
        status: 'deploying',
      }
    }
  }

  return merged.map((apiConfig) => {
    const local = localConfigs.find((c) => c.id === apiConfig.id)
    return mergeSocksProxySecrets(apiConfig, local)
  })
}

export function syncSessionFromConfigs(
  session: ChainDeploySession | null,
  configs: VpnConfig[],
): ChainDeploySession | null {
  if (!session || session.status !== 'deploying') return session

  const match =
    (session.configId && configs.find((c) => c.id === session.configId)) ||
    configs.find((c) => chainHopsMatch(c, session.hops) && c.status === 'deploying')

  if (!match) return session

  if (match.status === 'error') {
    return {
      ...session,
      configId: match.id,
      hopDeployStatus: match.hopDeployStatus ?? session.hopDeployStatus,
      status: 'error',
      statusMessage: match.statusMessage,
    }
  }

  return {
    ...session,
    configId: match.id,
    hopDeployStatus: normalizeHopDeployStatus(
      match.hopDeployStatus ?? session.hopDeployStatus,
    ),
    status: 'deploying',
  }
}

export function resolveChainDeployProgress(
  session: ChainDeploySession | null,
  configs: VpnConfig[],
  pendingHops: ChainHop[] | null,
): { hops: ChainHop[]; hopStatuses: HopDeployStatus[] } | null {
  if (session?.status === 'deploying') {
    return {
      hops: session.hops,
      hopStatuses: normalizeHopDeployStatus(
        session.hopDeployStatus ?? initialHopDeployStatus(session.hops.length),
      ),
    }
  }

  const deploying = configs.find(
    (c) => c.hops && c.hops.length >= 2 && c.status === 'deploying',
  )
  if (deploying?.hops) {
    return {
      hops: deploying.hops,
      hopStatuses: normalizeHopDeployStatus(
        deploying.hopDeployStatus ?? initialHopDeployStatus(deploying.hops.length),
      ),
    }
  }

  if (pendingHops && pendingHops.length >= 2) {
    const deployInFlight =
      configs.some((c) => c.hops && c.hops.length >= 2 && c.status === 'deploying')
    if (deployInFlight) {
      return {
        hops: pendingHops,
        hopStatuses: normalizeHopDeployStatus(
          session?.hopDeployStatus ?? initialHopDeployStatus(pendingHops.length),
        ),
      }
    }
  }

  return null
}
