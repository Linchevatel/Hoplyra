import type { ChainHop, OpenVpnTransport, Protocol, RouteTestResult, ServerMetrics, ServerPrepareProgress, VpnConfig, VpsServer } from './types'

const BASE = import.meta.env.VITE_API_URL ?? ''

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
    const body = await res.text()
    throw new Error(parseError(body, res.status))
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
}

export function fetchAuthMe(): Promise<AuthMeResponse> {
  return request<AuthMeResponse>('/api/auth/me')
}

export function loginApi(
  username: string,
  password: string,
): Promise<{ ok: boolean; username: string; defaultPassword?: boolean }> {
  return request('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
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
    const body = await res.text()
    throw new Error(parseError(body, res.status))
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
    const body = await res.text()
    throw new Error(parseError(body, res.status))
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
  authSecret: string
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
  return request<VpnConfig>('/api/configs/deploy-chain', {
    method: 'POST',
    body: JSON.stringify({
      hops: hops.map((h) => ({
        id: h.id,
        protocol: h.protocol,
        serverId: h.serverId,
        ...(h.protocol === 'openvpn' ? { transport: h.transport ?? 'udp' } : {}),
        ...(h.protocol === 'xray' && h.xrayBypass ? { xrayBypass: true } : {}),
        ...(h.protocol === 'awg' && h.awgVersion ? { awgVersion: h.awgVersion } : {}),
      })),
    }),
  })
}

export function deployConfig(
  serverId: string,
  protocol: Protocol,
  options?: { transport?: OpenVpnTransport; xrayBypass?: boolean; awgVersion?: 'awg' | 'awg1.5' | 'awg2.0' },
): Promise<VpnConfig> {
  const body: Record<string, string | boolean> = { serverId, protocol }
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

export function fetchClientConfig(id: string): Promise<{ protocol: string; config: string }> {
  return request(`/api/configs/${id}/client-config`)
}
