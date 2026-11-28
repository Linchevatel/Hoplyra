import { useState, useEffect } from 'react'
import { ArrowRight, GitBranch, Users, Download, Copy, Check, QrCode } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { ProtocolIcon } from '@/components/ui/ProtocolIcon'
import { QrImage } from '@/components/ui/QrImage'
import type { ChainHop, VpnConfig, VpsServer } from '@/lib/types'
import { PROTOCOLS } from '@/lib/constants'
import { getLocationFlag } from '@/lib/country-utils'
import { useI18n } from '@/i18n/i18n-context'
import { cn, copyToClipboard, formatBytes, formatLastSeen } from '@/lib/utils'
import { buildClientConfigBundle } from '@/lib/client-config'
import * as api from '@/lib/api'

export function ConfigStatusBadge({ status, liveState }: Pick<VpnConfig, 'status' | 'liveState'>) {
  const { t } = useI18n()
  const map = {
    active: { variant: 'success' as const, label: t('status.active') },
    deploying: { variant: 'warning' as const, label: t('common.deploying') },
    error: { variant: 'error' as const, label: t('common.error') },
    inactive: { variant: 'default' as const, label: t('common.off') },
  }
  const item =
    status === 'active' && liveState === 'down'
      ? { variant: 'error' as const, label: t('status.unavailable') }
      : status === 'active' && liveState === 'degraded'
        ? { variant: 'warning' as const, label: t('status.degraded') }
        : map[status]
  return <Badge variant={item.variant}>{item.label}</Badge>
}

function ChainRouteVisual({ hops, servers }: { hops: ChainHop[]; servers: VpsServer[] }) {
  const { t } = useI18n()

  return (
    <div className="flex flex-wrap items-center gap-y-2 gap-x-1">
      {hops.map((hop, i) => {
        const hopServer = servers.find((s) => s.id === hop.serverId)
        const isEntry = i === 0
        const isExit = i === hops.length - 1

        return (
          <span key={hop.id} className="flex items-center gap-1">
            {i > 0 && (
              <ArrowRight size={14} className="text-accent-violet/40 shrink-0 mx-0.5" aria-hidden />
            )}
            <span
              className={cn(
                'inline-flex items-center gap-2 pl-2 pr-3 py-2 rounded-xl border transition-colors',
                isEntry &&
                  'border-accent-cyan/30 bg-accent-cyan/[0.07] shadow-[0_0_12px_color-mix(in_srgb,var(--color-accent-cyan)_8%,transparent)]',
                isExit && !isEntry && 'border-accent-violet/25 bg-accent-violet/[0.06]',
                !isEntry && !isExit && 'border-white/8 bg-white/[0.03]',
              )}
            >
              <ProtocolIcon
                protocol={hop.protocol}
                size="xs"
                withGradient
                title={PROTOCOLS[hop.protocol]?.label ?? hop.protocol}
              />
              {hopServer && (
                <span className="inline-flex items-center gap-1.5 min-w-0">
                  <span className="text-sm leading-none shrink-0">{getLocationFlag(hopServer.location)}</span>
                  <span className="text-xs text-zinc-300 truncate max-w-[7rem] sm:max-w-[9rem]">
                    {hopServer.name}
                  </span>
                </span>
              )}
              {isEntry && (
                <span className="text-[9px] uppercase tracking-wider text-accent-cyan/70 font-semibold shrink-0">
                  {t('common.entry')}
                </span>
              )}
              {isExit && hops.length > 1 && (
                <span className="text-[9px] uppercase tracking-wider text-accent-violet/70 font-semibold shrink-0">
                  {t('common.exit')}
                </span>
              )}
            </span>
          </span>
        )
      })}
    </div>
  )
}

