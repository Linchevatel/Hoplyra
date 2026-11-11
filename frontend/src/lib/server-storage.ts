import type { VpsServer } from './types'

/** Never persist SSH credentials, including unknown fields from legacy records. */
export function serializeServers(servers: VpsServer[]): string {
  return JSON.stringify(servers.map((server) => {
    const safe = { ...server } as VpsServer & { authSecret?: unknown }
    delete safe.authSecret
    return safe
  }))
}

export function loadStoredServers(storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>, key: string): VpsServer[] {
  try {
    const raw = storage.getItem(key)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) throw new Error('Invalid server cache')
    // A cached connection is not evidence that the backend or VPS is reachable.
    const servers = parsed.map((server: VpsServer) => ({
      ...server,
      status: 'offline' as const,
      activeProtocol: undefined,
      activeChainHops: undefined,
      activeChain: undefined,
    }))
    const clean = serializeServers(servers)
    // Scrub legacy secrets immediately, even before React persistence effects run.
    storage.setItem(key, clean)
    return JSON.parse(clean) as VpsServer[]
  } catch {
    // Fail closed if migration cannot safely rewrite the old cache.
    try { storage.removeItem(key) } catch { /* Storage may be unavailable. */ }
    return []
  }
}

export function requireServerBackend(enabled: boolean, message: string): void {
  if (!enabled) throw new Error(message)
}
