import { useState } from 'react'
import { Activity, Gauge, Shield, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { ProtocolIcon } from '@/components/ui/ProtocolIcon'
import type { ChainHop, RouteTestResult, VpsServer } from '@/lib/types'
import { testRoute } from '@/lib/route-test'
import { PROTOCOLS } from '@/lib/constants'
import { useI18n } from '@/i18n/I18nProvider'
import { cn } from '@/lib/utils'

interface RouteTestPanelProps {
  hops: ChainHop[]
  servers: VpsServer[]
  disabled?: boolean
}

export function RouteTestPanel({ hops, servers, disabled }: RouteTestPanelProps) {
  const { t } = useI18n()
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<RouteTestResult | null>(null)

  async function handleTest() {
    if (hops.length < 2) return
    setTesting(true)
    setResult(null)
    const res = await testRoute(hops, servers)
    setResult(res)
    setTesting(false)
  }

  return (
    <div className="rounded-xl border border-white/5 bg-white/[0.02] p-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-3">
        <div>
          <h4 className="text-sm font-semibold flex items-center gap-2">
            <Activity size={16} className="text-cyan-400" />
            {t('dashboard.routeTest.title')}
          </h4>
          <p className="text-xs text-zinc-500 mt-0.5">
            {t('dashboard.routeTest.subtitle')}
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={handleTest}
          disabled={disabled || testing || hops.length < 2}
        >
          {testing ? (
            <>
              <Loader2 size={14} className="animate-spin" />
              {t('common.testing')}
            </>
          ) : (
            <>
              <Gauge size={14} />
              {t('dashboard.routeTest.testChain')}
            </>
          )}
        </Button>
      </div>

      {result && (
        <div className="space-y-2 mt-3 pt-3 border-t border-white/5">
          <div className="flex items-center justify-between text-xs">
            <span className={result.ok ? 'text-emerald-400' : 'text-red-400'}>
              {result.ok ? t('dashboard.routeTest.routeOk') : t('dashboard.routeTest.issues')}
            </span>
            <span className="text-zinc-500">Σ {result.totalLatencyMs} ms</span>
          </div>
          {result.hops.map((hop, i) => {
            const server = servers.find((s) => s.id === hop.serverId)
            return (
              <div
                key={hop.hopId}
                className={cn(
                  'flex items-center gap-3 p-2 rounded-lg text-xs',
                  hop.reachable ? 'surface-panel' : 'bg-red-500/5 border border-red-500/20',
                )}
              >
                <span className="text-zinc-600 w-4">{i + 1}</span>
                <ProtocolIcon protocol={hop.protocol} size="xs" withGradient />
                <span className="flex-1 truncate text-zinc-300">{server?.name ?? '—'}</span>
                {hop.reachable ? (
                  <Badge variant="success">{hop.latencyMs} ms</Badge>
                ) : (
                  <Badge variant="error">{hop.error ?? t('common.error')}</Badge>
                )}
              </div>
            )
          })}
        </div>
      )}

      {hops.length >= 2 && !result && !testing && (
        <p className="text-[11px] text-zinc-600 flex items-center gap-1.5">
          <Shield size={12} />
          {hops.map((h) => PROTOCOLS[h.protocol].shortLabel).join(' → ')}
        </p>
      )}
    </div>
  )
}
