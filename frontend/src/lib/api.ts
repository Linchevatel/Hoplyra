import { assertAwg, versionParams, type AwgParams } from './awg'
import type { ChainHop, OpenVpnTransport, Protocol, RealmBroker, RealmTunnel, RealmTunnelConfig, RouteTestResult, ServerMetrics, ServerPrepareProgress, VpnConfig, VpsServer } from './types'

const BASE = import.meta.env?.VITE_API_URL ?? ''

export class ApiError extends Error {
  status: number
  detail: string
  retryAfter?: number | null
  constructor(status: number, detail: string, retryAfter?: number | null) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.retryAfter = retryAfter
  }
}

let unauthorizedHandler: (() => void) | null = null

export function setUnauthorizedHandler(handler: (() => void) | null) {
  unauthorizedHandler = handler
}

function parseError(body: string, status: number): string {
  try {
    const json = JSON.parse(body) as { detail?: string | Array<{ msg?: string }> }
    if (typeof json.detail === 'string') return json.detail
    if (Array.isArray(json.detail)) {
      return json.detail.map((d) => d.msg).filter(Boolean).join('; ') || body
    }
  } catch {
    /* plain text */
  }
  return body || `HTTP ${status}`
}

function apiHeaders(extra?: HeadersInit): HeadersInit {
  return {
    'Content-Type': 'application/json',
    ...(extra as Record<string, string> | undefined),
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    credentials: 'include',
    headers: apiHeaders(init?.headers),
  })
  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    unauthorizedHandler?.()
  }
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    const retryAfterHeader = res.headers.get('Retry-After')
    const retryAfter = retryAfterHeader ? parseInt(retryAfterHeader, 10) : null
    throw new ApiError(res.status, parseError(body, res.status), isNaN(retryAfter as number) ? null : retryAfter)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export async function apiHealth(): Promise<boolean> {
  try {
    const data = await request<{ status: string }>('/api/health')
    return data.status === 'ok'
  } catch {
    return false
  }
}

export type AuthMeResponse = {
  authenticated: boolean
  authRequired: boolean
  username?: string | null
  defaultPassword?: boolean
  twoFactorEnabled?: boolean
}

export function fetchAuthMe(): Promise<AuthMeResponse> {
  return request<AuthMeResponse>('/api/auth/me')
}

export type LoginResult = {
  ok?: boolean
  username?: string
  defaultPassword?: boolean
  twoFactorEnabled?: boolean
  twoFactorRequired?: boolean
}

export function loginApi(
  username: string,
  password: string,
  code?: string,
): Promise<LoginResult> {
  return request('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password, ...(code ? { code } : {}) }),
  })
}

export function setup2faApi(): Promise<{ secret: string; otpauthUri: string }> {
  return request('/api/auth/2fa/setup', { method: 'POST' })
}

export function enable2faApi(code: string): Promise<{ ok: boolean }> {
  return request('/api/auth/2fa/enable', {
    method: 'POST',
    body: JSON.stringify({ code }),
  })
}

export function disable2faApi(password: string, code: string): Promise<{ ok: boolean }> {
  return request('/api/auth/2fa/disable', {
    method: 'POST',
    body: JSON.stringify({ password, code }),
  })
}

export function logoutApi(): Promise<{ ok: boolean }> {
  return request('/api/auth/logout', { method: 'POST' })
}

export function changePasswordApi(currentPassword: string, newPassword: string): Promise<{ ok: boolean }> {
  return request('/api/auth/change-password', {
    method: 'POST',
    body: JSON.stringify({ currentPassword, newPassword }),
  })
}

function filenameFromContentDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback
  const utf8 = header.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8?.[1]) return decodeURIComponent(utf8[1])
  const plain = header.match(/filename="?([^";]+)"?/i)
  return plain?.[1] ?? fallback
}

export async function downloadDbBackupApi(): Promise<{ filename: string; path: string }> {
  const res = await fetch(`${BASE}/api/settings/db-backup`, {
    method: 'POST',
    credentials: 'include',
  })
  if (res.status === 401) {
    unauthorizedHandler?.()
  }
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new ApiError(res.status, parseError(body, res.status))
  }
  const blob = await res.blob()
  const filename = filenameFromContentDisposition(
    res.headers.get('Content-Disposition'),
    'hoplyra-backup.db',
  )
  const path = res.headers.get('X-Hoplyra-Backup-Path') ?? filename
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
  return { filename, path }
}

export type DbBackupEntry = {
  name: string
  path: string
  sizeBytes: number
  createdAt: string
}

