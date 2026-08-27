import { PROTOCOLS, FREE_PROTOCOLS } from '@/lib/constants'
import { ProtocolIcon } from '@/components/ui/ProtocolIcon'
import type { Protocol } from '@/lib/types'
import { useI18n } from '@/i18n/I18nProvider'
import { useAllProtocolMeta } from '@/i18n/useProtocolMeta'
import { cn } from '@/lib/utils'

export function ProtocolComparisonTable({ compact }: { compact?: boolean }) {
  const { t } = useI18n()
  const meta = useAllProtocolMeta()

  return (
    <div className={cn('overflow-x-auto', compact ? '' : 'glass rounded-2xl p-4 sm:p-6')}>
      {!compact && (
        <>
          <h3 className="text-lg font-semibold mb-1">{t('protocol.comparison.title')}</h3>
          <p className="text-sm text-zinc-500 mb-1">
            {t('protocol.comparison.subtitle')}
          </p>
          <p className="text-xs text-zinc-600 mb-4">
            {t('protocol.comparison.note')}
          </p>
        </>
      )}
      <table className="w-full text-sm min-w-[560px]">
        <thead>
          <tr className="text-left text-xs text-zinc-500 border-b border-white/5">
            <th className="pb-2 pr-3 font-medium">{t('protocol.comparison.colProtocol')}</th>
            <th className="pb-2 px-2 font-medium">{t('protocol.comparison.colBestFor')}</th>
            <th className="pb-2 px-2 font-medium whitespace-nowrap">{t('protocol.comparison.colSetup')}</th>
            <th className="pb-2 pl-2 font-medium whitespace-nowrap">{t('protocol.comparison.colQr')}</th>
          </tr>
        </thead>
        <tbody>
          {FREE_PROTOCOLS.map((key) => {
            const p = meta[key as Protocol]
            return (
              <tr key={key} className="border-b border-white/5 last:border-0 align-top">
                <td className="py-3 pr-3">
                  <span className="flex items-center gap-2">
                    <ProtocolIcon protocol={key} size="sm" withGradient />
                    <span>
                      <span className="font-medium text-zinc-200 block">{p.label}</span>
                      <span className="text-[11px] text-zinc-500">{p.description}</span>
                    </span>
                  </span>
                </td>
                <td className="py-3 px-2 text-xs text-zinc-400 leading-relaxed max-w-[220px]">
                  {p.bestFor}
                </td>
                <td className="py-3 px-2 text-xs text-zinc-400 whitespace-nowrap">
                  {p.setup}
                </td>
                <td className="py-3 pl-2 text-xs text-zinc-400 whitespace-nowrap">
                  {PROTOCOLS[key as Protocol].qrConfig ? t('common.yes') : t('common.manual')}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

interface ProtocolSelectCardProps {
  protocol: Protocol
  selected: boolean
  onSelect: () => void
}

export function ProtocolSelectCard({ protocol, selected, onSelect }: ProtocolSelectCardProps) {
  const meta = useAllProtocolMeta()
  const p = meta[protocol]

  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        'relative flex flex-col rounded-2xl border p-4 text-left transition-all cursor-pointer h-full',
        selected
          ? 'border-cyan-500/40 bg-gradient-to-b from-cyan-500/[0.08] to-transparent shadow-[0_0_0_1px_rgba(34,211,238,0.15)]'
          : 'surface-panel glow-border-hover',
      )}
    >
      {selected && (
        <span className="absolute top-3 right-3 w-5 h-5 rounded-full bg-cyan-500 flex items-center justify-center">
          <svg viewBox="0 0 12 12" className="w-3 h-3 text-black" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M2 6l3 3 5-5" />
          </svg>
        </span>
      )}

      <ProtocolIcon protocol={protocol} size="lg" withGradient className="mb-3" />

      <div className="flex items-center gap-2 mb-1">
        <p className="font-semibold text-sm text-zinc-100">{p.label}</p>
        <span className="text-[10px] font-mono text-zinc-500 px-1.5 py-0.5 rounded bg-white/5">
          {p.shortLabel}
        </span>
      </div>

      <p className="text-xs text-zinc-400 leading-relaxed flex-1">{p.description}</p>

      <p className="text-[11px] text-zinc-500 mt-2 leading-snug">{p.bestFor}</p>

      <div className="flex flex-wrap gap-1 mt-3 pt-3 border-t border-white/5">
        {p.traits.map((trait) => (
          <span
            key={trait}
            className="text-[10px] px-2 py-0.5 rounded-md bg-white/[0.04] text-zinc-500 border border-white/5"
          >
            {trait}
          </span>
        ))}
      </div>
    </button>
  )
}
