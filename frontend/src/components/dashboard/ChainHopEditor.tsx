import { AwgAdvanced } from './AwgAdvanced'
import { Reorder } from 'framer-motion'
import { ArrowDown, GripVertical, X } from 'lucide-react'
import { ProtocolSelect, ServerSelect } from '@/components/dashboard/ChainSelects'
import { PROTOCOLS } from '@/lib/constants'
import { getBlockedProtocolsForHop, getHopDisplayRole } from '@/lib/chain-utils'
import type { ChainHop, VpsServer } from '@/lib/types'
import { cn } from '@/lib/utils'
import { getLocationFlag } from '@/lib/country-utils'
import { getDisabledServersForHop, getInvalidConsecutivePairs } from '@/lib/chain-utils'
import { ProtocolIcon } from '@/components/ui/ProtocolIcon'
import { Checkbox } from '@/components/ui/Checkbox'
import { useI18n } from '@/i18n/i18n-context'

interface ChainHopEditorProps {
  hops: ChainHop[]
  servers: VpsServer[]
  chainLockedServerIds?: string[]
  onChange: (hops: ChainHop[]) => void
  onRemove: (hopId: string) => void
}

interface HopItemProps {
  hop: ChainHop
  index: number
  total: number
  onlineServers: VpsServer[]
  invalidPair: boolean
  disabledServerIds: string[]
  chainLockedServerIds: string[]
  onUpdate: (hopId: string, patch: Partial<Pick<ChainHop, 'protocol' | 'serverId' | 'transport' | 'xrayBypass' | 'awgVersion' | 'awgMode' | 'awgParams'>>) => void
  onRemove: (hopId: string) => void
}