export function fetchDbBackupInfo(): Promise<{ backupDir: string; backups: DbBackupEntry[] }> {
  return request<{ backupDir: string; backups: DbBackupEntry[] }>('/api/settings/db-backup-info')
}

export function restoreDbBackupApi(name: string): Promise<{
  ok: boolean
  restoredFrom: string
  preRestoreBackup: string
}> {
  return request('/api/settings/db-backup/restore', {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
}

export async function restoreDbBackupUploadApi(file: File): Promise<{
  ok: boolean
  restoredFrom: string
  preRestoreBackup: string
}> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${BASE}/api/settings/db-backup/restore-upload`, {
    method: 'POST',
    credentials: 'include',
    body: form,
  })
  if (res.status === 401) {
    unauthorizedHandler?.()
  }
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new ApiError(res.status, parseError(body, res.status))
  }
  return res.json() as Promise<{
    ok: boolean
    restoredFrom: string
    preRestoreBackup: string
  }>
}

export interface ServerCreatePayload {
  name: string
  host: string
  port?: number
  username?: string
  authType?: 'password' | 'key' | 'agent'
  authSecret?: string
  location?: string
  tags?: string[]
  notes?: string
}

export function fetchServers(): Promise<VpsServer[]> {
  return request<VpsServer[]>('/api/servers')
}

export function createServer(payload: ServerCreatePayload): Promise<VpsServer> {
  return request<VpsServer>('/api/servers', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function fetchServerPrepareStatus(serverId: string): Promise<ServerPrepareProgress> {
  return request<ServerPrepareProgress>(`/api/servers/${serverId}/prepare`)
}

export function deleteServer(id: string): Promise<void> {
  return request<void>(`/api/servers/${id}`, { method: 'DELETE' })
}

export type ServerUpdatePayload = {
  name?: string
  tags?: string[]
  notes?: string | null
}

export function updateServer(id: string, payload: ServerUpdatePayload): Promise<VpsServer> {
  return request<VpsServer>(`/api/servers/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function pingServer(id: string): Promise<VpsServer> {
  return request<VpsServer>(`/api/servers/${id}/ping`, { method: 'POST' })
}

export function fetchServersMetrics(): Promise<ServerMetrics[]> {
  return request<ServerMetrics[]>('/api/servers/metrics')
}

export function fetchServerMetrics(id: string): Promise<ServerMetrics> {
  return request<ServerMetrics>(`/api/servers/${id}/metrics`)
}

export type MetricsStreamPayload = {
  metrics: ServerMetrics[]
  ts: string
}

export function subscribeMetricsStream(
  onData: (payload: MetricsStreamPayload) => void,
  onError?: () => void,
): () => void {
  const url = `${BASE}/api/servers/metrics/stream`
  const source = new EventSource(url, { withCredentials: true })

  source.onmessage = (event) => {
    try {
      onData(JSON.parse(event.data) as MetricsStreamPayload)
    } catch {
      onError?.()
    }
  }

  source.onerror = () => {
    onError?.()
  }

  return () => source.close()
}

export function fetchConfigs(): Promise<VpnConfig[]> {
  return request<VpnConfig[]>('/api/configs')
}

export function testChainRouteApi(hops: ChainHop[]): Promise<RouteTestResult> {
  return request<RouteTestResult>('/api/chains/test-route', {
    method: 'POST',
    body: JSON.stringify({
      hops: hops.map((h) => ({
        id: h.id,
        protocol: h.protocol,
        serverId: h.serverId,
        ...(h.protocol === 'openvpn' ? { transport: h.transport ?? 'udp' } : {}),
        ...(h.protocol === 'xray' && h.xrayBypass ? { xrayBypass: true } : {}),
      })),
    }),
  })
}

export function deployChainApi(hops: ChainHop[]): Promise<VpnConfig> {
  for (const h of hops) if (h.protocol === 'awg') assertAwg(versionParams(h.awgParams, h.awgVersion))
  return request<VpnConfig>('/api/configs/deploy-chain', {
    method: 'POST',
    body: JSON.stringify({
      hops: hops.map((h) => ({
        id: h.id,
        protocol: h.protocol,
        serverId: h.serverId,
        ...(h.protocol === 'openvpn' ? { transport: h.transport ?? 'udp' } : {}),
        ...(h.protocol === 'xray' && h.xrayBypass ? { xrayBypass: true } : {}),
        ...(h.protocol === 'awg' ? { awgMode: h.awgMode ?? (h.awgParams ? 'custom' : 'auto') } : {}),
        ...(h.protocol === 'awg' && h.awgVersion ? { awgVersion: h.awgVersion } : {}),
        ...(h.protocol === 'awg' && h.awgMode !== 'none' && h.awgMode !== 'auto' && versionParams(h.awgParams, h.awgVersion) ? { awgParams: versionParams(h.awgParams, h.awgVersion) } : {}),
      })),
    }),
  })
}

export function deployConfig(
  serverId: string,
  protocol: Protocol,
  options?: { transport?: OpenVpnTransport; xrayBypass?: boolean; awgMode?: 'none' | 'auto' | 'custom'; awgParams?: AwgParams; awgVersion?: 'awg' | 'awg1.5' | 'awg2.0' | 'awg3.1' },
): Promise<VpnConfig> {
  const body: Record<string, unknown> = { serverId, protocol }
  if (protocol === 'awg') {
    const params = versionParams(options?.awgParams, options?.awgVersion)
    assertAwg(params)
    body.awgMode = options?.awgMode ?? (params ? 'custom' : 'auto')
    if (params && body.awgMode === 'custom') body.awgParams = params
  }
  if (protocol === 'openvpn' && options?.transport) {
    body.transport = options.transport
  }
  if (protocol === 'xray' && options?.xrayBypass) {
    body.xrayBypass = true
  }
  if (protocol === 'awg' && options?.awgVersion) {
    body.awgVersion = options.awgVersion
  }
  return request<VpnConfig>('/api/configs/deploy', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}


export function stopConfigApi(id: string): Promise<VpnConfig> {
  return request<VpnConfig>(`/api/configs/${id}/stop`, { method: 'POST' })
}

export function restartConfigApi(id: string): Promise<VpnConfig> {
  return request<VpnConfig>(`/api/configs/${id}/restart`, { method: 'POST' })
}

export interface AwgUpgradeResult {
  serverId: string
  host: string
  packages?: Record<string, string>
  awgVersion?: string
  moduleVersion?: string
  confs?: string[]
  restartedContainers?: string[]
  skipped?: boolean
  reason?: string
}

export function upgradeAwgConfigApi(
  id: string,
): Promise<{ config: VpnConfig; upgrade: AwgUpgradeResult[] }> {
  return request(`/api/configs/${id}/upgrade-awg`, { method: 'POST' })
}

export function repairAmneziaAwgConfigApi(id: string): Promise<VpnConfig> {
  return request<VpnConfig>(`/api/configs/${id}/repair-amnezia-awg`, { method: 'POST' })
}

export function enableSocksProxyApi(id: string): Promise<VpnConfig> {
  return request<VpnConfig>(`/api/configs/${id}/socks/enable`, { method: 'POST' })
}

export function disableSocksProxyApi(id: string): Promise<VpnConfig> {
  return request<VpnConfig>(`/api/configs/${id}/socks/disable`, { method: 'POST' })
}

export function deleteConfigApi(id: string): Promise<void> {
  return request<void>(`/api/configs/${id}`, { method: 'DELETE' })
}

export function fetchClientConfig(id: string, mode?: 'all' | 'bypass-ru'): Promise<{ protocol: string; config: string; amneziaVpnUri?: string | null }> {
  const query = mode && mode !== 'all' ? `?mode=${encodeURIComponent(mode)}` : ''
  return request(`/api/configs/${id}/client-config${query}`)
}

export function generateAwg(version: string, profile: string) {
  return request<{ params: AwgParams }>(`/api/awg/generate?version=${encodeURIComponent(version)}&profile=${encodeURIComponent(profile)}`)
}

export interface ConfigPeer {
  id: string
  configId: string
  name: string
  publicKey: string
  clientIp: string
  clientConfig: string
  amneziaVpnUri?: string | null
  vlessUri?: string | null
  createdAt: string
  rxBytes?: number | null
  txBytes?: number | null
  lastHandshake?: number | null
  isConnected?: boolean | null
  statsSupported?: boolean
}

export function fetchConfigPeers(configId: string, mode?: 'all' | 'bypass-ru'): Promise<ConfigPeer[]> {
  const query = mode && mode !== 'all' ? `?mode=${encodeURIComponent(mode)}` : ''
  return request<ConfigPeer[]>(`/api/configs/${configId}/peers${query}`)
}

export function createConfigPeer(configId: string, name: string): Promise<ConfigPeer> {
  return request<ConfigPeer>(`/api/configs/${configId}/peers`, {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
}

export function deleteConfigPeer(configId: string, peerId: string): Promise<{ status: string }> {
  return request<{ status: string }>(`/api/configs/${configId}/peers/${peerId}`, {
    method: 'DELETE',
  })
}

export function censorCheckServerApi(serverId: string): Promise<any> {
  return request(`/api/servers/${serverId}/censor-check`, {
    method: 'POST',
  })
}

export function censorCheckAllServersApi(): Promise<{ results: Record<string, any> }> {
  return request('/api/servers/censor-check', {
    method: 'POST',
  })
}

export type SystemUpdateInfo = {
  currentVersion: string
  latestVersion: string
  hasUpdate: boolean
  releaseNotes?: string
  publishedAt?: string
  releaseUrl?: string
  error?: string
}

export function checkSystemUpdateApi(force: boolean = false): Promise<SystemUpdateInfo> {
  return request<SystemUpdateInfo>(`/api/system/update/check${force ? '?force=true' : ''}`)
}

export function applySystemUpdateApi(version?: string): Promise<{ ok: boolean; status: string; message: string; targetVersion?: string }> {
  return request<{ ok: boolean; status: string; message: string; targetVersion?: string }>('/api/system/update/apply', {
    method: 'POST',
    body: JSON.stringify({ version: version || null }),
  })
}

export type SystemUpdateStatus = {
  inProgress: boolean
  currentVersion: string
}

export function getSystemUpdateStatusApi(): Promise<SystemUpdateStatus> {
  return request<SystemUpdateStatus>('/api/system/update/status')
}

export function fetchDeployLog(configId: string, since = 0): Promise<import('./deploy-log-state').DeployLogPayload> {
  return request(`/api/configs/${encodeURIComponent(configId)}/deploy-log?since=${since}`)
}

export function deployLogStreamUrl(configId: string, since = 0): string {
  return `${BASE}/api/configs/${encodeURIComponent(configId)}/deploy-log/stream?since=${since}`
}

export const realmsApi = {
  getStatus: () => request<{ activeBroker: RealmBroker | null; brokers: RealmBroker[]; tunnels: RealmTunnel[] }>('/api/realms/status'),
  listBrokers: () => request<RealmBroker[]>('/api/realms/brokers'),
  deployBroker: (data: { serverId: string; listenPort?: number; token?: string; domainOrIp?: string; asyncDeploy?: boolean }) =>
    request<RealmBroker>('/api/realms/brokers', { method: 'POST', body: JSON.stringify(data) }),
  stopBroker: (brokerId: string) =>
    request<{ status: string }>(`/api/realms/brokers/${brokerId}/stop`, { method: 'POST' }),
  startBroker: (brokerId: string) =>
    request<{ status: string }>(`/api/realms/brokers/${brokerId}/start`, { method: 'POST' }),
  deleteBroker: (brokerId: string) =>
    request<{ status: string }>(`/api/realms/brokers/${brokerId}`, { method: 'DELETE' }),

  listTunnels: () => request<RealmTunnel[]>('/api/realms/tunnels'),
  deployTunnel: (data: { serverId: string; name: string; brokerType?: 'public' | 'self_hosted' | 'custom'; customBrokerUrl?: string; realmName?: string; authPassword?: string; asyncDeploy?: boolean }) =>
    request<RealmTunnel>('/api/realms/tunnels', { method: 'POST', body: JSON.stringify(data) }),
  stopTunnel: (tunnelId: string) =>
    request<{ status: string }>(`/api/realms/tunnels/${tunnelId}/stop`, { method: 'POST' }),
  startTunnel: (tunnelId: string) =>
    request<{ status: string }>(`/api/realms/tunnels/${tunnelId}/start`, { method: 'POST' }),
  deleteTunnel: (tunnelId: string) =>
    request<{ status: string }>(`/api/realms/tunnels/${tunnelId}`, { method: 'DELETE' }),
  getTunnelConfig: (tunnelId: string) =>
    request<RealmTunnelConfig>(`/api/realms/tunnels/${tunnelId}/config`),
  getTunnelLogs: (tunnelId: string, tail?: number) =>
    request<{ logs: string }>(`/api/realms/tunnels/${tunnelId}/logs${tail ? `?tail=${tail}` : ''}`),
  getBrokerLogs: (brokerId: string, tail?: number) =>
    request<{ logs: string }>(`/api/realms/brokers/${brokerId}/logs${tail ? `?tail=${tail}` : ''}`),
}

