import { ArrowRight, GitBranch } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { ProtocolIcon } from '@/components/ui/ProtocolIcon'
import type { ChainHop, VpnConfig, VpsServer } from '@/lib/types'
import { PROTOCOLS } from '@/lib/constants'
import { getLocationFlag } from '@/lib/country-utils'
import { useI18n } from '@/i18n/I18nProvider'
import { cn } from '@/lib/utils'

function ConfigStatusBadge({ status }: { status: VpnConfig['status'] }) {
  const { t } = useI18n()
  const map = {
    active: { variant: 'success' as const, label: t('status.active') },
    deploying: { variant: 'warning' as const, label: t('common.deploying') },
    error: { variant: 'error' as const, label: t('common.error') },
    inactive: { variant: 'default' as const, label: t('common.off') },
  }
  const item = map[status]
  return <Badge variant={item.variant}>{item.label}</Badge>
}

function ChainRouteVisual({ hops, servers }: { hops: ChainHop[]; servers: VpsServer[] }) {
  const { t } = useI18n()

  return (
    <div className="flex flex-wrap items-center gap-y-2 gap-x-1">
      {hops.map((hop, i) => {
        const hopServer = servers.find((s) => s.id === hop.serverId)
        const isEntry = i === 0
        const isExit = i === hops.length - 1

        return (
          <span key={hop.id} className="flex items-center gap-1">
            {i > 0 && (
              <ArrowRight size={14} className="text-violet-400/40 shrink-0 mx-0.5" aria-hidden />
            )}
            <span
              className={cn(
                'inline-flex items-center gap-2 pl-2 pr-3 py-2 rounded-xl border transition-colors',
                isEntry && 'border-cyan-500/30 bg-cyan-500/[0.07] shadow-[0_0_12px_rgba(34,211,238,0.08)]',
                isExit && !isEntry && 'border-violet-500/25 bg-violet-500/[0.06]',
                !isEntry && !isExit && 'border-white/8 bg-white/[0.03]',
              )}
            >
              <ProtocolIcon protocol={hop.protocol} size="xs" withGradient title={PROTOCOLS[hop.protocol].label} />
              {hopServer && (
                <span className="inline-flex items-center gap-1.5 min-w-0">
                  <span className="text-sm leading-none shrink-0">{getLocationFlag(hopServer.location)}</span>
                  <span className="text-xs text-zinc-300 truncate max-w-[7rem] sm:max-w-[9rem]">
                    {hopServer.name}
                  </span>
                </span>
              )}
              {isEntry && (
                <span className="text-[9px] uppercase tracking-wider text-cyan-400/70 font-semibold shrink-0">
                  {t('common.entry')}
                </span>
              )}
              {isExit && hops.length > 1 && (
                <span className="text-[9px] uppercase tracking-wider text-violet-400/70 font-semibold shrink-0">
                  {t('common.exit')}
                </span>
              )}
            </span>
          </span>
        )
      })}
    </div>
  )
}

export function ClientConfigCard({
  config,
  server,
  servers,
  onOpen,
}: {
  config: VpnConfig
  server: VpsServer
  servers: VpsServer[]
  onOpen: () => void
}) {
  const { t } = useI18n()
  const isChain = Boolean(config.hops && config.hops.length >= 2)

  return (
    <div className="rounded-2xl border border-white/5 surface-panel hover:border-white/10 transition-colors">
      <div className="flex items-center gap-5 px-6 py-6 min-h-[5.5rem]">
        <ProtocolIcon protocol={config.protocol} size="md" withGradient />

        <div className="flex-1 min-w-0 grid grid-cols-1 md:grid-cols-[minmax(0,240px)_1fr] lg:grid-cols-[minmax(0,280px)_1fr] gap-x-10 gap-y-3 items-center">
          <div className="min-w-0">
            <p className="text-base font-medium truncate flex items-center gap-2">
              <span className="text-lg leading-none">{getLocationFlag(server.location)}</span>
              {server.name}
            </p>
            <div className="flex flex-wrap items-center gap-2 mt-2">
              <span className="text-sm text-zinc-500">{PROTOCOLS[config.protocol].label}</span>
              <ConfigStatusBadge status={config.status} />
              {isChain && (
                <Badge variant="default" className="text-violet-300/90">
                  <GitBranch size={10} className="mr-1" />
                  {t('common.chain')}
                </Badge>
              )}
            </div>
          </div>

          <div className="min-w-0 py-1">
            {isChain && config.hops ? (
              <ChainRouteVisual hops={config.hops} servers={servers} />
            ) : (
              <p className="text-sm text-zinc-500 font-mono truncate">{server.host}</p>
            )}
          </div>
        </div>

        <Button variant="secondary" size="sm" onClick={onOpen} className="shrink-0">
          {t('common.expand')}
        </Button>
      </div>
    </div>
  )
}
