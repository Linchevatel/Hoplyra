import type { Protocol } from './types'

const _PROTOCOLS_RAW: Record<
  Protocol,
  {
    label: string
    shortLabel: string
    description: string
    color: string
    /** Когда протокол уместен — без числовых «бенчмарков» */
    bestFor: string
    /** Сложность первичной настройки */
    setup: 'simple' | 'medium' | 'advanced'
    traits: string[]
    qrConfig: boolean
  }
> = {
  awg: {
    label: 'AmneziaWG',
    shortLabel: 'AWG',
    description: 'Obfuscated WireGuard with DPI protection',
    color: 'from-accent-cyan to-accent-violet',
    bestFor: 'Bypass blocks, everyday VPN',
    setup: 'simple',
    traits: ['DPI bypass', 'WireGuard-based', 'Low latency'],
    qrConfig: true,
  },
  wg: {
    label: 'WireGuard',
    shortLabel: 'WG',
    description: 'Fast, modern VPN protocol',
    color: 'from-accent-cyan to-accent-violet',
    bestFor: 'Streaming, gaming, max speed',
    setup: 'simple',
    traits: ['Minimal overhead', 'Stable connection'],
    qrConfig: true,
  },
  openvpn: {
    label: 'OpenVPN',
    shortLabel: 'OVPN',
    description: 'Time-tested protocol with flexible setup',
    color: 'from-accent-cyan to-accent-violet',
    bestFor: 'Legacy client compatibility',
    setup: 'medium',
    traits: ['TCP and UDP', 'Flexible options', 'Broad support'],
    qrConfig: true,
  },
  xray: {
    label: 'Xray + VLESS',
    shortLabel: 'Xray',
    description: 'Advanced proxy platform with VLESS transport',
    color: 'from-accent-cyan to-accent-violet',
    bestFor: 'Strict DPI, HTTPS disguise',
    setup: 'advanced',
    traits: ['VLESS', 'TLS disguise', 'Flexible transports'],
    qrConfig: true,
  },
  tor: {
    label: 'Tor',
    shortLabel: 'Tor',
    description: 'Transport or exit — internet via Tor with rotating IP',
    color: 'from-accent-cyan to-accent-violet',
    bestFor: 'Anonymity, dynamic exit IP via onion routes',
    setup: 'advanced',
    traits: ['Onion routing', 'Dynamic IP', 'Transport or exit'],
    qrConfig: false,
  },
  hysteria2: {
    label: 'Hysteria 2',
    shortLabel: 'HY2',
    description: 'Ultra-fast QUIC protocol for mobile internet and lossy networks',
    color: 'from-accent-cyan to-accent-violet',
    bestFor: 'Max speed, 4G/5G, bad links',
    setup: 'simple',
    traits: ['QUIC (UDP)', 'Loss resistant', 'Port Hopping'],
    qrConfig: true,
  },
  shadowquic: {
    label: 'ShadowQUIC',
    shortLabel: 'SQ',
    description: '0-RTT QUIC proxy with JLS TLS SNI camouflage and zero cert overhead',
    color: 'from-accent-cyan to-accent-violet',
    bestFor: 'Active DPI evasion, fast UDP/QUIC links, 0-RTT',
    setup: 'simple',
    traits: ['QUIC (UDP)', 'JLS SNI camouflage', 'Zero-RTT'],
    qrConfig: true,
  },
}

const DEFAULT_PROTOCOL_META = {
  label: 'Unknown',
  shortLabel: 'UNK',
  description: 'Unknown protocol',
  color: 'from-accent-cyan to-accent-violet',
  bestFor: 'Alternative option',
  setup: 'simple' as const,
  traits: [] as string[],
  qrConfig: false,
}

export const PROTOCOLS = new Proxy(_PROTOCOLS_RAW, {
  get(target, prop: string) {
    if (prop in target) {
      return target[prop as Protocol]
    }
    return DEFAULT_PROTOCOL_META
  }
}) as typeof _PROTOCOLS_RAW

export const FREE_PROTOCOLS: Protocol[] = ['awg', 'wg', 'openvpn', 'xray', 'tor', 'hysteria2', 'shadowquic']

/** Протоколы для одиночного VPN (без Tor) */
export const VPN_PROTOCOLS: Protocol[] = ['awg', 'wg', 'openvpn', 'xray', 'hysteria2', 'shadowquic']

/** Tor нельзя ставить первым звеном цепи */
export const CHAIN_ENTRY_BLOCKED_PROTOCOLS: Protocol[] = ['tor']

export const SERVER_TAG_SUGGESTIONS = ['prod', 'test', 'relay', 'entry', 'tor-exit'] as const

export interface ChainTemplate {
  name: string
  hops: Protocol[]
  /** Индексы демо-серверов для автоподстановки */
  serverIndices?: number[]
}

export const CHAIN_TEMPLATES: ChainTemplate[] = [
  { name: 'Xray → OpenVPN', hops: ['xray', 'openvpn'], serverIndices: [1, 3] },
  { name: 'Hysteria 2 → Tor', hops: ['hysteria2', 'tor'], serverIndices: [0, 7] },
  { name: 'ShadowQUIC → Tor', hops: ['shadowquic', 'tor'], serverIndices: [1, 7] },
  { name: 'AWG → Tor', hops: ['awg', 'tor'], serverIndices: [0, 7] },
  { name: 'Xray → Tor', hops: ['xray', 'tor'], serverIndices: [1, 7] },
  { name: 'WG → Tor', hops: ['wg', 'tor'], serverIndices: [2, 7] },
  { name: 'OpenVPN → Tor', hops: ['openvpn', 'tor'], serverIndices: [3, 7] },
  { name: 'OpenVPN → AWG', hops: ['openvpn', 'awg'], serverIndices: [3, 0] },
  { name: 'Xray → Tor → Xray', hops: ['xray', 'tor', 'xray'], serverIndices: [1, 7, 2] },
  { name: 'AWG → Tor → Xray', hops: ['awg', 'tor', 'xray'], serverIndices: [0, 7, 2] },
]

/** @deprecated используйте CHAIN_TEMPLATES */
export const CHAIN_EXAMPLES = CHAIN_TEMPLATES.map((t) => t.hops)
