import { useEffect, useMemo, useState } from 'react'
import {
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  ExternalLink,
  Globe,
  Lock,
  Play,
  Plus,
  QrCode,
  RefreshCw,
  Send,
  Server,
  ShieldCheck,
  Square,
  Trash2,
  Zap,
} from 'lucide-react'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { PageHeader } from '@/components/ui/PageHeader'
import { Select, type SelectOption } from '@/components/ui/Select'
import { SocksProxyPanel } from '@/components/dashboard/SocksProxyPanel'
import { QrImage } from '@/components/ui/QrImage'
import { AppIcon } from '@/components/ui/AppIcon'
import { useDashboard } from '@/lib/dashboard-context'
import { useI18n } from '@/i18n/i18n-context'
import { standaloneProxiesApi } from '@/lib/api'
import type { StandaloneProxy, StandaloneProxyType, VpnConfig, VpsServer } from '@/lib/types'
import { cn } from '@/lib/utils'
import { getLocationFlag, getLocationCity, countryCodeFromLocation } from '@/lib/country-utils'

function isChainConfig(config: VpnConfig): boolean {
  return Boolean(config.hops && config.hops.length >= 2)
}

function serverLocationLabel(server: VpsServer): string | undefined {
  const countryCode = countryCodeFromLocation(server.location)?.toUpperCase()
  const city = getLocationCity(server.location)
  const label = [countryCode, city].filter(Boolean).join(' · ')
  return label || server.location
}

const PROXY_TYPE_CONFIG = {
  socks5: {
    badge: 'bg-cyan-500/15 text-cyan-400 border-cyan-500/30',
    icon: Globe,
    defaultPort: 1080,
  },
  https: {
    badge: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
    icon: ShieldCheck,
    defaultPort: 8443,
  },
  mtproto: {
    badge: 'bg-sky-500/15 text-sky-400 border-sky-500/30',
    icon: Send,
    defaultPort: 443,
  },
  shadowsocks: {
    badge: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
    icon: Zap,
    defaultPort: 8388,
  },
} as const

const SHADOWSOCKS_CIPHER_OPTIONS: SelectOption[] = [
  {
    value: 'AEAD_CHACHA20_POLY1305',
    label: 'chacha20-ietf-poly1305',
    description: 'Быстрый AEAD шифр (рекомендуется)',
  },
  {
    value: 'AEAD_AES_256_GCM',
    label: 'aes-256-gcm',
    description: 'Аппаратное AES-NI ускорение',
  },
  {
    value: 'AEAD_AES_128_GCM',
    label: 'aes-128-gcm',
    description: 'Легковесный 128-бит AEAD',
  },
]

