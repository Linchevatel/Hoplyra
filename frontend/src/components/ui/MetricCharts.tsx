import { Children, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface DonutChartProps {
  value: number
  max?: number
  label: string
  sublabel?: string
  size?: number
  stroke?: number
  color?: 'cyan' | 'violet' | 'emerald' | 'amber' | 'rose'
  className?: string
  decimals?: number
}

const COLORS = {
  cyan: { stroke: '#22d3ee', glow: 'rgba(34,211,238,0.35)' },
  violet: { stroke: '#a78bfa', glow: 'rgba(167,139,250,0.35)' },
  emerald: { stroke: '#34d399', glow: 'rgba(52,211,153,0.35)' },
  amber: { stroke: '#fbbf24', glow: 'rgba(251,191,36,0.35)' },
  rose: { stroke: '#fb7185', glow: 'rgba(251,113,133,0.35)' },
}

export function DonutChart({
  value,
  max = 100,
  label,
  sublabel,
  size = 112,
  stroke = 10,
  color = 'cyan',
  className,
  decimals = 0,
}: DonutChartProps) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0
  const radius = (size - stroke) / 2
  const circumference = 2 * Math.PI * radius
  const offset = circumference - (pct / 100) * circumference
  const palette = COLORS[color]
  const centerLabel =
    decimals > 0 ? `${pct.toFixed(decimals)}%` : `${Math.round(pct)}%`

  return (
    <div className={cn('flex flex-col items-center gap-2', className)}>
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="rgba(255,255,255,0.06)"
            strokeWidth={stroke}
          />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={palette.stroke}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            style={{ filter: `drop-shadow(0 0 8px ${palette.glow})` }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
          <span className="text-xl font-bold tabular-nums text-white">{centerLabel}</span>
          {sublabel && <span className="text-[10px] text-zinc-500">{sublabel}</span>}
        </div>
      </div>
      <span className="text-xs font-medium text-zinc-400">{label}</span>
    </div>
  )
}

interface MetricRingsRowProps {
  children: ReactNode
  className?: string
}

export function MetricRingsRow({ children, className }: MetricRingsRowProps) {
  const items = Children.toArray(children).filter(Boolean)

  return (
    <div
      className={cn(
        'grid grid-cols-3 overflow-hidden rounded-2xl border border-white/[0.06] bg-gradient-to-b from-white/[0.03] to-transparent',
        className,
      )}
    >
      {items.map((child, index) => (
        <div
          key={index}
          className={cn(
            'flex items-center justify-center px-1 py-4 sm:py-5 min-w-0',
            index > 0 && 'border-l border-white/[0.08]',
          )}
        >
          {child}
        </div>
      ))}
    </div>
  )
}

interface LoadBarsProps {
  load1?: number | null
  load5?: number | null
  load15?: number | null
  label: string
}

export function LoadBars({ load1, load5, load15, label }: LoadBarsProps) {
  const items = [
    { key: '1m', value: load1 ?? 0, color: 'bg-cyan-400' },
    { key: '5m', value: load5 ?? 0, color: 'bg-violet-400' },
    { key: '15m', value: load15 ?? 0, color: 'bg-emerald-400' },
  ]
  const max = Math.max(...items.map((i) => i.value), 0.01)

  return (
    <div className="space-y-2.5 rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">{label}</p>
      <div className="space-y-2">
        {items.map((item) => (
          <div key={item.key} className="flex items-center gap-3">
            <span className="w-8 text-[10px] uppercase tracking-wide text-zinc-500">{item.key}</span>
            <div className="flex-1 h-2 rounded-full bg-white/[0.06] overflow-hidden">
              <div
                className={cn('h-full rounded-full transition-all duration-500', item.color)}
                style={{ width: `${Math.min(100, (item.value / max) * 100)}%` }}
              />
            </div>
            <span className="w-10 text-right text-xs tabular-nums text-zinc-300">
              {item.value != null ? item.value.toFixed(2) : '—'}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

interface NetworkThroughputProps {
  rxBps?: number | null
  txBps?: number | null
  label: string
  downLabel: string
  upLabel: string
  format: (bps?: number | null) => string
}

export function NetworkThroughput({
  rxBps,
  txBps,
  label,
  downLabel,
  upLabel,
  format,
}: NetworkThroughputProps) {
  const rx = rxBps ?? 0
  const tx = txBps ?? 0
  const max = Math.max(rx, tx, 1024)

  return (
    <div className="space-y-2.5 rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">{label}</p>
      <div className="space-y-2">
        <div className="flex items-center gap-3">
          <span className="w-14 text-[10px] uppercase tracking-wide text-emerald-400/90 shrink-0">
            {downLabel}
          </span>
          <div className="flex-1 h-2 rounded-full bg-white/[0.06] overflow-hidden">
            <div
              className="h-full rounded-full bg-emerald-400/90 transition-all duration-500"
              style={{ width: `${Math.min(100, (rx / max) * 100)}%` }}
            />
          </div>
          <span className="w-20 text-right text-xs tabular-nums text-zinc-300 shrink-0">
            {format(rxBps)}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="w-14 text-[10px] uppercase tracking-wide text-sky-400/90 shrink-0">
            {upLabel}
          </span>
          <div className="flex-1 h-2 rounded-full bg-white/[0.06] overflow-hidden">
            <div
              className="h-full rounded-full bg-sky-400/90 transition-all duration-500"
              style={{ width: `${Math.min(100, (tx / max) * 100)}%` }}
            />
          </div>
          <span className="w-20 text-right text-xs tabular-nums text-zinc-300 shrink-0">
            {format(txBps)}
          </span>
        </div>
      </div>
    </div>
  )
}
