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
            <h3 className="font-semibold">{t('dashboard.configs.clientConfig')}</h3>
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

        {bundle.supportsQr && (
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
      </div>
    </div>
  )
}
