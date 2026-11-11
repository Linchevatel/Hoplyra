import { useEffect, useRef, useState, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  ChevronDown,
  Eye,
  EyeOff,
  Info,
  SlidersHorizontal,
} from 'lucide-react'
import { useI18n } from '@/i18n/i18n-context'
import { generateAwg } from '@/lib/api'
import {
  normalizeGeneratedAwg,
  validateAwg,
  versionParams,
  v31Fields,
  type AwgParams,
} from '@/lib/awg'
import { Badge } from '@/components/ui/Badge'
import { Checkbox } from '@/components/ui/Checkbox'
import { Input } from '@/components/ui/Input'
import { Select, type SelectOption } from '@/components/ui/Select'
import { cn } from '@/lib/utils'

interface AwgAdvancedProps {
  version: string
  mode: 'none' | 'auto' | 'custom'
  onModeChange: (mode: 'none' | 'auto' | 'custom', params?: AwgParams) => void
  value?: AwgParams
  onChange: (value: AwgParams | undefined) => void
}

function FieldLabel({
  label,
  tooltip,
  disabledReason,
}: {
  label: string
  tooltip?: string
  disabledReason?: string
}) {
  return (
    <span className="flex items-center justify-between gap-1 w-full min-w-0">
      <span className="truncate">{label}</span>
      {(disabledReason || tooltip) && (
        <span
          title={disabledReason ?? tooltip}
          className="text-text-muted hover:text-zinc-300 transition-colors cursor-help shrink-0"
        >
          <Info size={11} />
        </span>
      )}
    </span>
  )
}

function SectionCard({
  title,
  hint,
  children,
  badge,
}: {
  title: string
  hint?: string
  children: ReactNode
  badge?: ReactNode
}) {
  return (
    <div className="rounded-xl border border-white/5 bg-white/[0.02] p-3 space-y-2.5">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-zinc-300">
              {title}
            </span>
            {badge}
          </div>
          {hint && <p className="text-[11px] text-text-muted mt-0.5">{hint}</p>}
        </div>
      </div>
      {children}
    </div>
  )
}

const PROFILE_OPTIONS: SelectOption[] = [
  { value: 'quic', label: 'QUIC', description: 'HTTP/3 UDP masquerade' },
  { value: 'tls', label: 'TLS', description: 'HTTPS / TLS record mimicry' },
  { value: 'dns', label: 'DNS', description: 'Standard port 53 DNS behavior' },
  { value: 'sip', label: 'SIP', description: 'VoIP session protocol mimicry' },
  { value: 'stun', label: 'STUN', description: 'WebRTC NAT discovery mimicry' },
]

