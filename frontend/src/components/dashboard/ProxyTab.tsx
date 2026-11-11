import { useMemo, useState } from 'react'
import { GitBranch, Shield, Sparkles, ChevronsDownUp, ChevronsUpDown } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Card } from '@/components/ui/Card'
import { PageHeader } from '@/components/ui/PageHeader'
import { SocksProxyPanel } from '@/components/dashboard/SocksProxyPanel'
import { useDashboard } from '@/lib/dashboard-context'
import { useI18n } from '@/i18n/i18n-context'
import type { VpnConfig } from '@/lib/types'
import { cn } from '@/lib/utils'
import { AppIcon } from '@/components/ui/AppIcon'

type Filter = 'all' | 'chains' | 'vpn'

function isChainConfig(config: VpnConfig): boolean {
  return Boolean(config.hops && config.hops.length >= 2)
}

function FilterPill({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'px-3 py-1.5 rounded-full text-xs font-medium border transition-all cursor-pointer',
        active
          ? 'bg-accent-cyan/15 text-accent-cyan border-accent-cyan/30'
          : 'bg-white/[0.03] text-text-muted border-white/8 hover:text-zinc-300 hover:border-white/15',
      )}
    >
      {children}
    </button>
  )
}

export function ProxyTab() {
  const { t } = useI18n()
  const { configs } = useDashboard()
  const [filter, setFilter] = useState<Filter>('all')
  const [defaultExpanded, setDefaultExpanded] = useState(true)
  const [layoutKey, setLayoutKey] = useState(0)

  function collapseAll() {
    setDefaultExpanded(false)
    setLayoutKey((k) => k + 1)
  }

  function expandAll() {
    setDefaultExpanded(true)
    setLayoutKey((k) => k + 1)
  }

  const activeTargets = useMemo(
    () => configs.filter((c) => c.status === 'active'),
    [configs],
  )
  const chains = useMemo(() => activeTargets.filter(isChainConfig), [activeTargets])
  const vpns = useMemo(() => activeTargets.filter((c) => !isChainConfig(c)), [activeTargets])
  const socksCount = useMemo(
    () => activeTargets.filter((c) => c.socksProxy?.enabled).length,
    [activeTargets],
  )

  const visible =
    filter === 'all' ? activeTargets : filter === 'chains' ? chains : vpns

  return (
    <div className="dashboard-page">
      <PageHeader title={t('dashboard.proxyTab.title')} subtitle={t('dashboard.proxyTab.subtitle')}>
        {activeTargets.length > 0 && (
          <div className="flex items-center gap-2 text-xs text-text-muted shrink-0">
            <Sparkles size={13} className="text-accent-cyan/80" />
            {t('dashboard.proxyTab.stats', {
              routes: activeTargets.length,
              socks: socksCount,
            })}
          </div>
        )}
      </PageHeader>

      {activeTargets.length === 0 ? (
        <Card className="p-10 sm:p-12 text-center border-dashed border-white/10">
          <AppIcon name="no-active-vpn" size={96} className="mx-auto mb-4" />
          <p className="text-base font-medium text-zinc-300">{t('dashboard.proxyTab.empty')}</p>
          <p className="text-sm text-text-muted mt-2 max-w-md mx-auto">{t('dashboard.proxyTab.emptyHint')}</p>
          <div className="flex flex-wrap justify-center gap-3 mt-6">
            <Link
              to="/vpn"
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium bg-white/[0.04] border border-white/10 text-zinc-300 hover:border-accent-cyan/30 hover:text-accent-cyan transition-colors"
            >
              <Shield size={15} />
              {t('dashboard.proxyTab.goVpn')}
            </Link>
            <Link
              to="/chains"
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium bg-white/[0.04] border border-white/10 text-zinc-300 hover:border-accent-violet/30 hover:text-accent-violet transition-colors"
            >
              <GitBranch size={15} />
              {t('dashboard.proxyTab.goChains')}
            </Link>
          </div>
        </Card>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <FilterPill active={filter === 'all'} onClick={() => setFilter('all')}>
                {t('dashboard.proxyTab.filterAll')} ({activeTargets.length})
              </FilterPill>
              <FilterPill active={filter === 'chains'} onClick={() => setFilter('chains')}>
                {t('dashboard.proxyTab.chainsSection')} ({chains.length})
              </FilterPill>
              <FilterPill active={filter === 'vpn'} onClick={() => setFilter('vpn')}>
                {t('dashboard.proxyTab.vpnSection')} ({vpns.length})
              </FilterPill>
            </div>
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={expandAll}
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] text-text-muted hover:text-zinc-300 hover:bg-white/5 border border-transparent hover:border-white/10 transition-colors cursor-pointer"
              >
                <ChevronsUpDown size={13} />
                {t('dashboard.proxyTab.expandAll')}
              </button>
              <button
                type="button"
                onClick={collapseAll}
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] text-text-muted hover:text-zinc-300 hover:bg-white/5 border border-transparent hover:border-white/10 transition-colors cursor-pointer"
              >
                <ChevronsDownUp size={13} />
                {t('dashboard.proxyTab.collapseAll')}
              </button>
            </div>
          </div>

          {visible.length === 0 ? (
            <Card className="p-8 text-center">
              <p className="text-sm text-text-muted">{t('dashboard.proxyTab.filterEmpty')}</p>
            </Card>
          ) : (
            <div className="grid gap-4">
              {visible.map((config) => (
                <SocksProxyPanel
                  key={`${config.id}-${layoutKey}`}
                  config={config}
                  defaultExpanded={defaultExpanded}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
