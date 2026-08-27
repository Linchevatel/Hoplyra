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
    setup: 'простая' | 'средняя' | 'сложная'
    traits: string[]
    qrConfig: boolean
  }
> = {
  awg: {
    label: 'AmneziaWG',
    shortLabel: 'AWG',
    description: 'Обфусцированный WireGuard с защитой от DPI',
    color: 'from-cyan-500 to-blue-600',
    bestFor: 'Обход блокировок, повседневный VPN',
    setup: 'простая',
    traits: ['Обход DPI', 'На базе WireGuard', 'Низкая задержка'],
    qrConfig: true,
  },
  wg: {
    label: 'WireGuard',
    shortLabel: 'WG',
    description: 'Быстрый и современный VPN-протокол',
    color: 'from-emerald-500 to-teal-600',
    bestFor: 'Стриминг, игры, максимальная скорость',
    setup: 'простая',
    traits: ['Минимальные накладные расходы', 'Стабильное соединение'],
    qrConfig: true,
  },
  openvpn: {
    label: 'OpenVPN',
    shortLabel: 'OVPN',
    description: 'Проверенный временем протокол с гибкой настройкой',
    color: 'from-orange-500 to-amber-600',
    bestFor: 'Совместимость со старыми клиентами',
    setup: 'средняя',
    traits: ['TCP и UDP', 'Гибкие параметры', 'Широкая поддержка'],
    qrConfig: true,
  },
  xray: {
    label: 'Xray + VLESS',
    shortLabel: 'Xray',
    description: 'Продвинутая прокси-платформа с VLESS транспортом',
    color: 'from-violet-500 to-purple-600',
    bestFor: 'Строгий DPI, маскировка под HTTPS',
    setup: 'сложная',
    traits: ['VLESS', 'TLS-маскировка', 'Гибкие транспорты'],
    qrConfig: true,
  },
  tor: {
    label: 'Tor',
    shortLabel: 'Tor',
    description: 'Транспорт или exit — выход в интернет через сеть Tor со сменным IP',
    color: 'from-fuchsia-500 to-pink-600',
    bestFor: 'Анонимность, динамический exit-IP через onion-маршруты',
    setup: 'сложная',
    traits: ['Onion routing', 'Динамический IP', 'Транспорт или exit'],
    qrConfig: false,
  },
  hysteria2: {
    label: 'Hysteria 2',
    shortLabel: 'HY2',
    description: 'Ультрабыстрый QUIC-протокол для мобильного интернета и высоких задержек',
    color: 'from-rose-500 to-red-600',
    bestFor: 'Максимальная скорость, мобильный 4G/5G, плохой канал',
    setup: 'простая',
    traits: ['QUIC (UDP)', 'Игнорирование потерь', 'Port Hopping'],
    qrConfig: true,
  },
  tuic: {
    label: 'TUIC v5',
    shortLabel: 'TUIC',
    description: 'Низколатентный QUIC-протокол с мультиплексированием BBR',
    color: 'from-amber-500 to-orange-600',
    bestFor: 'Мгновенный отклик, веб-серфинг, 0-RTT',
    setup: 'простая',
    traits: ['QUIC (UDP)', 'Zero-RTT', 'BBR мультиплексирование'],
    qrConfig: true,
  },
}

const DEFAULT_PROTOCOL_META = {
  label: 'Unknown',
  shortLabel: 'UNK',
  description: 'Неизвестный протокол',
  color: 'from-gray-500 to-slate-600',
  bestFor: 'Альтернативный вариант',
  setup: 'простая' as const,
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

export const FREE_PROTOCOLS: Protocol[] = ['awg', 'wg', 'openvpn', 'xray', 'tor', 'hysteria2', 'tuic']

/** Протоколы для одиночного VPN (без Tor) */
export const VPN_PROTOCOLS: Protocol[] = ['awg', 'wg', 'openvpn', 'xray', 'hysteria2', 'tuic']

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
  { name: 'TUIC → Tor', hops: ['tuic', 'tor'], serverIndices: [1, 7] },
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