export function AwgAdvanced({ version, value, onChange, mode, onModeChange }: AwgAdvancedProps) {
  const { t } = useI18n()
  const [open, setOpen] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [showHpk, setShowHpk] = useState(false)
  const current = useRef<{ version: string; value: AwgParams | undefined; mode: string } | null>({ version, value, mode })

  useEffect(() => {
    current.current = { version, value, mode }
    return () => { current.current = null }
  }, [version, value, mode])

  const params = versionParams(value, version) ?? {}
  const errors = validateAwg(params)
  const hasCustomParams = Boolean(value && Object.keys(value).length > 0)

  function update(key: string, next: unknown) {
    const result = { ...params, [key]: next }
    if (next === undefined || next === '') {
      delete result[key as keyof AwgParams]
    }
    onChange(result)
  }

  async function generate() {
    setBusy(true)
    setError('')
    const snapshot = current.current
    try {
      const result = await generateAwg(version, params.profile ?? 'quic')
      if (current.current === snapshot && snapshot !== null) {
        const { awgVersion: _ignored, ...generated } = result.params as AwgParams & {
          awgVersion?: unknown
        }
        void _ignored
        onModeChange('custom', versionParams(normalizeGeneratedAwg(generated), version))
      }
    } catch (e) {
      if (current.current === snapshot && snapshot !== null) {
        setError(e instanceof Error ? e.message : t('common.error'))
      }
    } finally {
      if (current.current !== null) setBusy(false)
    }
  }

  function renderField(
    key:
      | 'jc'
      | 'jmin'
      | 'jmax'
      | 's1'
      | 's2'
      | 's3'
      | 's4'
      | 'h1'
      | 'h2'
      | 'h3'
      | 'h4'
      | 'i1'
      | 'i2'
      | 'i3'
      | 'i4'
      | 'i5'
      | 'headerProtectionKey'
      | 'contentPaddingAddition',
    options?: { fullWidth?: boolean; placeholder?: string },
  ) {
    const disabled = version !== 'awg3.1' && (v31Fields as readonly string[]).includes(key)
    const numeric =
      !key.startsWith('i') &&
      key !== 'headerProtectionKey' &&
      key !== 'contentPaddingAddition'
    const labelText = t(`awg.fields.${key}`)
    const tooltip = t(`awg.tips.${key}`)
    const disabledReason = disabled ? t('awg.only31') : undefined
    const isHpk = key === 'headerProtectionKey'
    const inputType = isHpk ? (showHpk ? 'text' : 'password') : 'text'

    return (
      <Input
        key={key}
        id={`awg-${key}`}
        aria-label={key}
        aria-invalid={!!errors[key]}
        disabled={disabled}
        title={disabledReason ?? tooltip}
        autoComplete="off"
        inputSize="sm"
        type={inputType}
        inputMode={numeric ? 'numeric' : undefined}
        value={params[key] ?? ''}
        placeholder={options?.placeholder}
        error={errors[key] ? t(`awg.${errors[key]}`) : undefined}
        label={
          <FieldLabel
            label={labelText}
            tooltip={tooltip}
            disabledReason={disabledReason}
          />
        }
        suffix={
          isHpk ? (
            <button
              type="button"
              disabled={disabled}
              onClick={() => setShowHpk((v) => !v)}
              className="p-1 rounded text-text-muted hover:text-zinc-200 hover:bg-white/5 transition-colors cursor-pointer"
              aria-label={showHpk ? t('awg.actions.hide') : t('awg.actions.show')}
              title={showHpk ? t('awg.actions.hide') : t('awg.actions.show')}
            >
              {showHpk ? <EyeOff size={13} /> : <Eye size={13} />}
            </button>
          ) : undefined
        }
        onChange={(e) =>
          update(
            key,
            e.target.value === ''
              ? undefined
              : numeric
                ? (e.target.value.replace(/\D/g, '') === '' ? undefined : Number(e.target.value.replace(/\D/g, '')))
                : e.target.value,
          )
        }
      />
    )
  }

  const is31 = version === 'awg3.1'

  return (
    <div className="mt-3" onPointerDown={(e) => e.stopPropagation()}>
      <div role="radiogroup" aria-label={t('awg.mode')} className="grid grid-cols-2 gap-2 mb-3">
        {(['none', 'auto'] as const).map(option => {
          const locked = !is31
          const stealthLocked = locked && option === 'auto'
          const selected = is31
            ? (option === 'none' ? mode === 'none' : mode !== 'none')
            : option === 'none'
          return (
            <button type="button" role="radio" aria-checked={selected} key={option}
              disabled={locked}
              title={stealthLocked ? t('awg.only31Version') : undefined}
              onClick={() => { if (locked) return; onChange(undefined); onModeChange(option) }}
              className={cn(
                'rounded-xl border p-3 text-left transition-colors',
                selected ? 'border-accent-cyan/50 bg-accent-cyan/10' : 'border-white/10 bg-white/[0.02]',
                locked && 'opacity-50 cursor-not-allowed',
              )}>
              <span className="flex items-center gap-2 text-sm font-semibold">
                {t(`awg.modes.${option}.title`)}
                {stealthLocked && (
                  <Badge variant="warning" className="text-[10px] py-0 px-1.5 leading-tight">
                    {t('awg.only31Version')}
                  </Badge>
                )}
              </span>
              <p className="text-xs text-zinc-400 mt-1">{t(`awg.modes.${option}.description`)}</p>
            </button>
          )
        })}
      </div>
      {is31 && mode !== 'none' && <div className="rounded-2xl border border-white/10 bg-white/[0.02] overflow-hidden transition-all">
        {/* Collapsible Trigger Header */}
        <button
          type="button"
          onClick={() => setOpen((prev) => !prev)}
          className="w-full flex items-center justify-between p-3 sm:p-3.5 text-left hover:bg-white/[0.02] transition-colors cursor-pointer"
          aria-expanded={open}
        >
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-8 h-8 rounded-lg bg-accent-violet/10 border border-accent-violet/25 flex items-center justify-center text-accent-violet shrink-0">
              <SlidersHorizontal size={15} />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs font-semibold text-zinc-100">
                  {t('awg.advanced')}
                </span>
                <Badge
                  variant={hasCustomParams ? 'pro' : 'default'}
                  className="text-[10px] py-0 px-2 leading-tight"
                >
                  {hasCustomParams
                    ? t('awg.badges.custom')
                    : t('awg.badges.auto')}
                </Badge>
              </div>
              <p className="text-[11px] text-zinc-400 truncate mt-0.5">
                {hasCustomParams
                  ? t('awg.status.customHint')
                  : t('awg.status.autoHint')}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0 ml-2">
            <ChevronDown
              size={16}
              className={cn(
                'text-zinc-400 transition-transform duration-200',
                open && 'rotate-180',
              )}
            />
          </div>
        </button>

        {/* Collapsible Content */}
        <AnimatePresence initial={false}>
          {open && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="overflow-hidden"
            >
              <div className="border-t border-white/5 p-3 sm:p-4 space-y-3.5 bg-black/10">
                <Checkbox
                  checked={mode !== 'custom'}
                  disabled={busy}
                  onChange={checked => { if (checked) { onChange(undefined); onModeChange('auto') } else { void generate() } }}
                  label={busy ? t('awg.actions.generating') : t('awg.automaticParameters')}
                  description={t('awg.status.autoHint')}
                  accent="cyan"
                />

                {error && (
                  <div
                    role="alert"
                    className="rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-xs text-red-300"
                  >
                    {error}
                  </div>
                )}

                {mode === 'custom' && <>
                {/* Group 1: Junk packets */}
                <SectionCard
                  title={t('awg.groups.junk')}
                  hint={t('awg.groups.junkHint')}
                >
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
                    {renderField('jc', { placeholder: '0–128' })}
                    {renderField('jmin', { placeholder: 'e.g. 40' })}
                    {renderField('jmax', { placeholder: 'e.g. 70' })}
                  </div>
                </SectionCard>

                {/* Group 2: Padding */}
                <SectionCard
                  title={t('awg.groups.padding')}
                  hint={t('awg.groups.paddingHint')}
                >
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
                    {renderField('s1', { placeholder: 'Init (B)' })}
                    {renderField('s2', { placeholder: 'Response (B)' })}
                    {renderField('s3', { placeholder: 'Cookie (B)' })}
                    {renderField('s4', { placeholder: 'Data (B)' })}
                  </div>
                </SectionCard>

                {/* Group 3: Headers */}
                <SectionCard
                  title={t('awg.groups.headers')}
                  hint={t('awg.groups.headersHint')}
                >
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
                    {renderField('h1', { placeholder: 'Init H1' })}
                    {renderField('h2', { placeholder: 'Response H2' })}
                    {renderField('h3', { placeholder: 'Cookie H3' })}
                    {renderField('h4', { placeholder: 'Data H4' })}
                  </div>
                </SectionCard>

                {/* Group 4: Mimicry (CPS) */}
                <SectionCard
                  title={t('awg.groups.mimicry')}
                  hint={t('awg.groups.mimicryHint')}
                >
                  <div className="space-y-2.5">
                    <div className="max-w-xs">
                      <div className="flex items-center justify-between gap-1 mb-1">
                        <span className="text-xs font-medium text-zinc-300">
                          {t('awg.fields.profile')}
                        </span>
                        <span
                          title={t('awg.tips.profile')}
                          className="text-text-muted hover:text-zinc-300 transition-colors cursor-help shrink-0"
                        >
                          <Info size={11} />
                        </span>
                      </div>
                      <Select
                        value={params.profile ?? 'quic'}
                        onChange={(v) => update('profile', v)}
                        options={PROFILE_OPTIONS}
                        className="px-3 py-1.5 text-xs rounded-lg min-h-[34px]"
                      />
                    </div>
                    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2.5">
                      {renderField('i1', { placeholder: 'I1 hex/str' })}
                      {renderField('i2', { placeholder: 'I2 hex/str' })}
                      {renderField('i3', { placeholder: 'I3 hex/str' })}
                      {renderField('i4', { placeholder: 'I4 hex/str' })}
                      {renderField('i5', { placeholder: 'I5 hex/str' })}
                    </div>
                  </div>
                </SectionCard>

                {/* Group 5: Protection v3.1 */}
                <SectionCard
                  title={t('awg.groups.protection')}
                  hint={t('awg.groups.protectionHint')}
                  badge={
                    version !== 'awg3.1' ? (
                      <Badge variant="warning" className="text-[10px] py-0 px-1.5">
                        AWG 3.1
                      </Badge>
                    ) : undefined
                  }
                >
                  <div className="space-y-3">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                      {renderField('headerProtectionKey', {
                        placeholder: '••••••••••••',
                      })}
                      {renderField('contentPaddingAddition', {
                        placeholder: 'e.g. 0-16',
                      })}
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1 border-t border-white/5">
                      <div
                        title={
                          version !== 'awg3.1' ? t('awg.only31') : undefined
                        }
                      >
                        <Checkbox
                          checked={params.randomTrailers ?? false}
                          onChange={(checked) => update('randomTrailers', checked)}
                          disabled={version !== 'awg3.1'}
                          label={t('awg.fields.randomTrailers')}
                          description={t('awg.fields.randomTrailersDesc')}
                          size="sm"
                          accent="cyan"
                        />
                      </div>
                      <div
                        title={
                          version !== 'awg3.1' ? t('awg.only31') : undefined
                        }
                      >
                        <Checkbox
                          checked={params.disableCookies ?? false}
                          onChange={(checked) => update('disableCookies', checked)}
                          disabled={version !== 'awg3.1'}
                          label={t('awg.fields.disableCookies')}
                          description={t('awg.fields.disableCookiesDesc')}
                          size="sm"
                          accent="cyan"
                        />
                      </div>
                    </div>
                  </div>
                </SectionCard>
                </>}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>}
    </div>
  )
}
