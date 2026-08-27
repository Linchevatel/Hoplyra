import * as api from './api'
import type { ChainHop, Protocol, VpnConfig } from './types'

export const CONFIG_POLL_MS = 2000
export const PENDING_CHAIN_DEPLOY_KEY = 'hoplyra-pending-chain-deploy'
const DEFAULT_TIMEOUT_MS = 45 * 60 * 1000

export function hasDeployingConfigs(configs: VpnConfig[]): boolean {
  return configs.some((c) => c.status === 'deploying')
}

export function hasPendingChainDeploy(): boolean {
  return readPendingChainDeploy() !== null
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export function chainHopsMatch(config: VpnConfig, hops: ChainHop[]): boolean {
  if (!config.hops || config.hops.length !== hops.length) return false
  return config.hops.every(
    (h, i) => h.serverId === hops[i].serverId && h.protocol === hops[i].protocol,
  )
}

export function findMatchingChainConfig(
  configs: VpnConfig[],
  hops: ChainHop[],
): VpnConfig | undefined {
  const entryId = hops[0]?.serverId
  if (!entryId) return undefined
  return configs.find((c) => c.serverId === entryId && chainHopsMatch(c, hops))
}

export function savePendingChainDeploy(hops: ChainHop[]): void {
  try {
    sessionStorage.setItem(
      PENDING_CHAIN_DEPLOY_KEY,
      JSON.stringify({ hops, ts: Date.now() }),
    )
  } catch {
    /* ignore quota / private mode */
  }
}

export function clearPendingChainDeploy(): void {
  try {
    sessionStorage.removeItem(PENDING_CHAIN_DEPLOY_KEY)
  } catch {
    /* ignore */
  }
}

export function readPendingChainDeploy(): ChainHop[] | null {
  try {
    const raw = sessionStorage.getItem(PENDING_CHAIN_DEPLOY_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { hops?: ChainHop[]; ts?: number }
    if (!parsed.hops?.length) return null
    if (parsed.ts && Date.now() - parsed.ts > DEFAULT_TIMEOUT_MS) {
      clearPendingChainDeploy()
      return null
    }
    return parsed.hops
  } catch {
    return null
  }
}

export async function waitForConfigDeploy(
  configId: string,
  options?: { timeoutMs?: number; onUpdate?: (cfg: VpnConfig) => void },
): Promise<VpnConfig> {
  const timeoutMs = options?.timeoutMs ?? DEFAULT_TIMEOUT_MS
  const started = Date.now()
  let missingPolls = 0
  while (Date.now() - started < timeoutMs) {
    const configs = await api.fetchConfigs()
    const cfg = configs.find((c) => c.id === configId)
    if (!cfg) {
      missingPolls += 1
      if (missingPolls >= 5) {
        throw new Error('Deploy failed')
      }
      await sleep(CONFIG_POLL_MS)
      continue
    }
    missingPolls = 0
    options?.onUpdate?.(cfg)
    if (cfg.status === 'active') {
      return cfg
    }
    if (cfg.status === 'error') {
      throw new Error(cfg.statusMessage ?? 'Deploy failed')
    }
    await sleep(CONFIG_POLL_MS)
  }
  throw new Error('Deploy timeout')
}

export async function waitForMatchingChainDeploy(
  hops: ChainHop[],
  options?: { timeoutMs?: number; onUpdate?: (cfg: VpnConfig) => void },
): Promise<VpnConfig | null> {
  const timeoutMs = options?.timeoutMs ?? DEFAULT_TIMEOUT_MS
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    try {
      const configs = await api.fetchConfigs()
      const match = findMatchingChainConfig(configs, hops)
      if (match) {
        options?.onUpdate?.(match)
        if (match.status === 'active') {
          return match
        }
        if (match.status === 'error') {
          throw new Error(match.statusMessage ?? 'Deploy failed')
        }
      }
    } catch (err) {
      if (err instanceof Error && err.message !== 'Deploy failed' && !err.message.includes('HTTP')) {
        throw err
      }
    }
    await sleep(CONFIG_POLL_MS)
  }
  return null
}

export async function recoverDeployedConfig(
  serverId: string,
  protocol: Protocol,
): Promise<VpnConfig | null> {
  try {
    const configs = await api.fetchConfigs()
    return (
      configs.find(
        (c) => c.serverId === serverId && c.protocol === protocol && c.status === 'active',
      ) ?? null
    )
  } catch {
    return null
  }
}

export async function recoverDeployedChain(hops: ChainHop[]): Promise<VpnConfig | null> {
  try {
    const configs = await api.fetchConfigs()
    const match = findMatchingChainConfig(configs, hops)
    return match?.status === 'active' ? match : null
  } catch {
    return null
  }
}
