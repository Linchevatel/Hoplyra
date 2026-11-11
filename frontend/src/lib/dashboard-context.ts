import type { AwgParams } from './awg'
import { createContext, useContext } from 'react'
import type { VpsServer, VpnConfig, ChainHop, UserChainTemplate, DeployHistoryEntry, AppNotification, Protocol, OpenVpnTransport } from './types'
import type { ChainDeploySession } from './chain-deploy-ui'
interface DashboardContextValue {
  servers: VpsServer[]
  configs: VpnConfig[]
  chainDraft: ChainHop[]
  userChainTemplates: UserChainTemplate[]
  deployHistory: DeployHistoryEntry[]
  notifications: AppNotification[]
  unreadNotifications: number
  setChainDraft: (hops: ChainHop[] | ((prev: ChainHop[]) => ChainHop[])) => void
  saveUserChainTemplate: (name: string, hops: ChainHop[]) => void
  removeUserChainTemplate: (id: string) => void
  addServer: (
    server: Omit<VpsServer, 'id' | 'status' | 'port'> & {
      port?: number
      authSecret: string
    },
  ) => Promise<void>
  updateServer: (id: string, patch: Partial<Pick<VpsServer, 'tags' | 'notes' | 'name'>>) => void
  removeServer: (id: string) => void
  pingServerById: (id: string) => Promise<void>
  deployProtocol: (
    serverId: string,
    protocol: Protocol,
    options?: { transport?: OpenVpnTransport; xrayBypass?: boolean; awgMode?: 'none' | 'auto' | 'custom'; awgParams?: AwgParams; awgVersion?: 'awg' | 'awg1.5' | 'awg2.0' | 'awg3.1' },
  ) => Promise<VpnConfig | undefined>
  deployChain: (hops: ChainHop[]) => Promise<void>
  chainDeployUi: ChainDeploySession | null
  stopConfig: (id: string) => void
  restartConfig: (id: string) => void
  upgradeAwgConfig: (id: string) => void
  upgradingAwgIds: ReadonlySet<string>
  enableSocksProxy: (id: string) => void
  disableSocksProxy: (id: string) => void
  socksProxyLoadingIds: ReadonlySet<string>
  deletingConfigIds: ReadonlySet<string>
  removingConfigIds: ReadonlySet<string>
  removeConfig: (id: string) => void
  updateConfig: (config: VpnConfig) => void
  markNotificationRead: (id: string) => void
  markAllNotificationsRead: () => void
  dismissNotification: (id: string) => void
  clearAllNotifications: () => void
}

export const DashboardContext = createContext<DashboardContextValue | null>(null)

export function useDashboard() {
  const ctx = useContext(DashboardContext)
  if (!ctx) throw new Error('useDashboard must be used within DashboardProvider')
  return ctx
}
