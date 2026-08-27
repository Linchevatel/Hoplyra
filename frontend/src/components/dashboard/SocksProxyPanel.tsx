import { useState } from 'react'
import {
  Copy,
  Check,
  Loader2,
  Power,
  PowerOff,
  ChevronDown,
  Server,
  ArrowRight,
  Eye,
  EyeOff,
} from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { ChainPreview } from '@/components/dashboard/ChainHopEditor'
import { ProtocolIcon } from '@/components/ui/ProtocolIcon'
import { useDashboard } from '@/lib/dashboard'
import { useI18n } from '@/i18n/I18nProvider'
import { PROTOCOLS } from '@/lib/constants'
import { formatChainProtocols, getChainEndpointHosts } from '@/lib/chain-utils'
import type { VpnConfig } from '@/lib/types'
import { cn } from '@/lib/utils'

type Props = {
  config: VpnConfig
  defaultExpanded?: boolean
}

function CopyField({
  label,
  value,
}: {
  label: string
  value: string
}) {
  const { t } = useI18n()
  const [copied, setCopied] = useState(false)

  async function handleCopy() {
    await navigator.clipboard.writeText(value)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="min-w-0">
      <p className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">{label}</p>
      <div className="flex items-center gap-1.5 rounded-lg bg-black/25 border border-white/5 px-2.5 py-2">
        <span className="flex-1 min-w-0 text-xs font-mono text-zinc-200 truncate" title={value}>
          {value}
        </span>
        <button
          type="button"
          onClick={() => void handleCopy()}
          className="p-1 rounded-md text-zinc-500 hover:text-cyan-400 hover:bg-white/5 transition-colors cursor-pointer shrink-0"
          aria-label={t('common.copy')}
        >
          {copied ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
        </button>
      </div>
    </div>
  )
}

function PasswordCopyField({ label, value }: { label: string; value: string }) {
  const { t } = useI18n()
  const [copied, setCopied] = useState(false)
  const [visible, setVisible] = useState(true)

  async function handleCopy() {
    await navigator.clipboard.writeText(value)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="min-w-0">
      <p className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">{label}</p>
      <div className="flex items-center gap-1.5 rounded-lg bg-black/25 border border-white/5 px-2.5 py-2">
        <span
          className="flex-1 min-w-0 text-xs font-mono text-zinc-200 truncate"
          title={visible ? value : undefined}
        >
          {visible ? value : '••••••••••••'}
        </span>
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          className="p-1 rounded-md text-zinc-500 hover:text-zinc-300 hover:bg-white/5 transition-colors cursor-pointer shrink-0"
          aria-label={visible ? t('dashboard.proxyTab.hidePassword') : t('dashboard.proxyTab.showPassword')}
        >
          {visible ? <EyeOff size={13} /> : <Eye size={13} />}
        </button>
        <button
          type="button"
          onClick={() => void handleCopy()}
          className="p-1 rounded-md text-zinc-500 hover:text-cyan-400 hover:bg-white/5 transition-colors cursor-pointer shrink-0"
          aria-label={t('common.copy')}
        >
          {copied ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
        </button>
      </div>
    </div>
  )
}

function routeTitle(config: VpnConfig, serverName: string | undefined, t: (k: string) => string) {
  if (config.hops && config.hops.length >= 2) {
    return formatChainProtocols(config.hops)
  }
  return serverName ?? t('dashboard.proxyTab.unknownServer')
}

export function SocksProxyPanel({ config, defaultExpanded = true }: Props) {
  const { t } = useI18n()
  const { servers, enableSocksProxy, disableSocksProxy, socksProxyLoadingIds } = useDashboard()
  const [expanded, setExpanded] = useState(defaultExpanded)
  const [uriCopied, setUriCopied] = useState(false)
  const loading = socksProxyLoadingIds.has(config.id)
  const proxy = config.socksProxy
  const chain = Boolean(config.hops && config.hops.length >= 2)
  const server = servers.find((s) => s.id === config.serverId)
  const enabled = Boolean(proxy?.enabled)
  const title = routeTitle(config, server?.name, t)

  if (config.status !== 'active') return null

  async function handleCopyUri(value: string | undefined) {
    if (!value) return
    await navigator.clipboard.writeText(value)
    setUriCopied(true)
    window.setTimeout(() => setUriCopied(false), 2000)
  }

  const endpoints =
    chain && config.hops ? getChainEndpointHosts(config.hops, servers) : null

  return (
    <article
      className={cn(
        'rounded-2xl border transition-all duration-300 overflow-hidden',
        enabled
          ? 'border-cyan-500/25 bg-gradient-to-br from-cyan-500/[0.07] via-transparent to-violet-500/[0.04] shadow-[0_0_40px_rgba(34,211,238,0.06)]'
          : 'border-white/8 bg-white/[0.02] hover:border-white/12',
      )}
    >
      <div className="p-4 sm:p-5">
        <div className="flex items-start gap-3">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="mt-0.5 p-1 rounded-md text-zinc-500 hover:text-zinc-300 hover:bg-white/5 transition-colors cursor-pointer shrink-0"
            aria-expanded={expanded}
            aria-label={expanded ? t('dashboard.proxyTab.collapse') : t('dashboard.proxyTab.expand')}
          >
            <ChevronDown
              size={18}
              className={cn('transition-transform duration-200', expanded && 'rotate-180')}
            />
          </button>

          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="flex-1 min-w-0 text-left cursor-pointer"
          >
            <div className="flex flex-wrap items-center gap-2">
              {chain ? (
                <Badge variant="protocol">{t('dashboard.proxyTab.chain')}</Badge>
              ) : (
                <ProtocolIcon protocol={config.protocol} size="sm" withGradient />
              )}
              <Badge variant={enabled ? 'success' : 'default'}>
                {enabled ? t('dashboard.proxyTab.socksOn') : t('dashboard.proxyTab.socksOff')}
              </Badge>
            </div>
            <p className="text-sm font-semibold text-zinc-100 truncate mt-2">{title}</p>
            {!expanded && (
              <p className="text-xs text-zinc-500 mt-1 font-mono truncate">
                {enabled && proxy
                  ? `${proxy.host}:${proxy.port}`
                  : chain && endpoints
                    ? `${endpoints.entry} → ${endpoints.exit}`
                    : server?.host ?? '—'}
              </p>
            )}
          </button>

          <div className="shrink-0" onClick={(e) => e.stopPropagation()}>
            {enabled ? (
              <Button
                variant="secondary"
                size="sm"
                disabled={loading}
                onClick={() => disableSocksProxy(config.id)}
              >
                {loading ? <Loader2 size={14} className="animate-spin" /> : <PowerOff size={14} />}
                <span className="hidden sm:inline">{t('dashboard.socks.disable')}</span>
              </Button>
            ) : (
              <Button size="sm" disabled={loading} onClick={() => enableSocksProxy(config.id)}>
                {loading ? <Loader2 size={14} className="animate-spin" /> : <Power size={14} />}
                <span className="hidden sm:inline">{t('dashboard.socks.enable')}</span>
              </Button>
            )}
          </div>
        </div>

        {expanded && (
          <div className="mt-4 ml-8 space-y-4">
            {chain && config.hops ? (
              <div className="space-y-2">
                <ChainPreview hops={config.hops} servers={servers} />
                {endpoints && (
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-zinc-500 font-mono">
                    <span className="inline-flex items-center gap-1">
                      <Server size={11} className="text-zinc-600" />
                      {endpoints.entry}
                    </span>
                    <ArrowRight size={11} className="text-zinc-600" />
                    <span>{endpoints.exit}</span>
                  </div>
                )}
              </div>
            ) : (
              <p className="text-xs text-zinc-500">
                {PROTOCOLS[config.protocol].label}
                {server?.host ? (
                  <>
                    {' '}
                    · <span className="font-mono text-zinc-400">{server.host}</span>
                  </>
                ) : null}
              </p>
            )}

            {enabled && proxy ? (
              <div className="pt-4 border-t border-white/8 space-y-4">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <CopyField label={t('dashboard.proxyTab.host')} value={proxy.host} />
                  <CopyField label={t('dashboard.proxyTab.port')} value={String(proxy.port)} />
                  <CopyField label={t('dashboard.proxyTab.username')} value={proxy.username} />
                  {proxy.password ? (
                    <PasswordCopyField
                      label={t('dashboard.proxyTab.password')}
                      value={proxy.password}
                    />
                  ) : (
                    <div className="min-w-0">
                      <p className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
                        {t('dashboard.proxyTab.password')}
                      </p>
                      <p className="text-xs text-zinc-500 rounded-lg bg-black/25 border border-white/5 px-2.5 py-2">
                        {t('dashboard.proxyTab.passwordHidden')}
                      </p>
                    </div>
                  )}
                </div>

                {proxy.uri && (
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
                      {t('dashboard.proxyTab.connectionUri')}
                    </p>
                    <div className="flex items-start gap-2 rounded-xl bg-black/30 border border-cyan-500/15 px-3 py-2.5">
                      <code className="flex-1 min-w-0 text-[11px] font-mono text-cyan-200/90 break-all leading-relaxed">
                        {proxy.uri}
                      </code>
                      <button
                        type="button"
                        onClick={() => void handleCopyUri(proxy.uri)}
                        className="p-1.5 rounded-md text-zinc-500 hover:text-cyan-400 hover:bg-white/5 transition-colors cursor-pointer shrink-0"
                        aria-label={t('common.copy')}
                      >
                        {uriCopied ? (
                          <Check size={14} className="text-emerald-400" />
                        ) : (
                          <Copy size={14} />
                        )}
                      </button>
                    </div>
                  </div>
                )}

                <p className="text-[10px] text-zinc-600 leading-relaxed">
                  {t('dashboard.socks.firewallHint')}
                </p>
              </div>
            ) : (
              <p className="text-xs text-zinc-500 leading-relaxed pt-2 border-t border-white/5">
                {t('dashboard.socks.subtitle')}
              </p>
            )}
          </div>
        )}
      </div>
    </article>
  )
}
