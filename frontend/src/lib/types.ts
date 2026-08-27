export type Protocol = 'awg' | 'wg' | 'openvpn' | 'xray' | 'tor' | 'hysteria2' | 'tuic'

export type OpenVpnTransport = 'udp' | 'tcp'

export interface ChainHop {
  id: string
  protocol: Protocol
  serverId: string
  /** OpenVPN: udp (default) или tcp */
  transport?: OpenVpnTransport
  /** Xray: VLESS+REALITY для обхода блокировок */
  xrayBypass?: boolean
  /** AWG: awg2.0 (default), awg1.5, или awg */
  awgVersion?: 'awg' | 'awg1.5' | 'awg2.0'
}

export type HopDeployStatus = 'waiting' | 'deploying' | 'done' | 'error'

export type Plan = 'free'

export interface User {
  id: string
  email: string
  name: string
  createdAt: string
}

export interface VpsServer {
  id: string
  name: string
  host: string
  port: number
  username: string
  status: 'online' | 'offline' | 'connecting' | 'error'
  os?: string
  location?: string
  tags?: string[]
  notes?: string
  /** Mock latency для автоподбора */
  latencyMs?: number
  activeProtocol?: Protocol
  /** Сообщение от бэкенда (нет podman, ssh error) */
  statusMessage?: string
  podmanVersion?: string
  /** Цепь, если этот сервер — точка входа */
  activeChainHops?: ChainHop[]
  /** @deprecated используйте activeChainHops */
  activeChain?: Protocol[]
  lastSeen?: string
  /** Прогресс фоновой подготовки VPS (status connecting) */
  prepareProgress?: ServerPrepareProgress
}

export interface ServerPrepareProgress {
  serverId: string
  percent: number
  stage: 'ssh' | 'check' | 'install' | 'verify' | 'done' | 'error'
  message: string
  status: 'running' | 'done' | 'error'
}

export interface VpnConfig {
  id: string
  serverId: string
  protocol: Protocol
  hops?: ChainHop[]
  chain?: Protocol[]
  status: 'active' | 'inactive' | 'deploying' | 'error'
  clientConfig?: string
  vlessUri?: string
  listenPort?: number
  /** OpenVPN: udp (default) или tcp */
  transport?: OpenVpnTransport
  /** Xray: VLESS+REALITY для обхода блокировок */
  xrayBypass?: boolean
  /** AWG: awg2.0 (default), awg1.5, или awg */
  awgVersion?: 'awg' | 'awg1.5' | 'awg2.0'

  hopDeployStatus?: HopDeployStatus[]
  /** Текст ошибки деплоя (если status === error) */
  statusMessage?: string
  /** Готовая ссылка vpn:// для AmneziaVPN (AWG) */
  amneziaVpnUri?: string
  socksProxy?: {
    enabled: boolean
    host: string
    port: number
    username: string
    password?: string
    uri?: string
  }
  createdAt: string
}

export interface UserChainTemplate {
  id: string
  name: string
  hops: Array<{ protocol: Protocol; serverId: string; transport?: OpenVpnTransport; xrayBypass?: boolean }>
  createdAt: string
}

export type DeployHistoryStatus = 'success' | 'error' | 'deploying' | 'stopped'

export interface DeployHistoryEntry {
  id: string
  userId: string
  userName: string
  type: 'vpn' | 'chain'
  serverId: string
  protocol: Protocol
  hops?: ChainHop[]
  status: DeployHistoryStatus
  message?: string
  createdAt: string
}

export type NotificationType =
  | 'server_offline'
  | 'deploy_complete'
  | 'deploy_error'
  | 'chain_error'

export interface AppNotification {
  id: string
  type: NotificationType
  title: string
  message: string
  read: boolean
  createdAt: string
  serverId?: string
  configId?: string
}

export interface ServerMetrics {
  serverId: string
  name: string
  host: string
  latencyMs?: number | null
  online: boolean
  collectedAt: string
  uptimeSeconds?: number | null
  load1?: number | null
  load5?: number | null
  load15?: number | null
  cpuPercent?: number | null
  memoryTotalBytes?: number | null
  memoryUsedBytes?: number | null
  memoryAvailableBytes?: number | null
  diskTotalBytes?: number | null
  diskUsedBytes?: number | null
  diskAvailableBytes?: number | null
  containerCount?: number | null
  networkRxBps?: number | null
  networkTxBps?: number | null
  error?: string | null
  isControl?: boolean
}

export interface HopTestResult {
  hopId: string
  serverId: string
  protocol: Protocol
  reachable: boolean
  latencyMs: number
  error?: string
}

export interface RouteTestResult {
  ok: boolean
  totalLatencyMs: number
  hops: HopTestResult[]
  testedAt: string
}

export type ChainGoal = 'latency' | 'anonymity'
export type ScenarioId = 'bypass' | 'streaming' | 'privacy'

export interface AuthCredentials {
  email: string
  password: string
  name?: string
}
