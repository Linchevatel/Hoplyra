import * as api from './api'
import type { ServerPrepareProgress, VpsServer } from './types'

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

export async function pollServerPrepare(
  serverId: string,
  onUpdate: (progress: ServerPrepareProgress) => void,
): Promise<VpsServer> {
  for (;;) {
    const progress = await api.fetchServerPrepareStatus(serverId)
    onUpdate(progress)
    if (progress.status === 'done') {
      const servers = await api.fetchServers()
      const server = servers.find((s) => s.id === serverId)
      if (server) return server
      throw new Error('Server not found after prepare')
    }
    if (progress.status === 'error') {
      throw new Error(progress.message || 'VPS prepare failed')
    }
    await sleep(1500)
  }
}
