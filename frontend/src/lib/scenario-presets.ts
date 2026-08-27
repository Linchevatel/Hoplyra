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
    name: 'Обход блокировок',
    description: 'Xray + Tor — обфускация и выход через анонимную сеть',
    protocols: ['xray', 'tor'],
    serverIndices: [1, 7],
  },
  {
    id: 'streaming',
    name: 'Стриминг',
    description: 'AWG или WireGuard — минимальная задержка для видео',
    protocols: ['awg', 'wg'],
    serverIndices: [0, 2],
  },
  {
    id: 'privacy',
    name: 'Максимальная приватность',
    description: 'AWG → Xray → Tor — многослойная анонимизация',
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
