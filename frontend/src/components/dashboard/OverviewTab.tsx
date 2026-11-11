import { AppIcon, type AppIconName } from '@/components/ui/AppIcon'
import { PageHeader } from '@/components/ui/PageHeader'
import { ChainVisualization } from './ChainVisualization'
import type { ReactNode } from 'react'
import { Server, Shield, GitBranch, AlertCircle, Loader2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { useDashboard } from '@/lib/dashboard-context'
import { historyLabel, statusLabel } from '@/lib/deploy-history'
import type { VpsServer, DeployHistoryEntry } from '@/lib/types'
import { getLocationFlag } from '@/lib/country-utils'
import { useI18n } from '@/i18n/i18n-context'
import { cn } from '@/lib/utils'

export function OverviewTab() {
  const { t, pluralHistory } = useI18n()
  const { servers, configs, deployHistory } = useDashboard()

  const onlineCount = servers.filter((s) => s.status === 'online').length
  const offlineCount = servers.filter((s) => s.status === 'offline' || s.status === 'error').length
  const activeConfigs = configs.filter((c) => c.status === 'active')
  const deployingConfigs = configs.filter((c) => c.status === 'deploying')
  const chainConfigsAll = configs.filter((c) => c.hops && c.hops.length >= 2)
  const serversWithVpn = new Set(
    configs.filter((c) => c.status !== 'inactive').flatMap((c) =>
      c.hops?.length ? c.hops.map((h) => h.serverId) : [c.serverId],
    ),
  )
  const unconfiguredOnline = servers.filter(
    (s) => s.status === 'online' && !serversWithVpn.has(s.id),
  )
  const recentHistory = [...deployHistory].sort(
    (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
  )

  return (
    <div className="dashboard-page">
      <PageHeader title={t('dashboard.overview.title')} subtitle={t('dashboard.overview.subtitle')} />

      {chainConfigsAll[0]?.hops && (
        <Card>
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-base font-medium">{t('dashboard.chains.visualization')}</h2>
            <Link to="/chains" className="text-sm text-accent-cyan hover:underline">{t('nav.chains')} →</Link>
          </div>
          <ChainVisualization hops={chainConfigsAll[0].hops} servers={servers} />
        </Card>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard
          to="/servers"
          iconName="servers"
          label={t('nav.servers')}
          value={servers.length}
          sub={t('dashboard.overview.serversSub', { online: onlineCount, offline: offlineCount })}
          color="text-accent-cyan"
        />
        <StatCard
          to="/vpn"
          iconName="lock"
          label="VPN"
          value={activeConfigs.length}
          sub={t('dashboard.overview.deployingCount', { count: deployingConfigs.length })}
          color="text-emerald-400"
        />
        <StatCard
          to="/chains"
          iconName="link2"
          label={t('nav.chains')}
          value={chainConfigsAll.filter((c) => c.status === 'active').length}
          sub={t('dashboard.overview.deployingCount', {
            count: chainConfigsAll.filter((c) => c.status === 'deploying').length,
          })}
          color="text-accent-violet"
        />
        <StatCard
          iconName="deploy2"
          label={t('dashboard.overview.deploys')}
          value={recentHistory.length}
          sub={pluralHistory(recentHistory.length)}
          color="text-amber-400"
        />
      </div>

      {(unconfiguredOnline.length > 0 || offlineCount > 0 || deployingConfigs.length > 0) && (
        <Card className="p-4 border-amber-500/15 bg-amber-500/[0.03]">
          <div className="flex items-start gap-3">
            <AlertCircle size={18} className="text-amber-400 shrink-0 mt-0.5" />
            <div className="min-w-0 space-y-1">
              <p className="text-sm font-medium text-amber-200/90">{t('status.needsAttention')}</p>
              <ul className="text-xs text-zinc-400 space-y-0.5">
                {deployingConfigs.length > 0 && (
                  <li>{t('dashboard.overview.attentionDeploying', { count: deployingConfigs.length })}</li>
                )}
                {offlineCount > 0 && (
                  <li>{t('dashboard.overview.attentionOffline', { count: offlineCount })}</li>
                )}
                {unconfiguredOnline.length > 0 && (
                  <li>
                    {t('dashboard.overview.attentionNoVpn', {
                      count: unconfiguredOnline.length,
                      names: unconfiguredOnline
                        .slice(0, 3)
                        .map((s) => s.name)
                        .join(', ') + (unconfiguredOnline.length > 3
                          ? ` ${t('dashboard.overview.andMore', { count: unconfiguredOnline.length - 3 })}`
                          : ''),
                    })}
                  </li>
                )}
              </ul>
            </div>
          </div>
        </Card>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-stretch">
        <SectionCard title={t('nav.servers')} link="/servers">
          {servers.length === 0 ? (
            <SectionScrollEmpty
              text={t('dashboard.overview.noServers')}
              action="/servers"
              actionLabel={t('dashboard.overview.addVps')}
            />
          ) : (
            <div className="max-h-[23.5rem] overflow-auto">
              <table className="server-table">
                <thead><tr>
                  <th scope="col">{t('nav.servers')}</th>
                  <th scope="col">{t('dashboard.servers.ipAddress')}</th>
                  <th scope="col">{t('nav.status')}</th>
                </tr></thead>
                <tbody>{servers.map((server) => (
                  <ServerRow key={server.id} server={server} hasVpn={serversWithVpn.has(server.id)} />
                ))}</tbody>
              </table>
            </div>
          )}
        </SectionCard>

        <SectionCard title={t('dashboard.overview.deployHistory')}>
          {recentHistory.length === 0 ? (
            <SectionScrollEmpty text={t('dashboard.overview.noDeploys')} />
          ) : (
            <SectionScrollList>
              {recentHistory.map((entry) => (
                <DeployHistoryRow key={entry.id} entry={entry} servers={servers} />
              ))}
            </SectionScrollList>
          )}
        </SectionCard>
      </div>
    </div>
  )
}

function SectionCard({
  title,
  link,
  badge,
  children,
}: {
  title: string
  link?: string
  badge?: ReactNode
  children: ReactNode
}) {
  const { t } = useI18n()

  return (
    <Card className="p-4 sm:p-5 h-full flex flex-col">
      <div className="flex items-center justify-between mb-3 shrink-0">
        <h2 className="font-semibold flex items-center gap-2">
          {title}
          {badge}
        </h2>
        {link && (
          <details className="card-menu relative">
            <summary aria-label={t('common.details')}>•••</summary>
            <div className="dropdown-panel absolute right-0 top-full z-10 mt-2 min-w-32 rounded-lg p-2">
              <Link to={link} className="block rounded-md px-3 py-2 text-xs text-text-secondary hover:bg-white/5">
                {t('common.details')}
              </Link>
            </div>
          </details>
        )}
      </div>
      <div className="flex-1 min-h-0">{children}</div>
    </Card>
  )
}

/** ~6 строк списка — одинаковая высота колонок на обзоре */
const SECTION_LIST_HEIGHT = 'max-h-[23.5rem]'

function SectionScrollList({ children }: { children: ReactNode }) {
  return (
    <div className={cn(SECTION_LIST_HEIGHT, 'overflow-y-auto overscroll-contain pr-1 -mr-1')}>
      <div className="space-y-2">{children}</div>
    </div>
  )
}

function SectionScrollEmpty({
  text,
  action,
  actionLabel,
}: {
  text: string
  action?: string
  actionLabel?: string
}) {
  return (
    <div className={'min-h-56 flex items-center justify-center'}>
      <div className="empty-state px-4">
        <Server size={28} aria-hidden="true" />
        <p className="text-sm text-text-muted">{text}</p>
        {action && actionLabel && (
          <Link to={action} className="inline-block mt-3">
            <Button size="sm">{actionLabel}</Button>
          </Link>
        )}
      </div>
    </div>
  )
}

function ServerRow({ server, hasVpn }: { server: VpsServer; hasVpn: boolean }) {
  const { t } = useI18n()
  const flag = getLocationFlag(server.location)

  return (
    <tr>
      <td><div className="flex items-center gap-2 min-w-0">
        <span className="text-lg shrink-0" aria-hidden="true">{flag}</span>
        <span className="truncate max-w-32">{server.name}</span>
      </div></td>
      <td className="font-mono text-xs text-text-secondary">{server.host}</td>
      <td>{hasVpn ? (
        <Badge variant="success">VPN</Badge>
      ) : server.status === 'online' ? (
        <Badge variant="warning">{t('status.noVpn')}</Badge>
      ) : (
        <StatusLabel status={server.status} />
      )}</td>
    </tr>
  )
}

function DeployHistoryRow({
  entry,
  servers,
}: {
  entry: DeployHistoryEntry
  servers: VpsServer[]
}) {
  const { t, formatRelativeTime } = useI18n()
  const statusVariant =
    entry.status === 'success'
      ? 'success'
      : entry.status === 'error'
        ? 'error'
        : entry.status === 'deploying'
          ? 'warning'
          : 'default'

  return (
    <div className="flex items-start gap-3 p-2.5 rounded-xl surface-panel">
      <div className="w-8 h-8 rounded-lg bg-white/5 flex items-center justify-center shrink-0 mt-0.5">
        {entry.status === 'deploying' ? (
          <Loader2 size={14} className="text-amber-400 animate-spin" />
        ) : entry.type === 'chain' ? (
          <GitBranch size={14} className="text-accent-violet" />
        ) : (
          <Shield size={14} className="text-accent-cyan" />
        )}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-zinc-200 truncate">
          {entry.userName}{' '}
          <span className="text-text-muted">
            · {entry.type === 'chain' ? t('dashboard.overview.historyChain') : t('dashboard.overview.historyVpn')}
          </span>
        </p>
        <p className="text-xs text-text-muted mt-0.5 truncate">{historyLabel(entry, servers)}</p>
        {entry.message && (
          <p className="text-[11px] text-text-muted mt-0.5 truncate">{entry.message}</p>
        )}
      </div>
      <div className="flex flex-col items-end gap-1 shrink-0">
        <Badge variant={statusVariant}>{statusLabel(entry.status)}</Badge>
        <span className="text-[11px] text-text-muted">{formatRelativeTime(entry.createdAt)}</span>
      </div>
    </div>
  )
}

function StatusLabel({ status }: { status: VpsServer['status'] }) {
  const { t } = useI18n()
  const map = {
    online: { variant: 'warning' as const, label: t('status.online') },
    offline: { variant: 'default' as const, label: t('status.offline') },
    connecting: { variant: 'warning' as const, label: t('status.connecting') },
    error: { variant: 'error' as const, label: t('common.error') },
  }
  const item = map[status]
  return <Badge variant={item.variant}>{status === 'online' && <span aria-hidden="true" className="inline-block h-1.5 w-1.5 rounded-full bg-accent-cyan shadow-[0_0_8px_color-mix(in_srgb,var(--color-accent-cyan)_40%,transparent)]" />}{item.label}</Badge>
}

function StatCard({
  to,
  iconName,
  label,
  value,
  sub,
  color,
}: {
  to?: string
  iconName: AppIconName
  label: string
  value: string | number | ReactNode
  sub: string
  color: string
}) {
  const content = (
    <div className="flex items-center gap-3">
      <div className={cn('w-9 h-9 rounded-xl bg-white/5 flex items-center justify-center shrink-0', color)}>
        <AppIcon name={iconName} size={28} />
      </div>
      <div className="min-w-0">
        <p className="text-xs text-text-muted">{label}</p>
        <p className="text-lg font-bold leading-tight">{value}</p>
        <p className="text-[11px] text-text-muted truncate">{sub}</p>
      </div>
    </div>
  )

  if (to) {
    return (
      <Link to={to} className="block group">
        <Card hover className="p-4 h-full">
          {content}
        </Card>
      </Link>
    )
  }

  return (
    <Card className="p-4 h-full">
      {content}
    </Card>
  )
}

