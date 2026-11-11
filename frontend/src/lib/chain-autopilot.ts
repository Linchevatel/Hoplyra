import type { ChainGoal, ChainHop, Protocol, VpsServer } from './types'
import { createHop, isSameServerPairAllowed } from './chain-utils'

function serverLatency(server: VpsServer): number {
  if (server.latencyMs != null) return server.latencyMs
  const hash = server.id.split('').reduce((a, c) => a + c.charCodeAt(0), 0)
  return 40 + (hash % 120)
}


function pickServers(servers: VpsServer[], count: number, goal: ChainGoal): VpsServer[] {
  const online = [...servers.filter((s) => s.status === 'online')]
  if (goal === 'latency') {
    online.sort((a, b) => serverLatency(a) - serverLatency(b))
  } else {
    online.sort((a, b) => {
      const locA = a.location?.includes('SE') || a.location?.includes('Tor') ? 1 : 0
      const locB = b.location?.includes('SE') || b.location?.includes('Tor') ? 1 : 0
      return locB - locA || serverLatency(b) - serverLatency(a)
    })
  }
  return online.slice(0, count)
}

function protocolsForGoal(goal: ChainGoal, hopCount: number): Protocol[] {
  if (goal === 'latency') {
    return hopCount >= 3 ? ['awg', 'wg', 'xray'] : ['awg', 'wg']
  }
  if (hopCount >= 3) return ['awg', 'xray', 'tor', 'xray']
  return ['xray', 'tor']
}

export function buildAutoChain(
  goal: ChainGoal,
  servers: VpsServer[],
  hopCount: 2 | 3 | 4 = 2,
): ChainHop[] {
  const effectiveCount = goal === 'anonymity' && hopCount < 2 ? 2 : hopCount
  const picked = pickServers(servers, effectiveCount, goal)
  if (picked.length < 2) return []

  const protocols = protocolsForGoal(goal, Math.min(effectiveCount, picked.length))
  const hops: ChainHop[] = []

  for (let i = 0; i < Math.min(effectiveCount, picked.length); i++) {
    let protocol = protocols[i] ?? protocols[protocols.length - 1]
    let server = picked[i]

    if (i > 0 && hops[i - 1].serverId === server.id && !isSameServerPairAllowed(hops[i - 1], { ...hops[i - 1], protocol, serverId: server.id })) {
      const alt = picked.find((s) => s.id !== hops[i - 1].serverId)
      if (alt) server = alt
      if (goal === 'anonymity' && protocol !== 'tor') protocol = 'tor'
    }

    hops.push(createHop(protocol, server.id))
  }

  return hops
}
