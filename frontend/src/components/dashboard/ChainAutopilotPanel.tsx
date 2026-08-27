import { Zap, Eye } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import type { ChainGoal, ChainHop, VpsServer } from '@/lib/types'
import { buildAutoChain } from '@/lib/chain-autopilot'
import { useI18n } from '@/i18n/I18nProvider'

interface ChainAutopilotPanelProps {
  servers: VpsServer[]
  onApply: (hops: ChainHop[]) => void
  disabled?: boolean
}

export function ChainAutopilotPanel({ servers, onApply, disabled }: ChainAutopilotPanelProps) {
  const { t } = useI18n()

  function apply(goal: ChainGoal, hops: 2 | 3) {
    const built = buildAutoChain(goal, servers, hops)
    if (built.length >= 2) onApply(built)
  }

  return (
    <div className="rounded-xl border border-white/5 bg-white/[0.02] p-4">
      <h4 className="text-sm font-semibold mb-1">{t('dashboard.autopilot.title')}</h4>
      <p className="text-xs text-zinc-500 mb-3">{t('dashboard.autopilot.subtitle')}</p>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="secondary"
          size="sm"
          disabled={disabled}
          onClick={() => apply('latency', 2)}
        >
          <Zap size={14} />
          {t('dashboard.autopilot.minLatency')}
        </Button>
        <Button
          variant="secondary"
          size="sm"
          disabled={disabled}
          onClick={() => apply('anonymity', 2)}
        >
          <Eye size={14} />
          {t('dashboard.autopilot.maxAnonymity')}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={() => apply('anonymity', 3)}
          className="text-xs"
        >
          {t('dashboard.autopilot.privacy3')}
        </Button>
      </div>
    </div>
  )
}
