import { MapPin } from 'lucide-react'
import { Select, type SelectOption } from '@/components/ui/Select'
import { PROTOCOLS, FREE_PROTOCOLS } from '@/lib/constants'
import type { Protocol, VpsServer } from '@/lib/types'
import { cn } from '@/lib/utils'
import { getLocationFlag, getLocationCity, countryCodeFromLocation } from '@/lib/country-utils'
import { ProtocolIcon } from '@/components/ui/ProtocolIcon'
import { useI18n } from '@/i18n/I18nProvider'

function ServerFlag({ server, size = 'md' }: { server: VpsServer; size?: 'sm' | 'md' }) {
  const flag = getLocationFlag(server.location)
  const box = size === 'sm' ? 'h-7 w-7 text-lg' : 'h-8 w-8 text-xl'

  return (
    <span className={cn('relative flex shrink-0 items-center justify-center rounded-lg surface-panel leading-none', box)}>
      {flag}
      <span
        className={cn(
          'absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full border-2 border-surface-850',
          server.status === 'online' ? 'bg-emerald-400' : 'bg-zinc-500',
        )}
      />
    </span>
  )
}

function serverLocationLabel(server: VpsServer): string | undefined {
  const countryCode = countryCodeFromLocation(server.location)?.toUpperCase()
  const city = getLocationCity(server.location)
  const label = [countryCode, city].filter(Boolean).join(' · ')
  return label || server.location
}

interface ServerSelectProps {
  value: string
  servers: VpsServer[]
  disabledServerIds?: string[]
  chainLockedServerIds?: string[]
  onChange: (serverId: string) => void
  onPointerDown?: (e: React.PointerEvent) => void
}

export function ServerSelect({
  value,
  servers,
  disabledServerIds = [],
  chainLockedServerIds = [],
  onChange,
  onPointerDown,
}: ServerSelectProps) {
  const { t } = useI18n()
  const disabledSet = new Set(disabledServerIds)
  const chainLockedSet = new Set(chainLockedServerIds)

  const options: SelectOption[] = servers.map((s) => ({
    value: s.id,
    label: s.name,
    description: serverLocationLabel(s),
    meta: chainLockedSet.has(s.id)
      ? t('dashboard.chainSelect.chainLocked')
      : disabledSet.has(s.id)
        ? t('dashboard.chainSelect.sameVpsBlocked')
        : s.host,
    disabled: disabledSet.has(s.id) || chainLockedSet.has(s.id),
    leading: <ServerFlag server={s} />,
  }))

  const selected = servers.find((s) => s.id === value)

  return (
    <div>
      <Select
        label={t('dashboard.chainSelect.vpsServer')}
        value={value}
        onChange={onChange}
        options={options}
        placeholder={t('dashboard.chainSelect.selectServer')}
        emptyMessage={t('dashboard.chainSelect.noOnline')}
        onPointerDown={onPointerDown}
        renderValue={(option) =>
          option && selected ? (
            <span className="flex items-center gap-2.5 min-w-0">
              <ServerFlag server={selected} size="sm" />
              <span className="min-w-0">
                <span className="block truncate text-zinc-200">{option.label}</span>
                {option.description && (
                  <span className="block text-[11px] text-zinc-500 truncate">{option.description}</span>
                )}
              </span>
            </span>
          ) : null
        }
        renderOption={(option, isSelected) => (
          <>
            {option.leading}
            <span className="flex-1 min-w-0">
              <span className={cn('text-sm font-medium truncate', isSelected ? 'text-white' : 'text-zinc-200')}>
                {option.label}
              </span>
              <span className="flex items-center gap-2 mt-0.5">
                {option.description && (
                  <span className="inline-flex items-center gap-1 text-xs text-zinc-500 truncate">
                    <MapPin size={10} />
                    {option.description}
                  </span>
                )}
              </span>
              {option.meta && (
                <span className="block text-[11px] text-zinc-600 font-mono truncate mt-0.5">{option.meta}</span>
              )}
            </span>
            {isSelected && (
              <span className="shrink-0 w-5 h-5 rounded-full bg-cyan-500/20 flex items-center justify-center">
                <span className="w-2 h-2 rounded-full bg-cyan-400" />
              </span>
            )}
          </>
        )}
        className="relative"
      />
      {selected && (
        <p className="text-[11px] text-zinc-600 font-mono mt-1.5 px-1">{selected.host}</p>
      )}
    </div>
  )
}

interface ProtocolSelectProps {
  value: Protocol
  onChange: (protocol: Protocol) => void
  onPointerDown?: (e: React.PointerEvent) => void
  excludeProtocols?: Protocol[]
}

export function ProtocolSelect({ value, onChange, onPointerDown, excludeProtocols = [] }: ProtocolSelectProps) {
  const { t } = useI18n()
  const excluded = new Set(excludeProtocols)
  const options: SelectOption[] = FREE_PROTOCOLS.filter((p) => !excluded.has(p)).map((p) => ({
    value: p,
    label: PROTOCOLS[p].label,
    description: PROTOCOLS[p].description,
    leading: <ProtocolIcon protocol={p} size="md" withGradient />,
  }))

  const current = PROTOCOLS[value]

  return (
    <Select
      label={t('common.protocol')}
      value={value}
      onChange={(v) => onChange(v as Protocol)}
      options={options}
      onPointerDown={onPointerDown}
      renderValue={() => (
        <span className="flex items-center gap-2.5 min-w-0">
          <ProtocolIcon protocol={value} size="sm" withGradient />
          <span className="truncate text-zinc-200">{current.label}</span>
        </span>
      )}
      renderOption={(option, isSelected) => (
          <>
            {option.leading}
            <span className="flex-1 min-w-0">
              <span className={cn('text-sm font-medium', isSelected ? 'text-white' : 'text-zinc-200')}>
                {option.label}
              </span>
              {option.description && (
                <span className="block text-xs text-zinc-500 mt-0.5 leading-snug">{option.description}</span>
              )}
            </span>
            {isSelected && <ProtocolIcon protocol={option.value as Protocol} size="xs" withGradient />}
          </>
        )}
    />
  )
}