function HopItem({
  hop,
  index,
  total,
  onlineServers,
  invalidPair,
  disabledServerIds,
  chainLockedServerIds,
  onUpdate,
  onRemove,
}: HopItemProps) {
  const { t } = useI18n()

  return (
    <Reorder.Item
      value={hop}
      as="div"
      className="min-w-0 overflow-visible list-none select-none cursor-grab active:cursor-grabbing touch-none"
      whileDrag={{
        scale: 1.02,
        boxShadow: '0 12px 40px rgba(0,0,0,0.45)',
        zIndex: 50,
        cursor: 'grabbing',
      }}
      transition={{ layout: { duration: 0.2 } }}
    >
      <div
        className={cn(
          'hop-card glass relative min-w-0 overflow-visible p-3 sm:p-4 transition-colors',
          invalidPair
            ? 'hop-card-invalid'
            : index === 0
              ? 'hop-card-accent'
              : index === total - 1
                ? 'hop-card-accent'
                : '',
        )}
      >
        <div className="flex items-start justify-between gap-3 mb-2">
          <div className="flex items-center gap-2">
            <GripVertical size={18} className="text-text-muted shrink-0" aria-hidden />
            <span className="w-6 h-6 rounded-lg bg-white/10 flex items-center justify-center text-xs font-bold text-zinc-400">
              {index + 1}
            </span>
            <span className="text-xs text-text-muted uppercase tracking-wider">
              {getHopDisplayRole(hop, index, total)}
            </span>
          </div>
          <button
            type="button"
            onClick={() => onRemove(hop.id)}
            onPointerDown={(e) => e.stopPropagation()}
            className="shrink-0 p-1 text-text-muted hover:text-rose-400 transition-colors cursor-pointer touch-auto"
            aria-label={t('dashboard.chains.removeHop')}
          >
            <X size={16} />
          </button>
        </div>

        <div className="hop-fields grid min-w-0 grid-cols-1 sm:grid-cols-2 gap-2.5 [&>*]:min-w-0">
          <ServerSelect
            value={hop.serverId}
            servers={onlineServers}
            disabledServerIds={disabledServerIds}
            chainLockedServerIds={chainLockedServerIds}
            onChange={(serverId) => onUpdate(hop.id, { serverId })}
            onPointerDown={(e) => e.stopPropagation()}
          />

          <ProtocolSelect
            value={hop.protocol}
            onChange={(protocol) =>
              onUpdate(hop.id, {
                protocol,
                transport: protocol === 'openvpn' ? hop.transport ?? 'udp' : undefined,
                xrayBypass: protocol === 'xray' ? hop.xrayBypass ?? true : undefined,
                awgVersion: protocol === 'awg' ? hop.awgVersion ?? 'awg3.1' : undefined,
              })
            }
            onPointerDown={(e) => e.stopPropagation()}
            excludeProtocols={getBlockedProtocolsForHop(index)}
          />
        </div>

        {hop.protocol === 'awg' && (
          <div className="mt-3 flex items-center gap-2 flex-wrap">
            <span className="text-xs text-text-muted shrink-0">{t('ui.awgTitle')}:</span>
            <div className="inline-flex max-w-full flex-wrap rounded-lg border border-white/10 p-0.5 gap-0.5">
              {([
                { id: 'awg3.1', label: 'AWG 3.1' },
                { id: 'awg2.0', label: 'AWG 2.0' },
                { id: 'awg1.5', label: 'AWG 1.5' },
                { id: 'awg', label: 'AWG 1.0' },
              ] as const).map((v) => (
                <button
                  key={v.id}
                  type="button"
                  onClick={() => onUpdate(hop.id, { awgVersion: v.id, awgMode: v.id === 'awg3.1' ? 'none' : 'auto', awgParams: undefined })}
                  onPointerDown={(e) => e.stopPropagation()}
                  className={cn(
                    'px-2.5 py-1 rounded text-xs font-medium transition-colors cursor-pointer',
                    (hop.awgVersion ?? 'awg3.1') === v.id
                      ? 'bg-emerald-500/20 text-emerald-300'
                      : 'text-zinc-400 hover:text-zinc-200',
                  )}
                >
                  {v.label}
                </button>
              ))}
            </div>
          </div>
        )}


        {hop.protocol === 'awg' && <AwgAdvanced version={hop.awgVersion ?? 'awg3.1'} mode={hop.awgMode ?? (hop.awgParams ? 'custom' : 'auto')} onModeChange={(awgMode, awgParams) => onUpdate(hop.id, { awgMode, awgParams })} value={hop.awgParams} onChange={awgParams => onUpdate(hop.id, { awgParams })} />}

        {hop.protocol === 'xray' && (
          <div className="mt-3 rounded-lg border border-accent-violet/15 bg-accent-violet/[0.04] px-3 py-2.5">
            <Checkbox
              checked={hop.xrayBypass ?? false}
              onChange={(checked) => onUpdate(hop.id, { xrayBypass: checked })}
              onPointerDown={(e) => e.stopPropagation()}
              label={t('dashboard.chains.xrayBypass')}
              size="sm"
              accent="violet"
            />
          </div>
        )}

        {hop.protocol === 'openvpn' && (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className="text-xs text-text-muted shrink-0">{t('dashboard.chains.transportLabel')}</span>
            <div className="transport-switch inline-flex max-w-full flex-wrap p-0.5 gap-0.5">
              {(['udp', 'tcp'] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => onUpdate(hop.id, { transport: t })}
                  onPointerDown={(e) => e.stopPropagation()}
                  className={cn(
                    'px-3 py-1.5 rounded-md text-xs font-medium transition-colors cursor-pointer',
                    (hop.transport ?? 'udp') === t
                      ? 'transport-active'
                      : 'text-zinc-400 hover:text-zinc-200',
                  )}
                >
                  {t.toUpperCase()}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {index < total - 1 && (
        <div className="flex justify-center py-2 pointer-events-none">
          <div className="flex flex-col items-center gap-0.5 text-text-muted">
            <ArrowDown size={16} />
            <span className="text-[10px] uppercase tracking-wider">{t('common.tunnel')}</span>
          </div>
        </div>
      )}
    </Reorder.Item>
  )
}

export function ChainHopEditor({ hops, servers, chainLockedServerIds = [], onChange, onRemove }: ChainHopEditorProps) {
  const { t } = useI18n()
  const onlineServers = servers.filter((s) => s.status === 'online')
  const invalidPairs = getInvalidConsecutivePairs(hops)
  const invalidHopIds = new Set(
    invalidPairs.flatMap((i) => [hops[i].id, hops[i + 1].id]),
  )
  if (hops[0]?.protocol === 'tor') {
    invalidHopIds.add(hops[0].id)
  }
  const last = hops[hops.length - 1]
  if (last?.protocol === 'tor' && hops.length === 1) {
    invalidHopIds.add(last.id)
  }

  function updateHop(hopId: string, patch: Partial<Pick<ChainHop, 'protocol' | 'serverId' | 'transport' | 'xrayBypass' | 'awgVersion' | 'awgMode' | 'awgParams'>>) {
    onChange(hops.map((hop) => (hop.id === hopId ? { ...hop, ...patch } : hop)))
  }

  if (hops.length === 0) {
    return null
  }

  return (
    <div>
      <p className="text-xs text-text-muted mb-3">
        {t('dashboard.chains.hopEditorHint')}
      </p>
      <Reorder.Group axis="y" values={hops} onReorder={onChange} className="min-w-0 space-y-0">
        {hops.map((hop, index) => (
          <HopItem
            key={hop.id}
            hop={hop}
            index={index}
            total={hops.length}
            onlineServers={onlineServers}
            invalidPair={invalidHopIds.has(hop.id)}
            disabledServerIds={getDisabledServersForHop(hops, index)}
            chainLockedServerIds={chainLockedServerIds}
            onUpdate={updateHop}
            onRemove={onRemove}
          />
        ))}
      </Reorder.Group>
    </div>
  )
}

interface ChainPreviewProps {
  hops: ChainHop[]
  servers: VpsServer[]
  compact?: boolean
}

export function ChainPreview({ hops, servers, compact }: ChainPreviewProps) {
  if (hops.length === 0) return null

  return (
    <div className={cn('flex flex-wrap items-center gap-2', compact && 'text-xs')}>
      {hops.map((hop, i) => {
        const server = servers.find((s) => s.id === hop.serverId)
        return (
          <span key={hop.id} className="flex items-center gap-2">
            {i > 0 && <span className="text-text-muted">→</span>}
            <span className="inline-flex flex-col sm:flex-row sm:items-center gap-0.5 sm:gap-2 px-2.5 py-1.5 rounded-lg surface-panel">
              <ProtocolIcon protocol={hop.protocol} size="xs" withGradient title={PROTOCOLS[hop.protocol]?.label ?? hop.protocol} />
              {hop.protocol === 'openvpn' && (
                <span className="text-[10px] text-orange-300/80 uppercase">
                  {(hop.transport ?? 'udp')}
                </span>
              )}
              {!compact && server && (
                <span className="inline-flex items-center gap-1.5 text-text-muted text-[11px] sm:border-l sm:border-white/10 sm:pl-2">
                  <span className="text-sm leading-none">{getLocationFlag(server.location)}</span>
                  {server.name}
                </span>
              )}
              {compact && server && (
                <span className="text-sm leading-none">{getLocationFlag(server.location)}</span>
              )}
            </span>
          </span>
        )
      })}
    </div>
  )
}
