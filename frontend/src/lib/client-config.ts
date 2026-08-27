import type { Protocol, VpnConfig, VpsServer } from './types'
import { formatChainHops } from './chain-utils'

export interface ClientConfigBundle {
  fileConfig: string
  filename: string
  supportsQr: boolean
  /** Что кодировать в QR — ссылка импорта или файл */
  qrPayload: string
  shareLink?: {
    scheme: 'vpn' | 'vless'
    uri: string
  }
  isChainEntry: boolean
  chainRoute?: string
}

function mockUuidFromId(id: string): string {
  const hex = id.replace(/-/g, '').slice(0, 12).padEnd(12, '0')
  return `00000000-0000-4000-8000-${hex}`
}

/** Keys/certs come from the backend — never synthesize client material for these. */
export function configRequiresServerSecrets(config: VpnConfig): boolean {
  if (config.protocol === 'xray' && config.xrayBypass) return true
  return config.protocol === 'awg' || config.protocol === 'wg' || config.protocol === 'openvpn'
}

export function extractVlessUri(clientConfig?: string): string | undefined {
  if (!clientConfig) return undefined
  const match = clientConfig.match(/vless:\/\/[^\s]+/)
  return match?.[0]
}

function toBase64(text: string): string {
  if (typeof btoa !== 'undefined') {
    return btoa(text)
  }
  return text
}

export function generateClientConfig(config: VpnConfig, server: VpsServer): string {
  return buildClientConfigBundle(config, server).fileConfig
}

export function generateVlessUri(host: string, configId: string, name: string): string {
  const uuid = mockUuidFromId(configId)
  const remark = encodeURIComponent(name)
  return `vless://${uuid}@${host}:443?encryption=none&security=tls&type=tcp&flow=xtls-rprx-vision&sni=${host}#${remark}`
}

export function generateVpnUri(fileConfig: string, name: string): string {
  const remark = encodeURIComponent(name)
  return `vpn://${toBase64(fileConfig)}#${remark}`
}

function generateFileConfig(config: VpnConfig, server: VpsServer): string {
  if (configRequiresServerSecrets(config)) {
    return '# Client config is issued by the server after deploy.\n# Open this dialog again to fetch the live config from the API.\n'
  }

  const id = config.id.slice(0, 8)
  const host = server.host

  switch (config.protocol) {
    case 'awg':
      return `[Interface]
# Hoplyra AmneziaWG — импорт в AmneziaVPN
PrivateKey = DEMO_PRIVATE_KEY_${id}
Address = 10.9.1.2/32
DNS = 1.1.1.1, 8.8.8.8
Jc = 5
Jmin = 54
Jmax = 173
S1 = 53
S2 = 75
S3 = 14
S4 = 12
H1 = 1020325451
H2 = 3288052141
H3 = 1766607858
H4 = 2528465083

[Peer]
PublicKey = DEMO_PUBLIC_KEY_${id}
Endpoint = ${host}:55424
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25`
    case 'wg':
      return `[Interface]
# Hoplyra WireGuard
PrivateKey = DEMO_PRIVATE_KEY_${id}
Address = 10.66.66.2/32
DNS = 1.1.1.1

[Peer]
PublicKey = DEMO_PUBLIC_KEY_${id}
Endpoint = ${host}:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25`
    case 'openvpn': {
      const transport = config.transport ?? 'udp'
      return `client
dev tun
proto ${transport}
remote ${host} 1194
resolv-retry infinite
nobind
persist-key
persist-tun
remote-cert-tls server
cipher AES-256-GCM
verb 3
# Hoplyra demo config ${id}`
    }
    case 'xray':
      return JSON.stringify(
        {
          log: { loglevel: 'warning' },
          outbounds: [
            {
              protocol: 'vless',
              settings: {
                vnext: [
                  {
                    address: host,
                    port: 443,
                    users: [{ id: mockUuidFromId(config.id), encryption: 'none', flow: 'xtls-rprx-vision' }],
                  },
                ],
              },
              streamSettings: { network: 'tcp', security: 'tls' },
            },
          ],
        },
        null,
        2,
      )
    case 'tor':
      return `# Tor SOCKS proxy via ${host}
SocksPort 9050
ExitNodes {de},{nl},{se}
# Hoplyra bridge config ${id}`
    default:
      return `# Config for ${config.protocol}`
  }
}

export function buildClientConfigBundle(
  config: VpnConfig,
  server: VpsServer,
  servers?: VpsServer[],
): ClientConfigBundle {
  const fileConfig =
    config.clientConfig ??
    (configRequiresServerSecrets(config) ? '' : generateFileConfig(config, server))
  const isChainEntry = Boolean(config.hops && config.hops.length >= 2)
  const chainRoute =
    isChainEntry && config.hops && servers
      ? formatChainHops(config.hops, servers)
      : undefined

  const bundle: ClientConfigBundle = {
    fileConfig,
    filename: configFilename(config.protocol, server.name, isChainEntry),
    supportsQr: supportsQr(config.protocol),
    qrPayload: fileConfig,
    isChainEntry,
    chainRoute,
  }

  if (config.protocol === 'awg' || config.protocol === 'wg') {
    const uri =
      config.protocol === 'awg' && config.amneziaVpnUri
        ? config.amneziaVpnUri
        : generateVpnUri(fileConfig, server.name)
    bundle.shareLink = { scheme: 'vpn', uri }
    bundle.qrPayload = uri
  }

  if (config.protocol === 'xray') {
    const uri =
      config.vlessUri ??
      extractVlessUri(config.clientConfig) ??
      extractVlessUri(fileConfig) ??
      (configRequiresServerSecrets(config)
        ? undefined
        : generateVlessUri(server.host, config.id, server.name))
    if (uri) {
      bundle.shareLink = { scheme: 'vless', uri }
      bundle.qrPayload = uri
    }
  }

  if (config.protocol === 'hysteria2' || config.protocol === 'tuic') {
    const uri = fileConfig.trim()
    if (uri.startsWith('hysteria2://') || uri.startsWith('tuic://')) {
      bundle.shareLink = { scheme: 'vless', uri }
      bundle.qrPayload = uri
    }
  }

  return bundle
}

export function qrCodeUrl(data: string, size = 180): string {
  return `https://api.qrserver.com/v1/create-qr-code/?size=${size}x${size}&data=${encodeURIComponent(data)}`
}

export function supportsQr(protocol: Protocol): boolean {
  return (
    protocol === 'wg' ||
    protocol === 'awg' ||
    protocol === 'openvpn' ||
    protocol === 'xray' ||
    protocol === 'hysteria2' ||
    protocol === 'tuic'
  )
}

export function canShowClientConfig(config: VpnConfig): boolean {
  return (
    config.status === 'active' ||
    config.status === 'inactive' ||
    (config.status === 'error' && Boolean(config.clientConfig))
  )
}

export function configFilename(protocol: Protocol, serverName: string, isChain?: boolean): string {
  const safe = serverName.replace(/\s+/g, '-').toLowerCase()
  const prefix = isChain ? 'chain-entry-' : ''
  const ext = protocol === 'xray' ? 'json' : protocol === 'openvpn' ? 'ovpn' : 'conf'
  const proto = protocol === 'openvpn' ? 'openvpn' : protocol
  return `hoplyra-${proto}-${prefix}${safe}.${ext}`
}
