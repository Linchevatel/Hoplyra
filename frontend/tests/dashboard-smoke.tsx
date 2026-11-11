import { MemoryRouter } from 'react-router-dom'
import type { VpsServer, VpnConfig } from '../src/lib/types'
import { I18nProvider } from '../src/i18n/I18nProvider'
import { DashboardContext } from '../src/lib/dashboard-context'
import { AuthContext } from '../src/lib/auth-context'
import { OverviewTab } from '../src/components/dashboard/OverviewTab'
import { ServersTab } from '../src/components/dashboard/ServersTab'
import { StatusTab } from '../src/components/dashboard/StatusTab'
import { VpnTab } from '../src/components/dashboard/VpnTab'
import { ChainsTab } from '../src/components/dashboard/ChainsTab'
import { ProxyTab } from '../src/components/dashboard/ProxyTab'
import { ClientConfigsTab } from '../src/components/dashboard/ClientConfigsTab'
import { SettingsTab } from '../src/components/dashboard/SettingsTab'

const tabs = { Overview: OverviewTab, Servers: ServersTab, Status: StatusTab, Vpn: VpnTab, Chains: ChainsTab, Proxy: ProxyTab, ClientConfigs: ClientConfigsTab, Settings: SettingsTab }
const noop = () => {}
const dashboard = {
  servers: [], configs: [], deployHistory: [], chainDraft: [], userChainTemplates: [],
  notifications: [], unreadNotifications: 0, chainDeployUi: null,
  deletingConfigIds: new Set<string>(), removingConfigIds: new Set<string>(), upgradingAwgIds: new Set<string>(), socksProxyLoadingIds: new Set<string>(),
  setChainDraft: noop, addServer: async () => {}, updateServer: noop, removeServer: noop, pingServerById: async () => {}, deployProtocol: async () => undefined,
  deployChain: async () => {}, stopConfig: noop, restartConfig: noop, upgradeAwgConfig: noop,
  enableSocksProxy: noop, disableSocksProxy: noop, removeConfig: noop, updateConfig: noop,
  saveUserChainTemplate: noop, removeUserChainTemplate: noop, markNotificationRead: noop,
  markAllNotificationsRead: noop, dismissNotification: noop, clearAllNotifications: noop,
}
const auth = { username: 'test', defaultPassword: false, authenticated: true, authRequired: true, loading: false, login: async () => {}, logout: async () => {}, refresh: async () => {} }

const servers: VpsServer[] = [
  { id: 'us', name: 'US Server', host: '192.0.2.10', port: 22, username: 'test', location: 'US, New York', status: 'online', latencyMs: 193 },
  { id: 'de', name: 'Germany', host: '192.0.2.11', port: 22, username: 'test', location: 'DE, Frankfurt', status: 'online', latencyMs: 107 },
  { id: 'jp', name: 'Japan', host: '192.0.2.12', port: 22, username: 'test', location: 'JP, Tokyo', status: 'offline' },
]
const configs: VpnConfig[] = [
  { id: 'single', serverId: 'us', protocol: 'wg', status: 'active', clientConfig: '[Interface]\nAddress = 10.0.0.2/32', createdAt: '2026-01-01T00:00:00Z' },
  { id: 'chain', serverId: 'de', protocol: 'awg', status: 'active', clientConfig: '[Interface]\nAddress = 10.1.0.2/32', createdAt: '2026-01-01T00:00:00Z', hops: [
    { id: 'first', serverId: 'de', protocol: 'awg' }, { id: 'last', serverId: 'jp', protocol: 'wg' },
  ] },
]

export function DashboardSmoke({ name, populated = false }: { name: keyof typeof tabs; populated?: boolean }) {
  const Tab = tabs[name]
  const value = populated ? { ...dashboard, servers, configs, chainDraft: configs[1].hops! } : dashboard
  return <MemoryRouter><I18nProvider><AuthContext.Provider value={auth}><DashboardContext.Provider value={value}><div style={{ padding: 16, minWidth: 0 }}><Tab /></div></DashboardContext.Provider></AuthContext.Provider></I18nProvider></MemoryRouter>
}
