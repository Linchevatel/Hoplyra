import type { ChainHop, VpsServer } from '@/lib/types'
import { getLocationFlag } from '@/lib/country-utils'
import { PROTOCOLS } from '@/lib/constants'
import { useI18n } from '@/i18n/i18n-context'

/** A read-only view of real hops; missing measurements are never fabricated. */
export function ChainVisualization({ hops, servers }: { hops: ChainHop[]; servers: VpsServer[] }) {
  const { t } = useI18n()
  if (!hops.length) return null
  return (
    <div className="chain-visualization" role="group" aria-label={t('dashboard.chains.visualization')}>
      <ol className="chain-track">
        {hops.map((hop) => {
          const server = servers.find((item) => item.id === hop.serverId)
          return (
            <li key={hop.id} className="chain-node">
              <span className="max-w-full truncate px-2 text-sm text-text-primary" title={server?.location ?? server?.name}>
                {getLocationFlag(server?.location)} {server?.location ?? server?.name ?? PROTOCOLS[hop.protocol].label}
              </span>
              <span className="chain-orb" aria-hidden="true" />
              <span className="flex flex-col gap-1 text-xs text-text-secondary">
                <span>{PROTOCOLS[hop.protocol].label}</span>
                <span className="font-mono tabular-nums">{server?.latencyMs != null ? `${server.latencyMs} ms` : '—'}</span>
              </span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