export function ClientConfigCard({
  config,
  server,
  servers,
  onOpen,
  refreshSignal,
}: {
  config: VpnConfig
  server: VpsServer
  servers: VpsServer[]
  onOpen: () => void
  refreshSignal?: number
}) {
  const { t } = useI18n()
  const isChain = Boolean(config.hops && config.hops.length >= 2)
  const supportsPeers =
    !isChain &&
    (config.protocol === 'awg' ||
      config.protocol === 'wg' ||
      config.protocol === 'xray' ||
      config.protocol === 'openvpn' ||
      config.protocol === 'hysteria2' ||
      config.protocol === 'shadowquic')

  const [peers, setPeers] = useState<api.ConfigPeer[]>([])
  const [loadingPeers, setLoadingPeers] = useState(false)
  const [peersError, setPeersError] = useState(false)
  const [lastUpdatedTs, setLastUpdatedTs] = useState<number | null>(null)
  const [copiedId, setCopiedId] = useState<string | null>(null)
  const [inlineQrId, setInlineQrId] = useState<string | null>(null)

  // Генерируем бандл для первичного клиента
  const primaryBundle = buildClientConfigBundle(config, server, servers)

  useEffect(() => {
    if (!supportsPeers || config.status !== 'active') return
    let cancelled = false
    setLoadingPeers(true)
    const load = () => {
      api
        .fetchConfigPeers(config.id)
        .then((data) => {
          if (!cancelled) {
            setPeers(data)
            setPeersError(false)
            setLastUpdatedTs(Date.now())
          }
        })
        .catch(() => {
          if (!cancelled) {
            setPeersError(true)
          }
        })
        .finally(() => {
          if (!cancelled) setLoadingPeers(false)
        })
    }
    load()

    // Периодический опрос живой статистики для открытой страницы
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') load()
    }, 15000)

    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [config.id, supportsPeers, config.status, refreshSignal])

  function handleCopy(text: string, id: string) {
    copyToClipboard(text)
      .then((ok) => {
        // Замечание [4]: Проверяем boolean и показываем галочку только при true
        if (ok) {
          setCopiedId(id)
          setTimeout(() => setCopiedId(null), 1500)
        }
      })
      .catch(() => {})
  }

  function handleDownload(content: string, name: string) {
    const ext =
      config.protocol === 'openvpn' ? 'ovpn' : config.protocol === 'xray' ? 'json' : 'conf'
    const blob = new Blob([content], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const safeName = name.replace(/[^a-zA-Z0-9._-]+/g, '_') || 'client'
    a.download = `${safeName}.${ext}`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="rounded-2xl border border-white/10 surface-panel overflow-hidden transition-all shadow-lg hover:border-white/15">
      {/* Шапка карточки */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 p-5 sm:p-6 bg-white/[0.02] border-b border-white/5">
        <div className="flex items-center gap-4 min-w-0">
          <ProtocolIcon protocol={config.protocol} size="md" withGradient />
          <div className="min-w-0">
            <p className="text-base font-semibold truncate flex items-center gap-2 text-zinc-100">
              <span className="text-lg leading-none">{getLocationFlag(server.location)}</span>
              {server.name}
            </p>
            <div className="flex flex-wrap items-center gap-2 mt-1">
              <span className="text-xs text-text-muted">
                {PROTOCOLS[config.protocol]?.label ?? config.protocol}
              </span>
              <ConfigStatusBadge status={config.status} liveState={config.liveState} />
              {isChain && (
                <Badge variant="default" className="text-accent-violet/90">
                  <GitBranch size={10} className="mr-1" />
                  {t('common.chain')}
                </Badge>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="min-w-0 text-right hidden md:block">
            {isChain && config.hops ? (
              <ChainRouteVisual hops={config.hops} servers={servers} />
            ) : (
              <p className="text-xs text-text-muted font-mono">{server.host}</p>
            )}
          </div>

          <Button variant="secondary" size="sm" onClick={onOpen} className="shrink-0 gap-1.5">
            {supportsPeers ? (
              <>
                <Users size={14} />
                {t('common.clients')}
              </>
            ) : (
              <>
                <Download size={14} />
                {t('common.config')}
              </>
            )}
          </Button>
        </div>
      </div>

      {/* Подробный список клиентов прямо в карточке */}
      {supportsPeers && config.status === 'active' && (
        <div className="p-5 sm:p-6 space-y-3">
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-text-muted flex items-center gap-2">
              <Users size={14} className="text-accent-cyan" />
              {t('dashboard.configs.peerListTitle')} ({peers.length + (config.clientConfig ? 1 : 0)})
            </h4>
            {peersError && (
              <span className="text-[11px] text-amber-400 font-medium">
                {t('dashboard.configs.peerStatsStale')}
              </span>
            )}
            {!peersError && lastUpdatedTs && (
              <span className="text-[10px] text-text-muted">
                {t('dashboard.configs.peerStatsUpdated')} {new Date(lastUpdatedTs).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
              </span>
            )}
          </div>

          <div className="grid grid-cols-1 gap-2.5">
            {/* Основной клиент (Primary) */}
            {config.clientConfig && (
              <div className="flex flex-col gap-3 p-3.5 rounded-xl border border-accent-cyan/25 bg-accent-cyan/[0.04] transition-colors">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="w-8 h-8 rounded-lg bg-accent-cyan/15 flex items-center justify-center shrink-0 text-accent-cyan font-bold text-xs">
                      P
                    </div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-zinc-100 text-sm truncate">
                          {t('dashboard.configs.primaryClient')}
                        </span>
                        <span className="px-1.5 py-0.5 rounded bg-accent-cyan/20 text-accent-cyan text-[10px] font-medium">
                          Primary
                        </span>
                      </div>
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 mt-0.5 text-xs text-text-muted font-mono">
                        <span>{config.clientConfig.match(/Address\s*=\s*([^\s\n]+)/)?.[1] ?? 'Primary'}</span>
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-1.5 shrink-0 self-end sm:self-auto">
                    {/* Кнопка vpn:// для Primary */}
                    {primaryBundle.shareLink && (
                      <Button
                        size="sm"
                        variant="secondary"
                        title={`${primaryBundle.shareLink.scheme}://`}
                        onClick={() => handleCopy(primaryBundle.shareLink!.uri, 'share-primary')}
                      >
                        {copiedId === 'share-primary' ? <Check size={13} /> : `${primaryBundle.shareLink.scheme}://`}
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="secondary"
                      title={t('dashboard.configs.copyConfig')}
                      onClick={() => handleCopy(config.clientConfig!, 'primary')}
                    >
                      {copiedId === 'primary' ? <Check size={13} /> : <Copy size={13} />}
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      title={t('dashboard.configs.downloadConf')}
                      onClick={() => handleDownload(config.clientConfig!, `${server.name}-primary`)}
                    >
                      <Download size={13} />
                    </Button>
                    {/* Кнопка открывает QR прямо здесь на странице */}
                    <Button
                      size="sm"
                      variant={inlineQrId === 'primary' ? 'secondary' : 'ghost'}
                      onClick={() => setInlineQrId(inlineQrId === 'primary' ? null : 'primary')}
                      title="QR"
                    >
                      <QrCode size={14} />
                    </Button>
                  </div>
                </div>

                {/* Раскрывающийся QR-код для Primary прямо в карточке */}
                {inlineQrId === 'primary' && primaryBundle.qrPayload && (
                  <div className="pt-3 pb-1 border-t border-accent-cyan/20 flex flex-col items-center gap-2">
                    <div className="p-2.5 bg-white rounded-xl shadow-md">
                      <QrImage data={primaryBundle.qrPayload} size={180} />
                    </div>
                    <span className="text-[11px] text-text-muted">
                      {primaryBundle.shareLink
                        ? t('dashboard.configs.qrScheme', { scheme: primaryBundle.shareLink.scheme })
                        : t('dashboard.configs.qrFile')}
                    </span>
                  </div>
                )}
              </div>
            )}

            {/* Дополнительные клиенты (Peers) */}
            {peers.map((peer) => {
              const isOnline =
                peer.isConnected ??
                (peer.lastHandshake && Math.floor(Date.now() / 1000) - peer.lastHandshake < 150)
              const hasStats = peer.statsSupported !== false && (peer.rxBytes != null || peer.txBytes != null || peer.lastHandshake != null || peer.isConnected != null)
              const lastSeenText = peer.lastHandshake
                ? formatLastSeen(peer.lastHandshake)
                : peer.statsSupported
                  ? t('dashboard.configs.peerNeverConnected')
                  : t('dashboard.configs.peerNoStats')

              const peerBundle = buildClientConfigBundle(
                { ...config, clientConfig: peer.clientConfig, amneziaVpnUri: peer.amneziaVpnUri ?? undefined, vlessUri: peer.vlessUri ?? undefined },
                server,
                servers,
              )
              const peerShareUri = peerBundle.shareLink?.uri
              const peerScheme = peerBundle.shareLink?.scheme ?? 'vpn'
              const peerQrPayload = peerBundle.qrPayload

              return (
                <div
                  key={peer.id}
                  className="flex flex-col gap-3 p-3.5 rounded-xl border border-white/5 bg-white/[0.02] hover:bg-white/[0.04] transition-colors"
                >
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                    <div className="flex items-center gap-3 min-w-0">
                      <div
                        className={cn(
                          'w-8 h-8 rounded-lg flex items-center justify-center shrink-0 text-xs font-semibold',
                          isOnline
                            ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                            : 'bg-zinc-800 text-zinc-400 border border-white/5',
                        )}
                      >
                        {peer.name.slice(0, 2).toUpperCase()}
                      </div>
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-zinc-100 text-sm truncate">{peer.name}</span>
                          <span
                            className={cn(
                              'inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-medium',
                              isOnline
                                ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/25'
                                : 'bg-zinc-800/80 text-zinc-400 border border-white/5',
                            )}
                          >
                            <span
                              className={cn(
                                'h-1.5 w-1.5 rounded-full',
                                isOnline ? 'bg-emerald-400 animate-pulse' : 'bg-zinc-500',
                              )}
                            />
                            {isOnline ? t('dashboard.configs.peerOnline') : lastSeenText}
                          </span>
                        </div>
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 mt-0.5 text-xs text-text-muted font-mono">
                          {peer.clientIp && !peer.clientIp.startsWith('peer_') && !peer.clientIp.startsWith('UUID ') && (
                            <span>{peer.clientIp}</span>
                          )}
                          {hasStats && (peer.rxBytes != null || peer.txBytes != null) ? (
                            <span className="text-zinc-300 font-medium">
                              ↓ {formatBytes(peer.rxBytes ?? 0)} · ↑ {formatBytes(peer.txBytes ?? 0)}
                            </span>
                          ) : (
                            <span className="text-zinc-500">{t('dashboard.configs.peerNoStats')}</span>
                          )}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-1.5 shrink-0 self-end sm:self-auto">
                      {/* Кнопка ссылки vpn:// или vless:// для пира */}
                      {peerShareUri && (
                        <Button
                          size="sm"
                          variant="secondary"
                          title={`${peerScheme}://`}
                          onClick={() => handleCopy(peerShareUri, `uri-${peer.id}`)}
                        >
                          {copiedId === `uri-${peer.id}` ? <Check size={13} /> : `${peerScheme}://`}
                        </Button>
                      )}
                      <Button
                        size="sm"
                        variant="secondary"
                        title={t('dashboard.configs.copyConfig')}
                        onClick={() => handleCopy(peer.clientConfig, peer.id)}
                      >
                        {copiedId === peer.id ? <Check size={13} /> : <Copy size={13} />}
                      </Button>
                      <Button
                        size="sm"
                        variant="secondary"
                        title={t('dashboard.configs.downloadConfig')}
                        onClick={() => handleDownload(peer.clientConfig, peer.name)}
                      >
                        <Download size={13} />
                      </Button>
                      {/* Кнопка открывает QR прямо здесь на странице */}
                      <Button
                        size="sm"
                        variant={inlineQrId === peer.id ? 'secondary' : 'ghost'}
                        onClick={() => setInlineQrId(inlineQrId === peer.id ? null : peer.id)}
                        title="QR"
                      >
                        <QrCode size={14} />
                      </Button>
                    </div>
                  </div>

                  {/* Раскрывающийся QR-код для пира прямо в карточке */}
                  {inlineQrId === peer.id && peerQrPayload && (
                    <div className="pt-3 pb-1 border-t border-white/5 flex flex-col items-center gap-2">
                      <div className="p-2.5 bg-white rounded-xl shadow-md">
                        <QrImage data={peerQrPayload} size={180} />
                      </div>
                      <span className="text-[11px] text-text-muted">
                        {peerShareUri
                          ? t('dashboard.configs.qrScheme', { scheme: peerScheme })
                          : t('dashboard.configs.qrFile')}
                      </span>
                    </div>
                  )}
                </div>
              )
            })}

            {peers.length === 0 && !loadingPeers && (
              <p className="text-xs text-text-muted italic py-1">
                {t('dashboard.configs.noPeersYet')}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
