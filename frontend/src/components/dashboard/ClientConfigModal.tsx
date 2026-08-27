import { useEffect, useState } from 'react'
import { X, Download, Copy, Check, GitBranch, Wrench } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import type { VpnConfig, VpsServer } from '@/lib/types'
import { buildClientConfigBundle, extractVlessUri, qrCodeUrl } from '@/lib/client-config'
import { copyToClipboard } from '@/lib/utils'
import { PROTOCOLS } from '@/lib/constants'
import { configUsesAwg } from '@/lib/deploy-history'
import { useI18n } from '@/i18n/I18nProvider'
import * as api from '@/lib/api'

interface ClientConfigModalProps {
  config: VpnConfig
  server: VpsServer
  servers?: VpsServer[]
  onClose: () => void
  onConfigUpdated?: (config: VpnConfig) => void
}

export function ClientConfigModal({
  config,
  server,
  servers,
  onClose,
  onConfigUpdated,
}: ClientConfigModalProps) {
  const { t } = useI18n()
  const [liveConfig, setLiveConfig] = useState(config)
  const [repairing, setRepairing] = useState(false)
  const [loadingConfig, setLoadingConfig] = useState(false)
  const bundle = buildClientConfigBundle(liveConfig, server, servers)

  useEffect(() => {
    if (
      liveConfig.id.startsWith('demo-') ||
      liveConfig.id === 'pending' ||
      liveConfig.id === 'pending-chain'
    ) {
      return
    }
    let cancelled = false
    setLoadingConfig(true)
    api
      .fetchClientConfig(liveConfig.id)
      .then(({ config: clientConfig }) => {
        if (cancelled) return
        const vlessUri = extractVlessUri(clientConfig)
        setLiveConfig((prev) => ({
          ...prev,
          clientConfig,
          ...(vlessUri ? { vlessUri } : {}),
        }))
      })
      .catch(() => {
        /* keep whatever the list API already returned */
      })
      .finally(() => {
        if (!cancelled) setLoadingConfig(false)
      })
    return () => {
      cancelled = true
    }
  }, [liveConfig.id])
  const needsAmneziaRepair =
    !liveConfig.hops?.length &&
    configUsesAwg(liveConfig) &&
    !/\bH1\s*=/.test(bundle.fileConfig)
  const [copiedFile, setCopiedFile] = useState(false)
  const [copiedLink, setCopiedLink] = useState(false)

  async function handleCopyFile() {
    const ok = await copyToClipboard(bundle.fileConfig)
    if (!ok) return
    setCopiedFile(true)
    setTimeout(() => setCopiedFile(false), 2000)
  }

  async function handleCopyLink() {
    if (!bundle.shareLink) return
    const ok = await copyToClipboard(bundle.shareLink.uri)
    if (!ok) return
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
    setRepairing(true)
    try {
      const updated = await api.repairAmneziaAwgConfigApi(liveConfig.id)
      setLiveConfig(updated)
      onConfigUpdated?.(updated)
    } finally {
      setRepairing(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
      <div className="w-full max-w-lg glass rounded-2xl p-5 glow-violet max-h-[90vh] overflow-y-auto">
        <div className="flex items-start justify-between gap-3 mb-4">
          <div className="min-w-0">
            <h3 className="font-semibold">{t('dashboard.configs.clientConfig')}</h3>
            <p className="text-xs text-zinc-500 mt-0.5">
              {PROTOCOLS[liveConfig.protocol].label} · {server.name}
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
              <p className="text-xs text-violet-400/90 mt-1.5 flex items-start gap-1.5">
                <GitBranch size={12} className="shrink-0 mt-0.5" />
                <span>
                  {t('dashboard.configs.chainEntry')}
                  {bundle.chainRoute && (
                    <span className="block text-zinc-500 mt-0.5 font-mono text-[10px]">{bundle.chainRoute}</span>
                  )}
                </span>
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 text-zinc-500 hover:text-white cursor-pointer shrink-0"
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
            <p className="text-[10px] text-zinc-600 mt-2 text-center">
              {bundle.shareLink
                ? t('dashboard.configs.qrScheme', { scheme: bundle.shareLink.scheme })
                : t('dashboard.configs.qrFile')}
            </p>
          </div>
        )}

        {bundle.shareLink && (
          <div className="mb-4 rounded-xl border border-cyan-500/15 bg-cyan-500/[0.04] p-3">
            <pre className="text-[11px] font-mono text-cyan-300/90 overflow-x-auto whitespace-pre-wrap break-all max-h-24">
              {bundle.shareLink.uri}
            </pre>
            <Button variant="secondary" size="sm" className="mt-2 w-full" onClick={handleCopyLink}>
              {copiedLink ? <Check size={14} /> : <Copy size={14} />}
              {copiedLink ? t('common.copied') : t('dashboard.configs.copyScheme', { scheme: bundle.shareLink.scheme })}
            </Button>
          </div>
        )}

        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500 mb-2">
            {t('dashboard.configs.configFile')}
          </p>
          <pre className="text-[11px] font-mono text-zinc-400 bg-black/30 rounded-xl p-3 overflow-x-auto max-h-40 whitespace-pre-wrap break-all">
            {bundle.fileConfig}
          </pre>
        </div>

        <div className="flex flex-col sm:flex-row gap-2 mt-4">
          <Button variant="secondary" size="sm" className="flex-1" onClick={handleCopyFile}>
            {copiedFile ? <Check size={14} /> : <Copy size={14} />}
            {copiedFile ? t('common.copied') : t('dashboard.configs.copyFile')}
          </Button>
          <Button size="sm" className="flex-1" onClick={handleDownload}>
            <Download size={14} />
            {t('dashboard.configs.downloadConfig')}
          </Button>
        </div>
      </div>
    </div>
  )
}
