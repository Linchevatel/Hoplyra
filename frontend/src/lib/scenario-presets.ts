import type { ChainHop, Protocol, ScenarioId, VpsServer } from './types'
import { createHop } from './chain-utils'

export interface ScenarioPreset {
  id: ScenarioId
  name: string
  description: string
  protocols: Protocol[]
  /** Индексы онлайн-серверов для подстановки */
  serverIndices: number[]
}

export const SCENARIO_PRESETS: ScenarioPreset[] = [
  {
    id: 'bypass',
    name: 'Bypass blocks',
    description: 'Xray + Tor — obfuscation and exit via anonymous network',
    protocols: ['xray', 'tor'],
    serverIndices: [1, 7],
  },
  {
    id: 'streaming',
    name: 'Streaming',
    description: 'AWG or WireGuard — minimal latency for video',
    protocols: ['awg', 'wg'],
    serverIndices: [0, 2],
  },
  {
    id: 'privacy',
    name: 'Maximum privacy',
    description: 'AWG → Xray → Tor — layered anonymization',
    protocols: ['awg', 'xray', 'tor'],
    serverIndices: [0, 1, 7],
  },
]

export function hopsFromScenario(
  scenario: ScenarioPreset,
  onlineServers: VpsServer[],
): ChainHop[] {
  if (onlineServers.length === 0) return []
  return scenario.protocols.map((protocol, i) => {
    const idx = scenario.serverIndices[i] ?? i % onlineServers.length
    const server = onlineServers[idx] ?? onlineServers[i % onlineServers.length]
    return createHop(protocol, server.id)
  })
}
