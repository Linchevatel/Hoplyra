import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { FileKey, Shield, GitBranch, LayoutGrid } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { useDashboard } from '@/lib/dashboard'
import { canShowClientConfig } from '@/lib/client-config'
import type { VpnConfig } from '@/lib/types'
import { useI18n } from '@/i18n/I18nProvider'
import { cn } from '@/lib/utils'
import { ClientConfigCard } from '@/components/dashboard/ClientConfigCard'
import { ClientConfigModal } from '@/components/dashboard/ClientConfigModal'

type Filter = 'all' | 'vpn' | 'chain'

const STATUS_ORDER: Record<VpnConfig['status'], number> = {
  active: 0,
  deploying: 1,
  inactive: 2,
  error: 3,
}

function sortConfigs(configs: VpnConfig[]): VpnConfig[] {
  return [...configs].sort((a, b) => {
    const statusDiff = STATUS_ORDER[a.status] - STATUS_ORDER[b.status]
    if (statusDiff !== 0) return statusDiff
    return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
  })
}

export function ClientConfigsTab() {
  const { t } = useI18n()
  const { servers, configs, updateConfig } = useDashboard()
  const [filter, setFilter] = useState<Filter>('all')
  const [configModalId, setConfigModalId] = useState<string | null>(null)

  const availableConfigs = useMemo(
    () => sortConfigs(configs.filter(canShowClientConfig)),
    [configs],
  )

  const vpnConfigs = useMemo(
    () => availableConfigs.filter((c) => !c.hops || c.hops.length < 2),
    [availableConfigs],
  )

  const chainConfigs = useMemo(
    () => availableConfigs.filter((c) => c.hops && c.hops.length >= 2),
    [availableConfigs],
  )

  const visibleConfigs =
    filter === 'vpn' ? vpnConfigs : filter === 'chain' ? chainConfigs : availableConfigs

  const configModal = configModalId ? configs.find((c) => c.id === configModalId) : null
  const configModalServer = configModal ? servers.find((s) => s.id === configModal.serverId) : null

  const filters: { id: Filter; label: string; icon: typeof LayoutGrid; count: number }[] = [
    { id: 'all', label: t('common.all'), icon: LayoutGrid, count: availableConfigs.length },
    { id: 'vpn', label: 'VPN', icon: Shield, count: vpnConfigs.length },
    { id: 'chain', label: t('nav.chains'), icon: GitBranch, count: chainConfigs.length },
  ]

  return (
    <div className="space-y-6 w-full">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <FileKey size={24} className="text-cyan-400" />
          {t('dashboard.configs.title')}
        </h1>
        <p className="text-zinc-400 mt-1">
          {t('dashboard.configs.subtitle')}
        </p>
      </div>

      {availableConfigs.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {filters.map(({ id, label, icon: Icon, count }) => (
            <button
              key={id}
              type="button"
              onClick={() => setFilter(id)}
              className={cn(
                'inline-flex items-center gap-2 px-3 py-1.5 rounded-xl text-sm transition-colors cursor-pointer border',
                filter === id
                  ? 'border-cyan-500/30 bg-cyan-500/10 text-cyan-200'
                  : 'border-white/5 bg-white/[0.02] text-zinc-400 hover:text-zinc-200 hover:border-white/10',
              )}
            >
              <Icon size={14} />
              {label}
              <span className="text-xs text-zinc-500 tabular-nums">{count}</span>
            </button>
          ))}
        </div>
      )}

      {availableConfigs.length === 0 ? (
        <Card className="text-center py-14">
          <FileKey size={40} className="mx-auto text-zinc-600 mb-4" />
          <h3 className="font-semibold text-lg">{t('dashboard.configs.empty')}</h3>
          <p className="text-sm text-zinc-500 mt-2 max-w-sm mx-auto">
            {t('dashboard.configs.emptyDesc')}
          </p>
          <div className="flex flex-wrap justify-center gap-2 mt-6">
            <Link to="/vpn">
              <Button size="sm">
                <Shield size={14} />
                {t('dashboard.vpn.deployVpn')}
              </Button>
            </Link>
            <Link to="/chains">
              <Button variant="secondary" size="sm">
                <GitBranch size={14} />
                {t('dashboard.configs.buildChain')}
              </Button>
            </Link>
          </div>
        </Card>
      ) : visibleConfigs.length === 0 ? (
        <Card className="text-center py-10">
          <p className="text-sm text-zinc-500">{t('dashboard.configs.emptyCategory')}</p>
        </Card>
      ) : (
        <div className="space-y-4">
          {visibleConfigs.map((config) => {
            const server = servers.find((s) => s.id === config.serverId)
            if (!server) return null
            return (
              <ClientConfigCard
                key={config.id}
                config={config}
                server={server}
                servers={servers}
                onOpen={() => setConfigModalId(config.id)}
              />
            )
          })}
        </div>
      )}

      {configModal && configModalServer && (
        <ClientConfigModal
          config={configModal}
          server={configModalServer}
          servers={servers}
          onClose={() => setConfigModalId(null)}
          onConfigUpdated={updateConfig}
        />
      )}
    </div>
  )
}
