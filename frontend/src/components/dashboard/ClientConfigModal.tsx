import { useEffect, useState } from 'react'
import { X, Download, Copy, Check, GitBranch, Wrench } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import type { VpnConfig, VpsServer } from '@/lib/types'
import { buildClientConfigBundle, extractVlessUri, qrCodeUrl } from '@/lib/client-config'
import { copyToClipboard } from '@/lib/utils'
import { PROTOCOLS } from '@/lib/constants'
import { configUsesAwg } from '@/lib/deploy-history'
import { useI18n } from '@/i18n/i18n-context'
import * as api from '@/lib/api'

interface ClientConfigModalProps {
  config: VpnConfig
  server: VpsServer
  servers?: VpsServer[]
  onClose: () => void
  onConfigUpdated?: (config: VpnConfig) => void
}

export function ClientConfigModal(props: ClientConfigModalProps) {
  // Each selected config owns its request, loading state and repair state.
  return <ClientConfigModalContent key={props.config.id} {...props} />
}

function ClientConfigModalContent({
  config,
  server,
  servers,
  onClose,
  onConfigUpdated,
}: ClientConfigModalProps) {
  const { t } = useI18n()
  const [sourceConfig, setSourceConfig] = useState(config)
  const [liveConfig, setLiveConfig] = useState(config)
  if (sourceConfig !== config) {
    setSourceConfig(config)
    setLiveConfig(config)
  }
  const [loadError, setLoadError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [retry, setRetry] = useState(0)
  const [loaded, setLoaded] = useState(config.id.startsWith('demo-'))
  const [peers, setPeers] = useState<api.ConfigPeer[]>([])
  const [peerName, setPeerName] = useState('')
  const [peerBusy, setPeerBusy] = useState(false)
  const [peerError, setPeerError] = useState<string | null>(null)
  const [copiedPeerId, setCopiedPeerId] = useState<string | null>(null)
  const [copiedPeerLinkId, setCopiedPeerLinkId] = useState<string | null>(null)
  const [qrPeerId, setQrPeerId] = useState<string | null>(null)
  const supportsPeers =
    (liveConfig.protocol === 'awg' || liveConfig.protocol === 'wg' || liveConfig.protocol === 'xray' || liveConfig.protocol === 'openvpn') &&
    !liveConfig.hops?.length &&
    liveConfig.status === 'active' &&
    !liveConfig.id.startsWith('demo-')

  useEffect(() => {
    if (!supportsPeers) return
    let active = true
    api.fetchConfigPeers(liveConfig.id)
      .then((list) => { if (active) setPeers(list) })
      .catch(() => {})
    return () => { active = false }
  }, [liveConfig.id, supportsPeers])

  async function handleAddPeer() {
    const trimmed = peerName.trim()
    if (!trimmed || peerBusy) return
    setPeerBusy(true)
    setPeerError(null)
    try {
      const created = await api.createConfigPeer(liveConfig.id, trimmed)
      setPeers((prev) => [...prev, created])
      setPeerName('')
    } catch (err) {
      setPeerError(err instanceof Error ? err.message : t('common.error'))
    } finally {
      setPeerBusy(false)
    }
  }

  async function handleRevokePeer(peerId: string) {
    if (peerBusy) return
    setPeerBusy(true)
    setPeerError(null)
    try {
      await api.deleteConfigPeer(liveConfig.id, peerId)
      setPeers((prev) => prev.filter((p) => p.id !== peerId))
    } catch (err) {
      setPeerError(err instanceof Error ? err.message : t('common.error'))
    } finally {
      setPeerBusy(false)
    }
  }

  const [repairing, setRepairing] = useState(false)
  const [loadingConfig, setLoadingConfig] = useState(() =>
    !config.id.startsWith('demo-') && !config.id.startsWith('pending'),
  )
  const bundle = buildClientConfigBundle(liveConfig, server, servers)

  useEffect(() => {
    if (
      liveConfig.id.startsWith('demo-') ||
      liveConfig.id.startsWith('pending')
    ) {
      return
    }
    let cancelled = false
    api
      .fetchClientConfig(liveConfig.id)
      .then(({ config: clientConfig }) => {
        if (cancelled) return
        if (!clientConfig.trim()) throw new Error(t('common.error'))
        setLoaded(true)
        const vlessUri = extractVlessUri(clientConfig)
        setLiveConfig((prev) => ({
          ...prev,
          clientConfig,
          ...(vlessUri ? { vlessUri } : {}),
        }))
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : t('common.error'))
      })
      .finally(() => {
        if (!cancelled) setLoadingConfig(false)
      })
    return () => {
      cancelled = true
    }
  }, [liveConfig.id, retry, t])
  const exportReady = loaded && !loadingConfig && !loadError && !!liveConfig.clientConfig?.trim()
  const needsAmneziaRepair =
    !liveConfig.hops?.length &&
    configUsesAwg(liveConfig) &&
    !/\bH1\s*=/.test(bundle.fileConfig)
  const [copiedFile, setCopiedFile] = useState(false)
  const [copiedLink, setCopiedLink] = useState(false)

  async function handleCopyFile() {
    const ok = await copyToClipboard(bundle.fileConfig)
    if (!ok) { setActionError(t('ui.copyFailed')); return }
    setActionError(null)
    setCopiedFile(true)
    setTimeout(() => setCopiedFile(false), 2000)
  }

  async function handleCopyLink() {
    if (!bundle.shareLink) return
    const ok = await copyToClipboard(bundle.shareLink.uri)
    if (!ok) { setActionError(t('ui.copyFailed')); return }
    setActionError(null)
    setCopiedLink(true)
    setTimeout(() => setCopiedLink(false), 2000)
  }

  function handleDownload() {
    const blob = new Blob([bundle.fileConfig], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = bundle.filename
    a.click()
    URL.revokeObjectURL(url)
  }

  async function handleRepairAmnezia() {
    setActionError(null)
    setRepairing(true)
    try {
      const updated = await api.repairAmneziaAwgConfigApi(liveConfig.id)
      setLiveConfig(updated)
      onConfigUpdated?.(updated)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : t('common.error'))
    } finally {
      setRepairing(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
      <div className="w-full max-w-lg glass rounded-2xl p-5 glow-violet max-h-[90vh] overflow-y-auto">
        {loadError && <div role="alert" className="text-rose-400">{loadError}<Button onClick={() => { setLoadingConfig(true); setLoaded(false); setLoadError(null); setRetry((n) => n + 1) }}>{t('ui.retry')}</Button></div>}
        {actionError && <p role="alert" className="text-rose-400">{actionError}</p>}
        <div className="flex items-start justify-between gap-3 mb-4">
          <div className="min-w-0">
            <h3 className="font-semibold">{supportsPeers ? t('common.clients') : t('dashboard.configs.clientConfig')}</h3>
            <p className="text-xs text-text-muted mt-0.5">
              {PROTOCOLS[liveConfig.protocol]?.label ?? liveConfig.protocol} · {server.name}
              {loadingConfig ? ' · …' : ''}
            </p>
            {needsAmneziaRepair && (
              <div className="mt-2 rounded-lg border border-amber-500/25 bg-amber-500/10 p-2.5">
                <p className="text-[11px] text-amber-200/90">{t('dashboard.configs.repairAmneziaAwgHint')}</p>
                <Button
                  size="sm"
                  variant="secondary"
                  className="mt-2 w-full"
                  disabled={repairing}
                  onClick={handleRepairAmnezia}
                >
                  <Wrench size={14} />
                  {repairing ? t('common.installing') : t('dashboard.configs.repairAmneziaAwg')}
                </Button>
              </div>
            )}
            {bundle.isChainEntry && (
              <p className="text-xs text-accent-violet/90 mt-1.5 flex items-start gap-1.5">
                <GitBranch size={12} className="shrink-0 mt-0.5" />
                <span>
                  {t('dashboard.configs.chainEntry')}
                  {bundle.chainRoute && (
                    <span className="block text-text-muted mt-0.5 font-mono text-[10px]">{bundle.chainRoute}</span>
                  )}
                </span>
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 text-text-muted hover:text-white cursor-pointer shrink-0"
            aria-label={t('common.close')}
          >
            <X size={18} />
          </button>
        </div>

        {!supportsPeers && bundle.supportsQr && (
          <div className="flex flex-col items-center mb-4">
            <img
              src={qrCodeUrl(bundle.qrPayload, 200)}
              alt={t('dashboard.configs.qrConfig')}
              className="rounded-xl border border-white/10 bg-white p-2"
              width={200}
              height={200}
            />
            <p className="text-[10px] text-text-muted mt-2 text-center">
              {bundle.shareLink
                ? t('dashboard.configs.qrScheme', { scheme: bundle.shareLink.scheme })
                : t('dashboard.configs.qrFile')}
            </p>
          </div>
        )}

        {supportsPeers ? (
          <div className="space-y-3">
            <div className="flex gap-2">
              <input
                type="text"
                value={peerName}
                onChange={(e) => setPeerName(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void handleAddPeer() } }}
                placeholder="Имя устройства (напр. iPhone)"
                className="flex-1 rounded-xl border border-white/10 bg-surface-900/80 px-3 py-2 text-xs text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:border-accent-cyan/50"
              />
              <Button size="sm" disabled={peerBusy || !peerName.trim()} onClick={handleAddPeer}>
                Добавить
              </Button>
            </div>
            {peerError && <p className="text-xs text-rose-400">{peerError}</p>}

            <div className="space-y-2 max-h-80 overflow-y-auto pr-0.5">
              {/* Primary client */}
              <div className="rounded-xl border border-accent-cyan/25 bg-accent-cyan/[0.04] p-3 text-xs space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="font-semibold text-zinc-100 truncate">Основной клиент</span>
                    <span className="px-1.5 py-0.5 rounded bg-accent-cyan/15 text-accent-cyan text-[10px] font-medium">Primary</span>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {bundle.shareLink && (
                      <Button size="sm" variant="secondary" disabled={!exportReady} onClick={handleCopyLink} title={bundle.shareLink.scheme + '://'}>
                        {copiedLink ? <Check size={13} /> : `${bundle.shareLink.scheme}://`}
                      </Button>
                    )}
                    <Button size="sm" variant="secondary" disabled={!exportReady} onClick={handleCopyFile} title="Скопировать конфиг">
                      {copiedFile ? <Check size={13} /> : <Copy size={13} />}
                    </Button>
                    <Button size="sm" variant="secondary" disabled={!exportReady} onClick={handleDownload} title="Скачать .conf">
                      <Download size={13} />
                    </Button>
                    {bundle.qrPayload && (
                      <Button size="sm" variant="ghost" onClick={() => setQrPeerId(qrPeerId === 'primary' ? null : 'primary')}>
                        QR
                      </Button>
                    )}
                  </div>
                </div>
                {qrPeerId === 'primary' && bundle.qrPayload && (
                  <div className="pt-2 flex justify-center">
                    <img src={qrCodeUrl(bundle.qrPayload, 176)} alt="QR" width={176} height={176} className="rounded-xl bg-white p-2" />
                  </div>
                )}
              </div>

              {/* Additional peers */}
              {peers.map((p) => {
                const peerBundle = buildClientConfigBundle(
                  { ...liveConfig, clientConfig: p.clientConfig, amneziaVpnUri: p.amneziaVpnUri ?? undefined, vlessUri: p.vlessUri ?? undefined },
                  server,
                  servers,
                )
                const peerShareUri = peerBundle.shareLink?.uri
                const peerScheme = peerBundle.shareLink?.scheme ?? 'vpn'
                const peerQr = peerBundle.qrPayload
                const peerAddr = p.clientIp.includes('.') ? `${p.clientIp}/32` : p.clientIp
                return (
                  <div key={p.id} className="rounded-xl border border-white/10 bg-white/[0.02] p-3 text-xs space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <p className="font-medium text-zinc-200 truncate">{p.name}</p>
                        <p className="text-[11px] text-text-muted font-mono">{peerAddr}</p>
                      </div>
                      <div className="flex items-center gap-1.5 shrink-0">
                        {peerShareUri && (
                          <Button
                            size="sm"
                            variant="secondary"
                            title={`${peerScheme}://`}
                            onClick={() => {
                              copyToClipboard(peerShareUri).catch(() => {})
                              setCopiedPeerLinkId(p.id)
                              setTimeout(() => setCopiedPeerLinkId(null), 1500)
                            }}
                          >
                            {copiedPeerLinkId === p.id ? <Check size={13} /> : `${peerScheme}://`}
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="secondary"
                          title={t('dashboard.configs.copyFile')}
                          onClick={() => {
                            copyToClipboard(p.clientConfig).catch(() => {})
                            setCopiedPeerId(p.id)
                            setTimeout(() => setCopiedPeerId(null), 1500)
                          }}
                        >
                          {copiedPeerId === p.id ? <Check size={13} /> : <Copy size={13} />}
                        </Button>
                        <Button
                          size="sm"
                          variant="secondary"
                          title={t('dashboard.configs.downloadConfig')}
                          onClick={() => {
                            const ext = liveConfig.protocol === 'openvpn' ? 'ovpn' : liveConfig.protocol === 'xray' ? 'json' : 'conf'
                            const blob = new Blob([p.clientConfig], { type: 'text/plain' })
                            const url = URL.createObjectURL(blob)
                            const a = document.createElement('a')
                            a.href = url
                            const safeName = p.name.replace(/[^a-zA-Z0-9._-]+/g, '_') || 'peer'
                            a.download = `${safeName}.${ext}`
                            a.click()
                            URL.revokeObjectURL(url)
                          }}
                        >
                          <Download size={13} />
                        </Button>
                        {peerQr && (
                          <Button size="sm" variant="ghost" onClick={() => setQrPeerId(qrPeerId === p.id ? null : p.id)}>
                            QR
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={peerBusy}
                          onClick={() => handleRevokePeer(p.id)}
                          className="text-rose-400 hover:text-rose-300"
                        >
                          Отозвать
                        </Button>
                      </div>
                    </div>
                    {qrPeerId === p.id && peerQr && (
                      <div className="pt-2 flex justify-center">
                        <img src={qrCodeUrl(peerQr, 176)} alt="QR" width={176} height={176} className="rounded-xl bg-white p-2" />
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        ) : (
        <>
        {bundle.shareLink && (
          <div className="mb-4 rounded-xl border border-accent-cyan/15 bg-accent-cyan/[0.04] p-3">
            <pre className="text-[11px] font-mono text-accent-cyan/90 overflow-x-auto whitespace-pre-wrap break-all max-h-24">
              {bundle.shareLink.uri}
            </pre>
            <Button variant="secondary" size="sm" className="mt-2 w-full" disabled={!exportReady} onClick={handleCopyLink}>
              {copiedLink ? <Check size={14} /> : <Copy size={14} />}
              {copiedLink ? t('common.copied') : t('dashboard.configs.copyScheme', { scheme: bundle.shareLink.scheme })}
            </Button>
          </div>
        )}

        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-text-muted mb-2">
            {t('dashboard.configs.configFile')}
          </p>
          <pre className="text-[11px] font-mono text-zinc-400 bg-black/30 rounded-xl p-3 overflow-x-auto max-h-40 whitespace-pre-wrap break-all">
            {bundle.fileConfig}
          </pre>
        </div>

        <div className="flex flex-col sm:flex-row gap-2 mt-4">
          <Button variant="secondary" size="sm" className="flex-1" disabled={!exportReady} onClick={handleCopyFile}>
            {copiedFile ? <Check size={14} /> : <Copy size={14} />}
            {copiedFile ? t('common.copied') : t('dashboard.configs.copyFile')}
          </Button>
          <Button size="sm" className="flex-1" disabled={!exportReady} onClick={handleDownload}>
            <Download size={14} />
            {t('dashboard.configs.downloadConfig')}
          </Button>
        </div>
        </>
        )}
      </div>
    </div>
  )
}
