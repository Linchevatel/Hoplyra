import { SCENARIO_PRESETS, hopsFromScenario } from '@/lib/scenario-presets'
import type { VpsServer } from '@/lib/types'
import type { ChainHop } from '@/lib/types'
import { ProtocolIcon } from '@/components/ui/ProtocolIcon'
import { ArrowRight } from 'lucide-react'
import { useI18n } from '@/i18n/I18nProvider'

interface ScenarioPresetsPanelProps {
  servers: VpsServer[]
  onApply: (hops: ChainHop[]) => void
  disabled?: boolean
}

export function ScenarioPresetsPanel({ servers, onApply, disabled }: ScenarioPresetsPanelProps) {
  const { t } = useI18n()
  const online = servers.filter((s) => s.status === 'online')

  return (
    <div className="rounded-xl border border-violet-500/15 bg-violet-500/[0.03] p-4">
      <h4 className="text-sm font-semibold mb-1">{t('common.scenarios')}</h4>
      <p className="text-xs text-zinc-500 mb-3">{t('dashboard.scenarios.subtitle')}</p>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        {SCENARIO_PRESETS.map((scenario) => (
          <button
            key={scenario.id}
            type="button"
            disabled={disabled || online.length === 0}
            onClick={() => onApply(hopsFromScenario(scenario, online))}
            className="flex flex-col gap-2 p-2.5 rounded-lg surface-panel glow-border-hover text-left cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <span className="text-xs font-medium text-zinc-200">{t(`scenario.${scenario.id}.name`)}</span>
            <span className="text-[11px] text-zinc-500 leading-snug">{t(`scenario.${scenario.id}.desc`)}</span>
            <span className="flex items-center gap-0.5 mt-auto">
              {scenario.protocols.map((p, i) => (
                <span key={i} className="flex items-center gap-0.5">
                  {i > 0 && <ArrowRight size={8} className="text-zinc-600" />}
                  <ProtocolIcon protocol={p} size="xs" withGradient />
                </span>
              ))}
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}