export function ProxyTab() {
  const { t } = useI18n()
  const { servers, configs } = useDashboard()

  const [proxies, setProxies] = useState<StandaloneProxy[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionBusy, setActionBusy] = useState<Record<string, boolean>>({})

  // Modal create
  const [modalOpen, setModalOpen] = useState(false)
  const [deploying, setDeploying] = useState(false)
  const [deployError, setDeployError] = useState<string | null>(null)
  const [selectedServerId, setSelectedServerId] = useState('')
  const [selectedType, setSelectedType] = useState<StandaloneProxyType>('socks5')
  const [customName, setCustomName] = useState('')
  const [customPort, setCustomPort] = useState<string>('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [sniDomain, setSniDomain] = useState('cloudflare.com')
  const [cipher, setCipher] = useState('AEAD_CHACHA20_POLY1305')

  // QR Modal
  const [qrModalData, setQrModalData] = useState<{ title: string; uri: string } | null>(null)

  // Expanded details per proxy
  const [expandedDetails, setExpandedDetails] = useState<Record<string, boolean>>({})
  const [copiedKey, setCopiedKey] = useState<string | null>(null)

  // Legacy VPN socks collapse
  const [showVpnSidecars, setShowVpnSidecars] = useState(false)

  const activeTargets = useMemo(
    () => configs.filter((c) => c.status === 'active'),
    [configs],
  )
  const socksCount = useMemo(
    () => activeTargets.filter((c) => c.socksProxy?.enabled).length,
    [activeTargets],
  )

  const serverSelectOptions: SelectOption[] = useMemo(
    () =>
      servers.map((s) => ({
        value: s.id,
        label: s.name,
        description: [s.host, serverLocationLabel(s)].filter(Boolean).join(' · '),
        leading: (
          <span className="relative flex shrink-0 items-center justify-center rounded-lg surface-panel leading-none h-7 w-7 text-lg">
            {getLocationFlag(s.location)}
            <span
              className={cn(
                'absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full border-2 border-surface-850',
                s.status === 'online' ? 'bg-emerald-400' : 'bg-zinc-500',
              )}
            />
          </span>
        ),
        meta: s.status !== 'online' ? `[${s.status}]` : undefined,
      })),
    [servers],
  )

  useEffect(() => {
    loadProxies()
  }, [])

  async function loadProxies() {
    setLoading(true)
    setError(null)
    try {
      const data = await standaloneProxiesApi.list()
      setProxies(data)
    } catch (err: any) {
      setError(err?.detail || err?.message || 'Failed to load proxies')
    } finally {
      setLoading(false)
    }
  }

  function openCreateModal() {
    const firstOnline = servers.find((s) => s.status === 'online') || servers[0]
    setSelectedServerId(firstOnline?.id || '')
    setSelectedType('socks5')
    setCustomName('')
    setCustomPort('')
    setUsername('')
    setPassword('')
    setSniDomain('cloudflare.com')
    setCipher('AEAD_CHACHA20_POLY1305')
    setDeployError(null)
    setModalOpen(true)
  }

  async function handleDeployProxy(e: React.FormEvent) {
    e.preventDefault()
    if (!selectedServerId) {
      setDeployError('Please select a VPS server')
      return
    }

    setDeploying(true)
    setDeployError(null)

    try {
      const portVal = customPort ? parseInt(customPort, 10) : undefined
      await standaloneProxiesApi.create({
        serverId: selectedServerId,
        proxyType: selectedType,
        name: customName.trim() || undefined,
        listenPort: portVal,
        username: username.trim() || undefined,
        password: password.trim() || undefined,
        sni: selectedType === 'mtproto' ? sniDomain.trim() || undefined : undefined,
        cipher: selectedType === 'shadowsocks' ? cipher : undefined,
      })

      setModalOpen(false)
      await loadProxies()
    } catch (err: any) {
      setDeployError(err?.detail || err?.message || 'Deployment failed')
    } finally {
      setDeploying(false)
    }
  }

  async function handleToggleStatus(proxy: StandaloneProxy) {
    setActionBusy((prev) => ({ ...prev, [proxy.id]: true }))
    try {
      if (proxy.status === 'active') {
        await standaloneProxiesApi.stop(proxy.id)
      } else {
        await standaloneProxiesApi.start(proxy.id)
      }
      await loadProxies()
    } catch (err: any) {
      alert(err?.detail || err?.message || 'Action failed')
    } finally {
      setActionBusy((prev) => ({ ...prev, [proxy.id]: false }))
    }
  }

  async function handleDeleteProxy(proxy: StandaloneProxy) {
    if (!confirm(t('dashboard.proxyTab.card.deleteConfirm'))) return
    setActionBusy((prev) => ({ ...prev, [proxy.id]: true }))
    try {
      await standaloneProxiesApi.delete(proxy.id)
      await loadProxies()
    } catch (err: any) {
      alert(err?.detail || err?.message || 'Delete failed')
    } finally {
      setActionBusy((prev) => ({ ...prev, [proxy.id]: false }))
    }
  }

  function copyToClipboard(text: string, key: string) {
    navigator.clipboard.writeText(text)
    setCopiedKey(key)
    setTimeout(() => setCopiedKey(null), 2000)
  }

  function toggleDetails(id: string) {
    setExpandedDetails((prev) => ({ ...prev, [id]: !prev[id] }))
  }

  return (
    <div className="dashboard-page space-y-6">
      <PageHeader
        title={t('dashboard.proxyTab.title')}
        subtitle={t('dashboard.proxyTab.subtitle')}
      >
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="ghost"
            onClick={loadProxies}
            disabled={loading}
            className="text-xs border border-white/10 text-zinc-400 hover:text-white"
          >
            <RefreshCw size={13} className={cn(loading && 'animate-spin')} />
          </Button>
          <Button
            size="sm"
            variant="primary"
            onClick={openCreateModal}
            className="text-xs font-semibold bg-accent-cyan hover:bg-accent-cyan/90 text-black flex items-center gap-1.5 shadow-lg shadow-accent-cyan/20"
          >
            <Plus size={14} />
            {t('dashboard.proxyTab.deployProxyBtn')}
          </Button>
        </div>
      </PageHeader>

      {error && (
        <Card className="p-4 border-rose-500/30 bg-rose-500/10 text-rose-300 text-sm">
          {error}
        </Card>
      )}

      {/* Main Standalone Proxies Section */}
      {loading && proxies.length === 0 ? (
        <div className="py-16 text-center text-sm text-text-muted">
          <RefreshCw size={24} className="mx-auto animate-spin text-accent-cyan/80 mb-3" />
          {t('common.waiting')}…
        </div>
      ) : proxies.length === 0 ? (
        <Card className="p-10 sm:p-12 text-center border-dashed border-white/10 bg-zinc-950/40 relative overflow-hidden">
          <div className="relative mx-auto mb-4 flex justify-center">
            <AppIcon name="no-proxies" size={96} className="relative drop-shadow-[0_4px_24px_rgba(245,158,11,0.18)]" />
          </div>
          <h3 className="text-lg font-semibold text-white">{t('dashboard.proxyTab.empty')}</h3>
          <p className="text-sm text-text-muted mt-2 max-w-md mx-auto">
            {t('dashboard.proxyTab.emptyHint')}
          </p>
          <div className="flex justify-center mt-6">
            <Button
              variant="primary"
              onClick={openCreateModal}
              className="text-sm font-semibold bg-accent-cyan hover:bg-accent-cyan/90 text-black flex items-center gap-2"
            >
              <Plus size={16} />
              {t('dashboard.proxyTab.deployProxyBtn')}
            </Button>
          </div>
        </Card>
      ) : (
        <div className="grid gap-4">
          {proxies.map((proxy) => {
            const server = servers.find((s) => s.id === proxy.serverId)
            const typeConf = PROXY_TYPE_CONFIG[proxy.proxyType] || PROXY_TYPE_CONFIG.socks5
            const Icon = typeConf.icon
            const isBusy = Boolean(actionBusy[proxy.id])
            const isExpanded = Boolean(expandedDetails[proxy.id])

            return (
              <Card
                key={proxy.id}
                className="p-5 border border-white/10 bg-zinc-900/40 hover:border-white/20 transition-all rounded-2xl"
              >
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                  <div className="flex items-start sm:items-center gap-3.5">
                    <div className="w-10 h-10 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center shrink-0">
                      <Icon size={20} className="text-accent-cyan" />
                    </div>
                    <div>
                      <div className="flex items-center gap-2.5 flex-wrap">
                        <span className="font-semibold text-white text-base">
                          {proxy.name}
                        </span>
                        <span
                          className={cn(
                            'px-2 py-0.5 rounded-full text-[11px] font-medium border uppercase tracking-wider',
                            typeConf.badge,
                          )}
                        >
                          {t(`dashboard.proxyTab.types.${proxy.proxyType}`)}
                        </span>
                        <span
                          className={cn(
                            'inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium border',
                            proxy.status === 'active'
                              ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                              : 'bg-zinc-800 text-zinc-400 border-zinc-700',
                          )}
                        >
                          <span
                            className={cn(
                              'w-1.5 h-1.5 rounded-full',
                              proxy.status === 'active' ? 'bg-emerald-400 animate-pulse' : 'bg-zinc-500',
                            )}
                          />
                          {proxy.status}
                        </span>
                      </div>

                      <div className="flex items-center gap-3 text-xs text-text-muted mt-1 flex-wrap">
                        <span className="flex items-center gap-1">
                          <Server size={12} className="text-zinc-500" />
                          {server?.name || t('dashboard.proxyTab.unknownServer')} ({server?.host || proxy.meta?.serverHost || '—'})
                        </span>
                        <span>•</span>
                        <span>
                          {t('dashboard.proxyTab.card.port')}:{' '}
                          <strong className="text-zinc-300 font-mono">{proxy.listenPort}</strong>
                        </span>
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 shrink-0 self-end sm:self-center">
                    {proxy.clientUri && (
                      <>
                        {proxy.proxyType === 'mtproto' && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => window.open(proxy.clientUri, '_blank')}
                            className="text-xs text-sky-400 hover:text-sky-300 hover:bg-sky-500/10 border border-sky-500/20"
                            title={t('dashboard.proxyTab.card.openTelegram')}
                          >
                            <Send size={13} className="mr-1" />
                            {t('dashboard.proxyTab.card.openTelegram')}
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() =>
                            setQrModalData({
                              title: `${proxy.name} (${t(`dashboard.proxyTab.types.${proxy.proxyType}`)})`,
                              uri: proxy.clientUri,
                            })
                          }
                          className="text-xs border border-white/10 text-zinc-300 hover:text-white"
                          title={t('dashboard.proxyTab.card.qrCode')}
                        >
                          <QrCode size={13} className="mr-1 text-accent-cyan" />
                          {t('dashboard.proxyTab.card.qrCode')}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => copyToClipboard(proxy.clientUri, proxy.id)}
                          className="text-xs border border-white/10 text-zinc-300 hover:text-white"
                          title={t('dashboard.proxyTab.card.copyUri')}
                        >
                          {copiedKey === proxy.id ? (
                            <>
                              <Check size={13} className="mr-1 text-emerald-400" />
                              {t('dashboard.proxyTab.card.copied')}
                            </>
                          ) : (
                            <>
                              <Copy size={13} className="mr-1" />
                              {t('dashboard.proxyTab.card.copyUri')}
                            </>
                          )}
                        </Button>
                      </>
                    )}

                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={isBusy}
                      onClick={() => handleToggleStatus(proxy)}
                      className="text-xs border border-white/10 text-zinc-300"
                    >
                      {proxy.status === 'active' ? (
                        <>
                          <Square size={12} className="mr-1 text-amber-400" />
                          {t('dashboard.proxyTab.card.stop')}
                        </>
                      ) : (
                        <>
                          <Play size={12} className="mr-1 text-emerald-400" />
                          {t('dashboard.proxyTab.card.start')}
                        </>
                      )}
                    </Button>

                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={isBusy}
                      onClick={() => handleDeleteProxy(proxy)}
                      className="text-xs text-rose-400 hover:text-rose-300 hover:bg-rose-500/10"
                      title={t('dashboard.proxyTab.card.delete')}
                    >
                      <Trash2 size={13} />
                    </Button>

                    <button
                      type="button"
                      onClick={() => toggleDetails(proxy.id)}
                      className="h-8 px-2.5 rounded-lg border border-white/10 bg-white/[0.02] hover:bg-white/5 hover:border-white/20 text-text-muted hover:text-white flex items-center gap-1.5 text-xs transition-all cursor-pointer"
                      title={isExpanded ? t('dashboard.proxyTab.collapse') : t('dashboard.proxyTab.expand')}
                    >
                      <span className="text-[11px] font-medium hidden sm:inline">
                        {isExpanded ? t('dashboard.proxyTab.collapse') : t('dashboard.proxyTab.expand')}
                      </span>
                      <ChevronDown
                        size={14}
                        className={cn(
                          'transition-transform duration-200',
                          isExpanded && 'rotate-180 text-accent-cyan',
                        )}
                      />
                    </button>
                  </div>
                </div>

                {/* Direct Connection URI Bar */}
                {proxy.clientUri && (
                  <div className="mt-3.5 pt-3 border-t border-white/5 flex items-center justify-between gap-2 bg-black/40 rounded-xl px-3 py-2">
                    <div className="font-mono text-xs text-zinc-300 truncate select-all">
                      {proxy.clientUri}
                    </div>
                  </div>
                )}

                {/* Expanded Details */}
                {isExpanded && (
                  <div className="mt-3 pt-3 border-t border-white/5 grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3 text-xs text-text-muted bg-white/[0.02] p-3 rounded-xl">
                    {proxy.meta?.username && (
                      <div>
                        <span className="block text-zinc-500">{t('dashboard.proxyTab.card.username')}:</span>
                        <span className="font-mono text-zinc-200 select-all">{proxy.meta.username}</span>
                      </div>
                    )}
                    {proxy.meta?.password && (
                      <div>
                        <span className="block text-zinc-500">{t('dashboard.proxyTab.card.password')}:</span>
                        <span className="font-mono text-zinc-200 select-all">{proxy.meta.password}</span>
                      </div>
                    )}
                    {proxy.meta?.secret && (
                      <div className="col-span-full">
                        <span className="block text-zinc-500">{t('dashboard.proxyTab.card.secret')}:</span>
                        <span className="font-mono text-zinc-200 break-all select-all">{proxy.meta.secret}</span>
                      </div>
                    )}
                    {proxy.meta?.sni && (
                      <div>
                        <span className="block text-zinc-500">{t('dashboard.proxyTab.card.sni')}:</span>
                        <span className="font-mono text-zinc-200">{proxy.meta.sni}</span>
                      </div>
                    )}
                    {proxy.meta?.cipher && (
                      <div>
                        <span className="block text-zinc-500">{t('dashboard.proxyTab.card.cipher')}:</span>
                        <span className="font-mono text-zinc-200">{proxy.meta.cipher}</span>
                      </div>
                    )}
                    {proxy.containerName && (
                      <div>
                        <span className="block text-zinc-500">Container:</span>
                        <span className="font-mono text-zinc-400">{proxy.containerName}</span>
                      </div>
                    )}
                    {proxy.runtime && (
                      <div>
                        <span className="block text-zinc-500">Runtime:</span>
                        <span className="font-mono text-zinc-400">{proxy.runtime}</span>
                      </div>
                    )}
                  </div>
                )}
              </Card>
            )
          })}
        </div>
      )}

      {/* Legacy / Auxiliary VPN SOCKS sidecars section (collapsible) */}
      {activeTargets.length > 0 && (
        <div className="mt-8 pt-6 border-t border-white/10">
          <button
            type="button"
            onClick={() => setShowVpnSidecars((v) => !v)}
            className="w-full flex items-center justify-between p-4 rounded-2xl bg-zinc-900/40 border border-white/10 hover:border-white/20 transition-all text-left cursor-pointer group"
          >
            <div>
              <h4 className="text-sm font-semibold text-zinc-200 flex items-center gap-2">
                <span>{t('dashboard.proxyTab.vpnSidecarsSection')}</span>
                <span className="text-xs px-2 py-0.5 rounded-full bg-white/5 text-text-muted border border-white/5">
                  {socksCount} / {activeTargets.length}
                </span>
              </h4>
              <p className="text-xs text-text-muted mt-1">
                {t('dashboard.proxyTab.vpnSidecarsHint')}
              </p>
            </div>
            <div className="w-8 h-8 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center shrink-0 text-text-muted group-hover:text-white group-hover:border-white/20 transition-all">
              <ChevronDown
                size={16}
                className={cn(
                  'transition-transform duration-200',
                  showVpnSidecars && 'rotate-180 text-accent-cyan',
                )}
              />
            </div>
          </button>

          {showVpnSidecars && (
            <div className="grid gap-4 mt-4 animate-in fade-in duration-150">
              {activeTargets.map((config) => (
                <SocksProxyPanel key={config.id} config={config} defaultExpanded={false} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* MODAL: Deploy Proxy */}
      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-150">
          <form
            onSubmit={handleDeployProxy}
            className="w-full max-w-xl rounded-2xl bg-zinc-950 border border-white/10 p-6 shadow-2xl space-y-5 max-h-[90vh] overflow-y-auto"
          >
            <div className="flex items-center justify-between pb-3 border-b border-white/10">
              <h3 className="text-base font-semibold text-white flex items-center gap-2.5">
                <AppIcon name="no-proxies" size={24} className="shrink-0" />
                {t('dashboard.proxyTab.modal.title')}
              </h3>
              <button
                type="button"
                onClick={() => setModalOpen(false)}
                className="text-text-muted hover:text-white text-lg px-1"
              >
                ✕
              </button>
            </div>

            {deployError && (
              <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs">
                {deployError}
              </div>
            )}

            {/* Protocol Type Selection Cards */}
            <div>
              <label className="text-xs font-medium text-text-secondary block mb-2">
                {t('dashboard.proxyTab.modal.type')}
              </label>
              <div className="grid grid-cols-2 gap-2.5">
                {(['socks5', 'https', 'mtproto', 'shadowsocks'] as const).map((type) => {
                  const conf = PROXY_TYPE_CONFIG[type]
                  const Icon = conf.icon
                  const active = selectedType === type

                  return (
                    <button
                      key={type}
                      type="button"
                      onClick={() => {
                        setSelectedType(type)
                        setCustomPort('')
                      }}
                      className={cn(
                        'flex flex-col items-start p-3 rounded-xl border text-left transition-all cursor-pointer',
                        active
                          ? 'bg-accent-cyan/10 border-accent-cyan shadow-sm shadow-accent-cyan/20'
                          : 'bg-white/[0.02] border-white/10 hover:border-white/20',
                      )}
                    >
                      <div className="flex items-center gap-2 mb-1">
                        <Icon
                          size={16}
                          className={active ? 'text-accent-cyan' : 'text-zinc-400'}
                        />
                        <span
                          className={cn(
                            'text-xs font-semibold',
                            active ? 'text-white' : 'text-zinc-300',
                          )}
                        >
                          {t(`dashboard.proxyTab.types.${type}`)}
                        </span>
                      </div>
                      <p className="text-[11px] text-text-muted leading-relaxed line-clamp-2">
                        {t(`dashboard.proxyTab.typesDesc.${type}`)}
                      </p>
                    </button>
                  )
                })}
              </div>
            </div>

            {/* Beautiful Custom Server Selection via Select */}
            <div>
              <Select
                label={t('dashboard.proxyTab.modal.server')}
                value={selectedServerId}
                onChange={setSelectedServerId}
                options={serverSelectOptions}
                placeholder="Выберите VPS сервер"
                renderValue={(opt) => {
                  const s = servers.find((srv) => srv.id === opt?.value)
                  if (!opt || !s) return null
                  return (
                    <div className="flex items-center gap-2.5 min-w-0">
                      <span className="relative flex shrink-0 items-center justify-center rounded-lg surface-panel leading-none h-6 w-6 text-base">
                        {getLocationFlag(s.location)}
                        <span
                          className={cn(
                            'absolute -bottom-0.5 -right-0.5 h-1.5 w-1.5 rounded-full',
                            s.status === 'online' ? 'bg-emerald-400' : 'bg-zinc-500',
                          )}
                        />
                      </span>
                      <span className="truncate text-zinc-100 font-medium text-xs">
                        {s.name}
                      </span>
                      <span className="text-[11px] text-text-muted font-mono truncate">
                        ({s.host})
                      </span>
                    </div>
                  )
                }}
                renderOption={(opt, isSelected) => {
                  const s = servers.find((srv) => srv.id === opt.value)
                  return (
                    <div className="flex items-center justify-between w-full gap-2">
                      <div className="flex items-center gap-2.5 min-w-0">
                        {opt.leading}
                        <div className="min-w-0 text-left">
                          <div
                            className={cn(
                              'text-xs font-medium truncate',
                              isSelected ? 'text-white' : 'text-zinc-200',
                            )}
                          >
                            {opt.label}
                          </div>
                          <div className="text-[11px] text-text-muted font-mono truncate">
                            {s?.host} {s?.location ? `· ${s.location}` : ''}
                          </div>
                        </div>
                      </div>
                      {isSelected && (
                        <span className="shrink-0 w-4 h-4 rounded-full bg-accent-cyan/20 flex items-center justify-center">
                          <span className="w-1.5 h-1.5 rounded-full bg-accent-cyan" />
                        </span>
                      )}
                    </div>
                  )
                }}
              />
            </div>

            {/* Name & Custom Port — Perfectly Aligned Grid */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
              <div>
                <div className="flex items-center justify-between h-5 mb-1.5">
                  <label className="text-xs font-medium text-text-secondary">
                    {t('dashboard.proxyTab.modal.name')}
                  </label>
                  <span className="text-[10px] text-text-muted uppercase tracking-wider font-medium">
                    {t('dashboard.proxyTab.modal.optional')}
                  </span>
                </div>
                <div className="relative">
                  <input
                    type="text"
                    placeholder={`My ${selectedType.toUpperCase()}`}
                    value={customName}
                    onChange={(e) => setCustomName(e.target.value)}
                    className="w-full h-10 rounded-xl bg-white/[0.04] glow-border text-zinc-100 placeholder:text-text-muted px-3 text-xs focus:outline-none focus:border-accent-cyan/35 focus:ring-2 focus:ring-accent-cyan/15 transition-all"
                  />
                </div>
                <p className="text-[11px] text-text-muted mt-1.5 truncate">
                  {t('dashboard.proxyTab.modal.nameHint')}
                </p>
              </div>

              <div>
                <div className="flex items-center justify-between h-5 mb-1.5">
                  <label className="text-xs font-medium text-text-secondary">
                    {t('dashboard.proxyTab.modal.port')}
                  </label>
                  <span className="text-[11px] text-accent-cyan font-mono font-medium">
                    {customPort
                      ? customPort
                      : `auto (${PROXY_TYPE_CONFIG[selectedType].defaultPort})`}
                  </span>
                </div>
                <div className="relative flex items-center">
                  <input
                    type="number"
                    placeholder={String(PROXY_TYPE_CONFIG[selectedType].defaultPort)}
                    value={customPort}
                    onChange={(e) => setCustomPort(e.target.value)}
                    min={1}
                    max={65535}
                    className="w-full h-10 rounded-xl bg-white/[0.04] glow-border text-zinc-100 placeholder:text-text-muted pl-3 pr-16 text-xs font-mono focus:outline-none focus:border-accent-cyan/35 focus:ring-2 focus:ring-accent-cyan/15 transition-all [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                  />
                  {/* Custom elegant stepper buttons */}
                  <div className="absolute right-1.5 flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => {
                        const cur = customPort
                          ? parseInt(customPort, 10)
                          : PROXY_TYPE_CONFIG[selectedType].defaultPort
                        if (cur > 1) setCustomPort(String(cur - 1))
                      }}
                      className="w-6 h-7 rounded-lg bg-white/5 hover:bg-white/10 active:bg-white/20 border border-white/10 hover:border-white/20 flex items-center justify-center text-text-muted hover:text-white transition-all cursor-pointer"
                      title="-1"
                    >
                      <ChevronDown size={14} />
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        const cur = customPort
                          ? parseInt(customPort, 10)
                          : PROXY_TYPE_CONFIG[selectedType].defaultPort
                        if (cur < 65535) setCustomPort(String(cur + 1))
                      }}
                      className="w-6 h-7 rounded-lg bg-white/5 hover:bg-white/10 active:bg-white/20 border border-white/10 hover:border-white/20 flex items-center justify-center text-text-muted hover:text-white transition-all cursor-pointer"
                      title="+1"
                    >
                      <ChevronUp size={14} />
                    </button>
                  </div>
                </div>
                <p className="text-[11px] text-text-muted mt-1.5 truncate">
                  {t('dashboard.proxyTab.modal.portHint')}
                </p>
              </div>
            </div>

            {/* Protocol-Specific Options */}
            {(selectedType === 'socks5' || selectedType === 'https') && (
              <div className="space-y-3 pt-3 border-t border-white/5">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                  <div>
                    <label className="text-xs font-medium text-text-secondary block mb-1.5">
                      {t('dashboard.proxyTab.modal.username')}
                    </label>
                    <input
                      type="text"
                      placeholder="user..."
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      className="w-full h-10 rounded-xl bg-white/[0.04] glow-border text-zinc-100 placeholder:text-text-muted px-3 text-xs font-mono focus:outline-none focus:border-accent-cyan/35 focus:ring-2 focus:ring-accent-cyan/15 transition-all"
                    />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-text-secondary block mb-1.5">
                      {t('dashboard.proxyTab.modal.password')}
                    </label>
                    <input
                      type="text"
                      placeholder="Auto-generated"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="w-full h-10 rounded-xl bg-white/[0.04] glow-border text-zinc-100 placeholder:text-text-muted px-3 text-xs font-mono focus:outline-none focus:border-accent-cyan/35 focus:ring-2 focus:ring-accent-cyan/15 transition-all"
                    />
                  </div>
                </div>
                <p className="text-[11px] text-text-muted">
                  {t('dashboard.proxyTab.modal.authHint')}
                </p>
              </div>
            )}

            {selectedType === 'mtproto' && (
              <div className="space-y-2 pt-3 border-t border-white/5">
                <label className="text-xs font-medium text-text-secondary block mb-1.5">
                  {t('dashboard.proxyTab.modal.sniDomain')}
                </label>
                <input
                  type="text"
                  value={sniDomain}
                  onChange={(e) => setSniDomain(e.target.value)}
                  placeholder="cloudflare.com"
                  className="w-full h-10 rounded-xl bg-white/[0.04] glow-border text-zinc-100 placeholder:text-text-muted px-3 text-xs font-mono focus:outline-none focus:border-accent-cyan/35 focus:ring-2 focus:ring-accent-cyan/15 transition-all"
                />
                <p className="text-[11px] text-text-muted">
                  {t('dashboard.proxyTab.modal.sniHint')}
                </p>
              </div>
            )}

            {selectedType === 'shadowsocks' && (
              <div className="space-y-3 pt-3 border-t border-white/5">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
                  <div>
                    <Select
                      label={t('dashboard.proxyTab.modal.cipher')}
                      value={cipher}
                      onChange={setCipher}
                      options={SHADOWSOCKS_CIPHER_OPTIONS}
                    />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-text-secondary block mb-1.5">
                      {t('dashboard.proxyTab.modal.password')}
                    </label>
                    <input
                      type="text"
                      placeholder="Auto-generated"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="w-full h-10 rounded-xl bg-white/[0.04] glow-border text-zinc-100 placeholder:text-text-muted px-3 text-xs font-mono focus:outline-none focus:border-accent-cyan/35 focus:ring-2 focus:ring-accent-cyan/15 transition-all"
                    />
                  </div>
                </div>
                <p className="text-[11px] text-text-muted">
                  {t('dashboard.proxyTab.modal.authHint')}
                </p>
              </div>
            )}

            <div className="flex items-center justify-end gap-2 pt-4 border-t border-white/10">
              <Button
                type="button"
                variant="ghost"
                onClick={() => setModalOpen(false)}
                className="text-xs"
              >
                {t('dashboard.proxyTab.modal.cancelBtn')}
              </Button>
              <Button
                type="submit"
                variant="primary"
                disabled={deploying}
                className="text-xs font-semibold bg-accent-cyan hover:bg-accent-cyan/90 text-black flex items-center gap-1.5 shadow-lg shadow-accent-cyan/20"
              >
                {deploying && <RefreshCw size={13} className="animate-spin" />}
                {deploying
                  ? t('dashboard.proxyTab.deploying')
                  : t('dashboard.proxyTab.modal.deployBtn')}
              </Button>
            </div>
          </form>
        </div>
      )}

      {/* MODAL: QR Code Display */}
      {qrModalData && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-150">
          <div className="w-full max-w-sm rounded-2xl bg-zinc-950 border border-white/10 p-6 shadow-2xl text-center space-y-4">
            <div className="flex items-center justify-between pb-2 border-b border-white/10">
              <h3 className="text-sm font-semibold text-white truncate text-left">
                {qrModalData.title}
              </h3>
              <button
                type="button"
                onClick={() => setQrModalData(null)}
                className="text-text-muted hover:text-white text-base px-1"
              >
                ✕
              </button>
            </div>

            <div className="flex justify-center p-3 bg-white rounded-2xl shadow-inner mx-auto w-fit">
              <QrImage data={qrModalData.uri} size={220} className="object-contain" />
            </div>

            <p className="text-[11px] text-text-muted">
              {t('dashboard.proxyTab.qrModal.scanHint')}
            </p>

            <div className="flex justify-center gap-2 pt-2">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => copyToClipboard(qrModalData.uri, 'modal-qr')}
                className="text-xs border border-white/10 text-zinc-300 hover:text-white"
              >
                {copiedKey === 'modal-qr' ? (
                  <>
                    <Check size={13} className="mr-1 text-emerald-400" />
                    {t('dashboard.proxyTab.card.copied')}
                  </>
                ) : (
                  <>
                    <Copy size={13} className="mr-1" />
                    {t('dashboard.proxyTab.card.copyUri')}
                  </>
                )}
              </Button>
              <Button
                size="sm"
                variant="primary"
                onClick={() => setQrModalData(null)}
                className="text-xs bg-white/10 hover:bg-white/20 text-white"
              >
                {t('dashboard.proxyTab.qrModal.close')}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
