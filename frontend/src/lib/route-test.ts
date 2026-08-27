import * as api from './api'
import { isDemoId } from './server-utils'
import { t } from '@/i18n/core'
import type { ChainHop, HopTestResult, RouteTestResult, VpsServer } from './types'

function mockLatency(server: VpsServer): number {
  if (server.latencyMs != null) return server.latencyMs
  const base = server.status === 'online' ? 35 : 999
  const hash = server.host.split('.').reduce((a, p) => a + Number(p), 0)
  return base + (hash % 80)
}

async function testRouteLocal(hops: ChainHop[], servers: VpsServer[]): Promise<RouteTestResult> {
  await new Promise((r) => setTimeout(r, 600 + hops.length * 200))

  const results: HopTestResult[] = []

  for (const hop of hops) {
    const server = servers.find((s) => s.id === hop.serverId)
    if (!server || server.status !== 'online') {
      results.push({
        hopId: hop.id,
        serverId: hop.serverId,
        protocol: hop.protocol,
        reachable: false,
        latencyMs: 0,
        error: server
          ? t('dashboard.routeTestLib.serverUnavailable')
          : t('dashboard.routeTestLib.serverNotFound'),
      })
      continue
    }

    const latencyMs =
      mockLatency(server) + (hop.protocol === 'tor' ? 120 : hop.protocol === 'xray' ? 25 : 10)
    results.push({
      hopId: hop.id,
      serverId: hop.serverId,
      protocol: hop.protocol,
      reachable: true,
      latencyMs,
    })
  }

  const ok = results.every((r) => r.reachable)
  const totalLatencyMs = results.reduce((sum, r) => sum + r.latencyMs, 0)

  return {
    ok,
    totalLatencyMs,
    hops: results,
    testedAt: new Date().toISOString(),
  }
}

export async function testRoute(hops: ChainHop[], servers: VpsServer[]): Promise<RouteTestResult> {
  const canUseApi =
    hops.length >= 2 &&
    hops.every((h) => !isDemoId(h.serverId)) &&
    (await api.apiHealth().catch(() => false))

  if (canUseApi) {
    try {
      return await api.testChainRouteApi(hops)
    } catch {
    }
  }

  return testRouteLocal(hops, servers)
}
