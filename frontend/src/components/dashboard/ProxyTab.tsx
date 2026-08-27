import { useMemo, useState } from 'react'
import { Globe, GitBranch, Shield, Sparkles, ChevronsDownUp, ChevronsUpDown } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Card } from '@/components/ui/Card'
import { SocksProxyPanel } from '@/components/dashboard/SocksProxyPanel'
import { useDashboard } from '@/lib/dashboard'
import { useI18n } from '@/i18n/I18nProvider'
import type { VpnConfig } from '@/lib/types'
import { cn } from '@/lib/utils'

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
          ? 'bg-cyan-500/15 text-cyan-200 border-cyan-500/30'
          : 'bg-white/[0.03] text-zinc-500 border-white/8 hover:text-zinc-300 hover:border-white/15',
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
    <div className="space-y-6 max-w-4xl">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-zinc-100 flex items-center gap-2.5">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-cyan-500/10 border border-cyan-500/20">
              <Globe size={20} className="text-cyan-400" />
            </span>
            {t('dashboard.proxyTab.title')}
          </h1>
          <p className="text-sm text-zinc-500 mt-2 max-w-xl">{t('dashboard.proxyTab.subtitle')}</p>
        </div>
        {activeTargets.length > 0 && (
          <div className="flex items-center gap-2 text-xs text-zinc-500 shrink-0">
            <Sparkles size={13} className="text-cyan-500/80" />
            {t('dashboard.proxyTab.stats', {
              routes: activeTargets.length,
              socks: socksCount,
            })}
          </div>
        )}
      </header>

      {activeTargets.length === 0 ? (
        <Card className="p-10 sm:p-12 text-center border-dashed border-white/10">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-white/[0.04] border border-white/8 mb-4">
            <Globe size={26} className="text-zinc-600" />
          </div>
          <p className="text-base font-medium text-zinc-300">{t('dashboard.proxyTab.empty')}</p>
          <p className="text-sm text-zinc-500 mt-2 max-w-md mx-auto">{t('dashboard.proxyTab.emptyHint')}</p>
          <div className="flex flex-wrap justify-center gap-3 mt-6">
            <Link
              to="/vpn"
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium bg-white/[0.04] border border-white/10 text-zinc-300 hover:border-cyan-500/30 hover:text-cyan-200 transition-colors"
            >
              <Shield size={15} />
              {t('dashboard.proxyTab.goVpn')}
            </Link>
            <Link
              to="/chains"
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium bg-white/[0.04] border border-white/10 text-zinc-300 hover:border-violet-500/30 hover:text-violet-200 transition-colors"
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
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] text-zinc-500 hover:text-zinc-300 hover:bg-white/5 border border-transparent hover:border-white/10 transition-colors cursor-pointer"
              >
                <ChevronsUpDown size={13} />
                {t('dashboard.proxyTab.expandAll')}
              </button>
              <button
                type="button"
                onClick={collapseAll}
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] text-zinc-500 hover:text-zinc-300 hover:bg-white/5 border border-transparent hover:border-white/10 transition-colors cursor-pointer"
              >
                <ChevronsDownUp size={13} />
                {t('dashboard.proxyTab.collapseAll')}
              </button>
            </div>
          </div>

          {visible.length === 0 ? (
            <Card className="p-8 text-center">
              <p className="text-sm text-zinc-500">{t('dashboard.proxyTab.filterEmpty')}</p>
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
