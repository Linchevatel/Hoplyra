import { useEffect, useState } from 'react'
import {
  Radio,
  Server,
  Plus,
  QrCode,
  Copy,
  Check,
  Play,
  Square,
  Trash2,
  RefreshCw,
  Download,
  AlertCircle,
  Settings,
  Loader2,
} from 'lucide-react'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { PageHeader } from '@/components/ui/PageHeader'
import { QrImage } from '@/components/ui/QrImage'
import { useDashboard } from '@/lib/dashboard-context'
import { useI18n } from '@/i18n/i18n-context'
import { useDeployLogDrawer } from '@/lib/deploy-log-context'
import { realmsApi } from '@/lib/api'
import type { RealmBroker, RealmTunnel, RealmTunnelConfig } from '@/lib/types'
import { cn } from '@/lib/utils'

export function HysteriaRealmsTab() {
  const { t } = useI18n()
  const { servers, pushNotification } = useDashboard()
  const { open: openDeployLog } = useDeployLogDrawer()

  const [loading, setLoading] = useState(true)
  const [activeBroker, setActiveBroker] = useState<RealmBroker | null>(null)
  const [tunnels, setTunnels] = useState<RealmTunnel[]>([])
  const [error, setError] = useState<string | null>(null)

  // Modals
  const [createTunnelOpen, setCreateTunnelOpen] = useState(false)
  const [deployBrokerOpen, setDeployBrokerOpen] = useState(false)
  const [connectModalTunnel, setConnectModalTunnel] = useState<RealmTunnel | null>(null)
  const [connectConfig, setConnectConfig] = useState<RealmTunnelConfig | null>(null)
  const [loadingConfig, setLoadingConfig] = useState(false)

  // Action states
  const [busyTunnelId, setBusyTunnelId] = useState<string | null>(null)
  const [busyBroker, setBusyBroker] = useState(false)
  const [copiedKey, setCopiedKey] = useState<string | null>(null)

  // Form: Create Tunnel
  const [tunnelServerId, setTunnelServerId] = useState('')
  const [tunnelName, setTunnelName] = useState('')
  const [tunnelBrokerType, setTunnelBrokerType] = useState<'public' | 'self_hosted' | 'custom'>('public')
  const [customBrokerUrl, setCustomBrokerUrl] = useState('')
  const [tunnelRealmName, setTunnelRealmName] = useState('')
  const [tunnelAuthPassword, setTunnelAuthPassword] = useState('')
  const [creatingTunnel, setCreatingTunnel] = useState(false)

  // Form: Deploy Broker
  const [brokerServerId, setBrokerServerId] = useState('')
  const [brokerPort, setBrokerPort] = useState(8443)
  const [brokerDomainOrIp, setBrokerDomainOrIp] = useState('')
  const [brokerToken, setBrokerToken] = useState('')
  const [deployingBroker, setDeployingBroker] = useState(false)

  useEffect(() => {
    loadData()
  }, [])

  async function loadData() {
    setLoading(true)
    setError(null)
    try {
      const res = await realmsApi.getStatus()
      setActiveBroker(res.activeBroker || null)
      setTunnels(res.tunnels || [])
    } catch (err: any) {
      setError(err?.detail || err?.message || t('common.error'))
    } finally {
      setLoading(false)
    }
  }

  function generateRandomName() {
    const randomHex = Math.random().toString(16).substring(2, 10)
    return `hoplyra-${randomHex}`
  }

  function openCreateModal() {
    setTunnelServerId(servers[0]?.id || '')
    setTunnelName('')
    setTunnelRealmName(generateRandomName())
    setTunnelAuthPassword('')
    setTunnelBrokerType(activeBroker ? 'self_hosted' : 'public')
    setCreateTunnelOpen(true)
  }

  function openBrokerModal() {
    setBrokerServerId(servers[0]?.id || '')
    setBrokerPort(8443)
    setBrokerDomainOrIp('')
    setBrokerToken('')
    setDeployBrokerOpen(true)
  }

  async function waitForTunnelDeploy(tunnelId: string, maxWaitMs = 60000) {
    const start = Date.now()
    while (Date.now() - start < maxWaitMs) {
      await new Promise((r) => setTimeout(r, 1200))
      try {
        const res = await realmsApi.getStatus()
        setTunnels(res.tunnels || [])
        const found = res.tunnels?.find((t) => t.id === tunnelId)
        if (found) {
          if (found.status === 'active') return found
          if (found.status === 'error') {
            throw new Error(t('dashboard.realms.notifications.errorTitle') || 'Deployment failed')
          }
        }
      } catch (err: any) {
        if (err?.message?.includes('failed') || err?.message?.includes('Ошибка')) throw err
      }
    }
  }

  async function waitForBrokerDeploy(brokerId: string, maxWaitMs = 60000) {
    const start = Date.now()
    while (Date.now() - start < maxWaitMs) {
      await new Promise((r) => setTimeout(r, 1200))
      try {
        const res = await realmsApi.getStatus()
        setActiveBroker(res.activeBroker || null)
        if (res.activeBroker && res.activeBroker.id === brokerId) {
          if (res.activeBroker.status === 'active') return res.activeBroker
          if (res.activeBroker.status === 'error') {
            throw new Error(t('dashboard.realms.notifications.errorTitle') || 'Deployment failed')
          }
        }
      } catch (err: any) {
        if (err?.message?.includes('failed') || err?.message?.includes('Ошибка')) throw err
      }
    }
  }

  async function handleCreateTunnel(e: React.FormEvent) {
    e.preventDefault()
    if (!tunnelServerId) return
    const name = tunnelName.trim() || 'Hysteria-Realm'
    const realmName = tunnelRealmName.trim()
    const brokerType = tunnelBrokerType
    const customBroker = tunnelBrokerType === 'custom' ? customBrokerUrl.trim() : undefined
    const authPass = tunnelAuthPassword.trim() || undefined

    setCreateTunnelOpen(false)
    setCreatingTunnel(true)
    setError(null)
    try {
      const created = await realmsApi.deployTunnel({
        serverId: tunnelServerId,
        name,
        brokerType,
        customBrokerUrl: customBroker,
        realmName,
        authPassword: authPass,
        asyncDeploy: true,
      })
      openDeployLog(created.id, {
        title: 'Hysteria Realm',
        subtitle: created.name,
      })
      setTunnels((prev) => [created, ...prev.filter((t) => t.id !== created.id)])
      await waitForTunnelDeploy(created.id)
      pushNotification({
        id: crypto.randomUUID(),
        type: 'deploy_complete',
        title: t('dashboard.realms.notifications.tunnelCreated'),
        message: `${created.name} (${created.realmName})`,
        read: false,
        createdAt: new Date().toISOString(),
        serverId: created.serverId,
      })
      await loadData()
    } catch (err: any) {
      const msg = err?.detail || err?.message || t('common.error')
      setError(msg)
      pushNotification({
        id: crypto.randomUUID(),
        type: 'deploy_error',
        title: t('dashboard.realms.notifications.errorTitle'),
        message: msg,
        read: false,
        createdAt: new Date().toISOString(),
      })
      await loadData()
    } finally {
      setCreatingTunnel(false)
    }
  }

  async function handleDeployBroker(e: React.FormEvent) {
    e.preventDefault()
    if (!brokerServerId) return
    const port = Number(brokerPort) || 8443
    const domainOrIp = brokerDomainOrIp.trim() || undefined
    const token = brokerToken.trim() || undefined

    setDeployBrokerOpen(false)
    setDeployingBroker(true)
    setError(null)
    try {
      const deployed = await realmsApi.deployBroker({
        serverId: brokerServerId,
        listenPort: port,
        domainOrIp,
        token,
        asyncDeploy: true,
      })
      const server = servers.find((s) => s.id === brokerServerId)
      openDeployLog(deployed.id, {
        title: 'Rendezvous Broker',
        subtitle: `${deployed.domainOrIp || server?.host || 'server'}:${deployed.listenPort}`,
      })
      setActiveBroker(deployed)
      await waitForBrokerDeploy(deployed.id)
      pushNotification({
        id: crypto.randomUUID(),
        type: 'deploy_complete',
        title: t('dashboard.realms.notifications.brokerDeployed'),
        message: `${deployed.domainOrIp || server?.host || ''}:${deployed.listenPort}`,
        read: false,
        createdAt: new Date().toISOString(),
        serverId: deployed.serverId,
      })
      await loadData()
    } catch (err: any) {
      const msg = err?.detail || err?.message || t('common.error')
      setError(msg)
      pushNotification({
        id: crypto.randomUUID(),
        type: 'deploy_error',
        title: t('dashboard.realms.notifications.errorTitle'),
        message: msg,
        read: false,
        createdAt: new Date().toISOString(),
      })
      await loadData()
    } finally {
      setDeployingBroker(false)
    }
  }

  async function handleToggleTunnel(tunnel: RealmTunnel) {
    setBusyTunnelId(tunnel.id)
    try {
      if (tunnel.status === 'active') {
        await realmsApi.stopTunnel(tunnel.id)
        pushNotification({
          id: crypto.randomUUID(),
          type: 'deploy_complete',
          title: t('dashboard.realms.notifications.tunnelStopped'),
          message: tunnel.name,
          read: false,
          createdAt: new Date().toISOString(),
        })
      } else {
        await realmsApi.startTunnel(tunnel.id)
        pushNotification({
          id: crypto.randomUUID(),
          type: 'deploy_complete',
          title: t('dashboard.realms.notifications.tunnelStarted'),
          message: tunnel.name,
          read: false,
          createdAt: new Date().toISOString(),
        })
      }
      await loadData()
    } catch (err: any) {
      setError(err?.detail || err?.message || t('common.error'))
    } finally {
      setBusyTunnelId(null)
    }
  }

  async function handleDeleteTunnel(tunnel: RealmTunnel) {
    if (!window.confirm(`${t('common.delete')} ${tunnel.name}?`)) return
    setBusyTunnelId(tunnel.id)
    try {
      await realmsApi.deleteTunnel(tunnel.id)
      pushNotification({
        id: crypto.randomUUID(),
        type: 'deploy_complete',
        title: t('dashboard.realms.notifications.tunnelDeleted'),
        message: tunnel.name,
        read: false,
        createdAt: new Date().toISOString(),
      })
      await loadData()
    } catch (err: any) {
      setError(err?.detail || err?.message || t('common.error'))
    } finally {
      setBusyTunnelId(null)
    }
  }

  async function handleToggleBroker(broker: RealmBroker) {
    setBusyBroker(true)
    try {
      if (broker.status === 'active') {
        await realmsApi.stopBroker(broker.id)
        pushNotification({
          id: crypto.randomUUID(),
          type: 'deploy_complete',
          title: t('dashboard.realms.notifications.brokerStopped'),
          message: `${broker.domainOrIp}:${broker.listenPort}`,
          read: false,
          createdAt: new Date().toISOString(),
        })
      } else {
        await realmsApi.startBroker(broker.id)
        pushNotification({
          id: crypto.randomUUID(),
          type: 'deploy_complete',
          title: t('dashboard.realms.notifications.brokerStarted'),
          message: `${broker.domainOrIp}:${broker.listenPort}`,
          read: false,
          createdAt: new Date().toISOString(),
        })
      }
      await loadData()
    } catch (err: any) {
      setError(err?.detail || err?.message || t('common.error'))
    } finally {
      setBusyBroker(false)
    }
  }

  async function handleDeleteBroker(broker: RealmBroker) {
    if (!window.confirm(`${t('common.delete')} broker (${broker.domainOrIp}:${broker.listenPort})?`)) return
    setBusyBroker(true)
    try {
      await realmsApi.deleteBroker(broker.id)
      pushNotification({
        id: crypto.randomUUID(),
        type: 'deploy_complete',
        title: t('dashboard.realms.notifications.brokerDeleted'),
        message: `${broker.domainOrIp}:${broker.listenPort}`,
        read: false,
        createdAt: new Date().toISOString(),
      })
      await loadData()
    } catch (err: any) {
      setError(err?.detail || err?.message || t('common.error'))
    } finally {
      setBusyBroker(false)
    }
  }

  async function openConnectModal(tunnel: RealmTunnel) {
    setConnectModalTunnel(tunnel)
    setLoadingConfig(true)
    setConnectConfig(null)
    try {
      const cfg = await realmsApi.getTunnelConfig(tunnel.id)
      setConnectConfig(cfg)
    } catch (err: any) {
      setError(err?.detail || err?.message || t('common.error'))
    } finally {
      setLoadingConfig(false)
    }
  }

  function copyToClipboard(text: string, key: string) {
    navigator.clipboard.writeText(text)
    setCopiedKey(key)
    setTimeout(() => setCopiedKey(null), 2000)
  }

  function downloadFile(content: string, filename: string) {
    const blob = new Blob([content], { type: 'text/yaml' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="dashboard-page space-y-6">
      <PageHeader
        title={t('dashboard.realms.title')}
        subtitle={t('dashboard.realms.subtitle')}
      >
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            onClick={openBrokerModal}
            className="border border-white/10 text-xs text-text-secondary hover:text-white"
          >
            <Settings size={14} className="mr-1.5 text-accent-cyan" />
            {activeBroker
              ? t('dashboard.realms.brokerCard.redeployBtn')
              : t('dashboard.realms.brokerCard.deployBtn')}
          </Button>
          <Button
            variant="primary"
            onClick={openCreateModal}
            className="bg-accent-cyan hover:bg-accent-cyan/90 text-black font-semibold text-xs shadow-lg shadow-accent-cyan/20"
          >
            <Plus size={15} className="mr-1.5" />
            {t('dashboard.realms.tunnelsCard.addBtn')}
          </Button>
        </div>
      </PageHeader>

      {error && (
        <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-4 text-sm text-rose-400 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <AlertCircle size={18} className="shrink-0" />
            <span>{error}</span>
          </div>
          <button onClick={() => setError(null)} className="text-rose-400/80 hover:text-rose-300">✕</button>
        </div>
      )}

      {/* Rendezvous Broker Status Banner */}
      <Card className="p-4 bg-zinc-900/60 border-white/10 backdrop-blur-md">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="flex items-start gap-3.5">
            <div className="p-2.5 rounded-xl bg-accent-cyan/10 border border-accent-cyan/20 text-accent-cyan shrink-0 mt-0.5">
              <Radio size={20} />
            </div>
            <div>
              <div className="flex items-center gap-2.5">
                <h3 className="text-sm font-semibold text-white">
                  {t('dashboard.realms.brokerCard.title')}
                </h3>
                {activeBroker ? (
                  activeBroker.status === 'deploying' ? (
                    <button
                      type="button"
                      onClick={() => {
                        const server = servers.find((s) => s.id === activeBroker.serverId)
                        openDeployLog(activeBroker.id, {
                          title: 'Rendezvous Broker',
                          subtitle: `${activeBroker.domainOrIp || server?.host || 'server'}:${activeBroker.listenPort}`,
                        })
                      }}
                      className="cursor-pointer hover:opacity-85 transition-opacity"
                      title={t('deployLog.open')}
                    >
                      <Badge variant="warning" className="text-[11px]">
                        <span className="flex items-center gap-1">
                          <Loader2 size={11} className="animate-spin" />
                          {t('common.deploying')}
                        </span>
                      </Badge>
                    </button>
                  ) : (
                    <Badge
                      variant={activeBroker.status === 'active' ? 'success' : 'default'}
                      className="text-[11px]"
                    >
                      ● {t('dashboard.realms.brokerCard.selfHostedBroker')} (
                      {activeBroker.status === 'active'
                        ? t('dashboard.realms.brokerCard.statusActive')
                        : t('dashboard.realms.brokerCard.statusInactive')}
                      )
                    </Badge>
                  )
                ) : (
                  <Badge variant="default" className="bg-white/5 text-zinc-300 border-white/10 text-[11px]">
                    ● {t('dashboard.realms.brokerCard.publicBroker')} (realm.hy2.io)
                  </Badge>
                )}
              </div>
              <p className="text-xs text-text-muted mt-1 leading-relaxed">
                {activeBroker ? (
                  <>
                    {t('dashboard.realms.brokerCard.selfHostedBrokerDesc')}{' '}
                    <code className="text-accent-cyan font-mono font-medium">
                      {activeBroker.domainOrIp}:{activeBroker.listenPort}
                    </code>
                  </>
                ) : (
                  t('dashboard.realms.brokerCard.publicBrokerDesc')
                )}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0 self-end md:self-center">
            {activeBroker && (
              <>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busyBroker || activeBroker.status === 'deploying'}
                  onClick={() => handleToggleBroker(activeBroker)}
                  className="text-xs border border-white/10 text-zinc-300"
                >
                  {activeBroker.status === 'active' ? (
                    <>
                      <Square size={12} className="mr-1 text-amber-400" />
                      {t('dashboard.realms.brokerCard.stopBtn')}
                    </>
                  ) : (
                    <>
                      <Play size={12} className="mr-1 text-emerald-400" />
                      {t('dashboard.realms.brokerCard.startBtn')}
                    </>
                  )}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busyBroker || activeBroker.status === 'deploying'}
                  onClick={() => handleDeleteBroker(activeBroker)}
                  className="text-xs text-rose-400 hover:text-rose-300 hover:bg-rose-500/10"
                  title={t('dashboard.realms.brokerCard.deleteBtn')}
                >
                  <Trash2 size={13} />
                </Button>
              </>
            )}
            <Button
              size="sm"
              variant="ghost"
              onClick={loadData}
              disabled={loading}
              className="text-xs border border-white/10 text-zinc-400 hover:text-white"
            >
              <RefreshCw size={13} className={cn(loading && 'animate-spin')} />
            </Button>
          </div>
        </div>
      </Card>

      {/* Tunnels List */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-white flex items-center gap-2">
            <span>{t('dashboard.realms.tunnelsCard.title')}</span>
            <span className="text-xs font-normal text-text-muted">({tunnels.length})</span>
          </h2>
        </div>

        {loading && tunnels.length === 0 ? (
          <div className="py-16 text-center text-sm text-text-muted">
            <RefreshCw size={24} className="mx-auto animate-spin text-accent-cyan/80 mb-3" />
            {t('common.waiting')}…
          </div>
        ) : tunnels.length === 0 ? (
          <Card className="py-12 px-6 text-center border-dashed border-white/10 bg-zinc-900/30">
            <div className="w-12 h-12 rounded-2xl bg-white/5 border border-white/10 flex items-center justify-center mx-auto mb-4 text-accent-cyan">
              <Radio size={24} />
            </div>
            <h3 className="text-sm font-semibold text-white">
              {t('dashboard.realms.tunnelsCard.noTunnels')}
            </h3>
            <p className="text-xs text-text-muted max-w-md mx-auto mt-1.5 leading-relaxed">
              {t('dashboard.realms.tunnelsCard.noTunnelsHint')}
            </p>
            <div className="mt-5">
              <Button onClick={openCreateModal} variant="primary" className="text-xs font-semibold">
                <Plus size={14} className="mr-1.5" />
                {t('dashboard.realms.tunnelsCard.addBtn')}
              </Button>
            </div>
          </Card>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {tunnels.map((tunnel) => {
              const server = servers.find((s) => s.id === tunnel.serverId)
              const isBusy = busyTunnelId === tunnel.id
              const isDeploying = tunnel.status === 'deploying'
              const isRunning = tunnel.status === 'active'

              return (
                <Card
                  key={tunnel.id}
                  className={cn(
                    "p-5 border-white/10 bg-zinc-900/60 backdrop-blur-md flex flex-col justify-between transition-all hover:border-white/20",
                    isDeploying && "border-amber-500/20 bg-amber-500/5",
                  )}
                >
                  <div>
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="flex items-center gap-2">
                          <h3 className="text-base font-semibold text-white">{tunnel.name}</h3>
                          {isDeploying ? (
                            <button
                              type="button"
                              onClick={() => {
                                openDeployLog(tunnel.id, {
                                  title: 'Hysteria Realm',
                                  subtitle: tunnel.name,
                                })
                              }}
                              className="cursor-pointer hover:opacity-85 transition-opacity"
                              title={t('deployLog.open')}
                            >
                              <Badge variant="warning" className="text-[10px] px-1.5 py-0.5">
                                <span className="flex items-center gap-1">
                                  <Loader2 size={10} className="animate-spin" />
                                  {t('common.deploying')}
                                </span>
                              </Badge>
                            </button>
                          ) : (
                            <Badge
                              variant={isRunning ? 'success' : 'default'}
                              className="text-[10px] px-1.5 py-0.5"
                            >
                              {isRunning
                                ? `● ${t('dashboard.realms.tunnelsCard.statusActive')}`
                                : `○ ${t('dashboard.realms.tunnelsCard.statusInactive')}`}
                            </Badge>
                          )}
                        </div>
                        <div className="flex items-center gap-2 mt-1 text-xs text-text-muted">
                          <Server size={12} className="text-accent-cyan" />
                          <span>{server?.name || tunnel.serverId} ({server?.host || 'local'})</span>
                        </div>
                      </div>

                      <div className="flex items-center gap-1.5">
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={isBusy || isDeploying}
                          onClick={() => handleToggleTunnel(tunnel)}
                          className="h-8 px-2.5 text-xs border border-white/10 text-zinc-300 hover:text-white"
                          title={
                            isRunning
                              ? t('dashboard.realms.tunnelsCard.btnStop')
                              : t('dashboard.realms.tunnelsCard.btnStart')
                          }
                        >
                          {isRunning ? (
                            <Square size={12} className="text-amber-400" />
                          ) : (
                            <Play size={12} className="text-emerald-400" />
                          )}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={isBusy || isDeploying}
                          onClick={() => handleDeleteTunnel(tunnel)}
                          className="h-8 w-8 p-0 text-rose-400/80 hover:text-rose-300 hover:bg-rose-500/10"
                          title={t('dashboard.realms.tunnelsCard.btnDelete')}
                        >
                          <Trash2 size={13} />
                        </Button>
                      </div>
                    </div>

                    <div className="mt-4 p-3 rounded-xl bg-black/40 border border-white/5 space-y-1.5 text-xs">
                      <div className="flex items-center justify-between text-text-muted">
                        <span>{t('dashboard.realms.createModal.realmNameLabel')}:</span>
                        <div className="flex items-center gap-1.5">
                          <code className="text-zinc-300 font-mono text-[11px]">{tunnel.realmName}</code>
                          <button
                            onClick={() => copyToClipboard(tunnel.realmName, `realm-${tunnel.id}`)}
                            className="text-text-muted hover:text-white transition-colors"
                          >
                            {copiedKey === `realm-${tunnel.id}` ? (
                              <Check size={12} className="text-emerald-400" />
                            ) : (
                              <Copy size={12} />
                            )}
                          </button>
                        </div>
                      </div>
                      <div className="flex items-center justify-between text-text-muted">
                        <span>{t('dashboard.realms.tunnelsCard.thBroker')}:</span>
                        <span className="text-accent-cyan text-[11px] truncate max-w-[200px]" title={tunnel.brokerUrl}>
                          {tunnel.brokerType === 'public'
                            ? 'realm.hy2.io'
                            : tunnel.brokerType === 'self_hosted'
                              ? t('dashboard.realms.brokerCard.selfHostedBroker')
                              : 'Custom'}
                        </span>
                      </div>
                    </div>
                  </div>

                  <div className="mt-5 pt-3 border-t border-white/5 flex items-center justify-between">
                    <span className="text-[11px] text-text-muted">
                      Hysteria 2 • UDP Hole Punching
                    </span>
                    <Button
                      size="sm"
                      variant="primary"
                      disabled={!isRunning}
                      onClick={() => openConnectModal(tunnel)}
                      className="bg-accent-cyan hover:bg-accent-cyan/90 text-black font-semibold text-xs h-8 px-3 disabled:opacity-50"
                    >
                      <QrCode size={13} className="mr-1.5" />
                      {t('dashboard.realms.tunnelsCard.btnConnect')}
                    </Button>
                  </div>
                </Card>
              )
            })}
          </div>
        )}
      </div>

      {/* MODAL: Connect Client */}
      {connectModalTunnel && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-150">
          <div className="w-full max-w-lg rounded-2xl bg-zinc-950 border border-white/10 p-6 shadow-2xl relative">
            <div className="flex items-center justify-between pb-4 border-b border-white/10">
              <div>
                <h3 className="text-base font-semibold text-white">
                  {t('dashboard.realms.connectModal.title')} · {connectModalTunnel.name}
                </h3>
                <p className="text-xs text-text-muted mt-0.5">
                  {t('dashboard.realms.connectModal.subtitle')}
                </p>
              </div>
              <button
                onClick={() => setConnectModalTunnel(null)}
                className="text-text-muted hover:text-white p-1 rounded-lg"
              >
                ✕
              </button>
            </div>

            {loadingConfig ? (
              <div className="py-12 text-center text-sm text-text-muted">
                <RefreshCw size={20} className="animate-spin mx-auto text-accent-cyan mb-2" />
                {t('common.waiting')}…
              </div>
            ) : connectConfig ? (
              <div className="mt-5 space-y-4">
                {/* QR Code */}
                <div className="flex flex-col items-center justify-center p-4 bg-white rounded-xl mx-auto w-fit">
                  <QrImage data={connectConfig.shareUri} size={180} />
                  <span className="text-[10px] text-zinc-600 font-mono mt-1 font-semibold">
                    Hysteria 2 Realm Profile
                  </span>
                </div>

                {/* Share Link */}
                <div>
                  <label className="text-xs font-medium text-text-muted mb-1 block">
                    {t('dashboard.realms.connectModal.tabQr')} (v2rayNG / Matsuri / sing-box / Nekobox)
                  </label>
                  <div className="flex items-center gap-2">
                    <input
                      readOnly
                      value={connectConfig.shareUri}
                      className="flex-1 bg-black/50 border border-white/10 rounded-lg px-3 py-2 text-xs font-mono text-zinc-300 focus:outline-none"
                    />
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => copyToClipboard(connectConfig.shareUri, 'share-uri')}
                      className="border border-white/10 text-xs shrink-0"
                    >
                      {copiedKey === 'share-uri' ? (
                        <Check size={14} className="text-emerald-400" />
                      ) : (
                        <Copy size={14} />
                      )}
                    </Button>
                  </div>
                </div>

                {/* Config Download & YAML */}
                <div className="flex items-center justify-between pt-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => copyToClipboard(connectConfig.yaml, 'yaml-config')}
                    className="text-xs border border-white/10 text-text-muted hover:text-white"
                  >
                    {copiedKey === 'yaml-config' ? (
                      <>
                        <Check size={13} className="mr-1 text-emerald-400" />
                        {t('dashboard.realms.connectModal.copied')}
                      </>
                    ) : (
                      <>
                        <Copy size={13} className="mr-1" />
                        {t('dashboard.realms.connectModal.copyConfig')}
                      </>
                    )}
                  </Button>
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={() =>
                      downloadFile(connectConfig.yaml, `hysteria2-realm-${connectModalTunnel.realmName}.yaml`)
                    }
                    className="text-xs font-semibold bg-accent-cyan hover:bg-accent-cyan/90 text-black"
                  >
                    <Download size={13} className="mr-1.5" />
                    {t('dashboard.realms.connectModal.downloadConfig')}
                  </Button>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      )}

      {/* MODAL: Create Realm Tunnel */}
      {createTunnelOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-150">
          <form
            onSubmit={handleCreateTunnel}
            className="w-full max-w-lg rounded-2xl bg-zinc-950 border border-white/10 p-6 shadow-2xl space-y-4"
          >
            <div className="flex items-center justify-between pb-3 border-b border-white/10">
              <h3 className="text-base font-semibold text-white">
                {t('dashboard.realms.createModal.title')}
              </h3>
              <button
                type="button"
                onClick={() => setCreateTunnelOpen(false)}
                className="text-text-muted hover:text-white"
              >
                ✕
              </button>
            </div>

            <div>
              <label className="text-xs font-medium text-text-muted block mb-1.5">
                {t('dashboard.realms.createModal.serverLabel')}
              </label>
              <select
                value={tunnelServerId}
                onChange={(e) => setTunnelServerId(e.target.value)}
                className="w-full bg-black/50 border border-white/10 rounded-xl px-3 py-2.5 text-xs text-white focus:outline-none focus:border-accent-cyan"
                required
              >
                {servers.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} ({s.host})
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="text-xs font-medium text-text-muted block mb-1.5">
                {t('dashboard.realms.createModal.nameLabel')}
              </label>
              <input
                type="text"
                value={tunnelName}
                onChange={(e) => setTunnelName(e.target.value)}
                placeholder={t('dashboard.realms.createModal.namePlaceholder')}
                className="w-full bg-black/50 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-accent-cyan"
                required
              />
            </div>

            <div>
              <label className="text-xs font-medium text-text-muted block mb-1.5">
                {t('dashboard.realms.createModal.realmNameLabel')}
              </label>
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={tunnelRealmName}
                  onChange={(e) => setTunnelRealmName(e.target.value)}
                  placeholder={t('dashboard.realms.createModal.realmNamePlaceholder')}
                  className="flex-1 bg-black/50 border border-white/10 rounded-xl px-3 py-2 text-xs font-mono text-zinc-300 focus:outline-none focus:border-accent-cyan"
                  required
                />
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setTunnelRealmName(generateRandomName())}
                  className="border border-white/10 text-xs text-text-muted hover:text-white shrink-0"
                >
                  <RefreshCw size={12} className="mr-1" />
                  {t('awg.generate')}
                </Button>
              </div>
              <p className="text-[11px] text-text-muted mt-1">
                {t('dashboard.realms.createModal.realmNameHint')}
              </p>
            </div>

            <div>
              <label className="text-xs font-medium text-text-muted block mb-1.5">
                {t('dashboard.realms.createModal.brokerTypeLabel')}
              </label>
              <div className="grid grid-cols-3 gap-2">
                <button
                  type="button"
                  onClick={() => setTunnelBrokerType('public')}
                  className={cn(
                    'p-2.5 rounded-xl border text-left transition-all text-xs',
                    tunnelBrokerType === 'public'
                      ? 'border-accent-cyan/50 bg-accent-cyan/10 text-white'
                      : 'border-white/10 bg-black/30 text-text-muted hover:border-white/20'
                  )}
                >
                  <div className="font-semibold text-white">
                    {t('dashboard.realms.brokerCard.publicBroker')}
                  </div>
                  <div className="text-[10px] text-text-muted mt-0.5 truncate">realm.hy2.io</div>
                </button>

                <button
                  type="button"
                  onClick={() => setTunnelBrokerType('self_hosted')}
                  className={cn(
                    'p-2.5 rounded-xl border text-left transition-all text-xs',
                    tunnelBrokerType === 'self_hosted'
                      ? 'border-accent-cyan/50 bg-accent-cyan/10 text-white'
                      : 'border-white/10 bg-black/30 text-text-muted hover:border-white/20'
                  )}
                >
                  <div className="font-semibold text-white">
                    {t('dashboard.realms.brokerCard.selfHostedBroker')}
                  </div>
                  <div className="text-[10px] text-text-muted mt-0.5 truncate">
                    {activeBroker
                      ? `${activeBroker.domainOrIp}:${activeBroker.listenPort}`
                      : t('dashboard.realms.brokerCard.notConfigured')}
                  </div>
                </button>

                <button
                  type="button"
                  onClick={() => setTunnelBrokerType('custom')}
                  className={cn(
                    'p-2.5 rounded-xl border text-left transition-all text-xs',
                    tunnelBrokerType === 'custom'
                      ? 'border-accent-cyan/50 bg-accent-cyan/10 text-white'
                      : 'border-white/10 bg-black/30 text-text-muted hover:border-white/20'
                  )}
                >
                  <div className="font-semibold text-white">
                    {t('dashboard.realms.brokerCard.customBroker')}
                  </div>
                  <div className="text-[10px] text-text-muted mt-0.5 truncate">URL</div>
                </button>
              </div>

              {tunnelBrokerType === 'custom' && (
                <div className="mt-2.5">
                  <label className="text-[11px] text-text-muted block mb-1">
                    {t('dashboard.realms.createModal.customUrlLabel')}:
                  </label>
                  <input
                    type="text"
                    value={customBrokerUrl}
                    onChange={(e) => setCustomBrokerUrl(e.target.value)}
                    placeholder={t('dashboard.realms.createModal.customUrlPlaceholder')}
                    className="w-full bg-black/50 border border-white/10 rounded-xl px-3 py-2 text-xs font-mono text-white focus:outline-none focus:border-accent-cyan"
                    required
                  />
                </div>
              )}
            </div>

            <div>
              <label className="text-xs font-medium text-text-muted block mb-1.5">
                {t('dashboard.realms.createModal.authPasswordLabel')}
              </label>
              <input
                type="text"
                value={tunnelAuthPassword}
                onChange={(e) => setTunnelAuthPassword(e.target.value)}
                placeholder={t('dashboard.realms.createModal.authPasswordPlaceholder')}
                className="w-full bg-black/50 border border-white/10 rounded-xl px-3 py-2 text-xs font-mono text-zinc-300 focus:outline-none focus:border-accent-cyan"
              />
            </div>

            <div className="pt-3 border-t border-white/10 flex items-center justify-end gap-3">
              <Button
                type="button"
                variant="ghost"
                onClick={() => setCreateTunnelOpen(false)}
                className="text-xs text-text-muted hover:text-white"
              >
                {t('common.cancel')}
              </Button>
              <Button
                type="submit"
                variant="primary"
                disabled={creatingTunnel || (tunnelBrokerType === 'self_hosted' && !activeBroker)}
                className="bg-accent-cyan hover:bg-accent-cyan/90 text-black font-semibold text-xs"
              >
                {creatingTunnel
                  ? t('dashboard.realms.createModal.submitting')
                  : t('dashboard.realms.createModal.submitBtn')}
              </Button>
            </div>
          </form>
        </div>
      )}

      {/* MODAL: Deploy Rendezvous Broker */}
      {deployBrokerOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-150">
          <form
            onSubmit={handleDeployBroker}
            className="w-full max-w-lg rounded-2xl bg-zinc-950 border border-white/10 p-6 shadow-2xl space-y-4"
          >
            <div className="flex items-center justify-between pb-3 border-b border-white/10">
              <h3 className="text-base font-semibold text-white">
                {t('dashboard.realms.deployBrokerModal.title')}
              </h3>
              <button
                type="button"
                onClick={() => setDeployBrokerOpen(false)}
                className="text-text-muted hover:text-white"
              >
                ✕
              </button>
            </div>

            <div className="p-3 rounded-xl bg-accent-cyan/5 border border-accent-cyan/15 text-xs text-text-muted leading-relaxed space-y-1">
              <div className="font-semibold text-accent-cyan">
                {t('dashboard.realms.deployBrokerModal.infoTitle')}
              </div>
              <p>{t('dashboard.realms.deployBrokerModal.infoP1')}</p>
              <p>{t('dashboard.realms.deployBrokerModal.infoP2')}</p>
            </div>

            <div>
              <label className="text-xs font-medium text-text-muted block mb-1.5">
                {t('dashboard.realms.deployBrokerModal.serverLabel')}
              </label>
              <select
                value={brokerServerId}
                onChange={(e) => setBrokerServerId(e.target.value)}
                className="w-full bg-black/50 border border-white/10 rounded-xl px-3 py-2.5 text-xs text-white focus:outline-none focus:border-accent-cyan"
                required
              >
                {servers.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} ({s.host})
                  </option>
                ))}
              </select>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-text-muted block mb-1.5">
                  {t('dashboard.realms.deployBrokerModal.portLabel')}
                </label>
                <input
                  type="number"
                  value={brokerPort}
                  onChange={(e) => setBrokerPort(Number(e.target.value))}
                  className="w-full bg-black/50 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-accent-cyan"
                  required
                />
              </div>

              <div>
                <label className="text-xs font-medium text-text-muted block mb-1.5">
                  {t('dashboard.realms.deployBrokerModal.domainOrIpLabel')}
                </label>
                <input
                  type="text"
                  value={brokerDomainOrIp}
                  onChange={(e) => setBrokerDomainOrIp(e.target.value)}
                  placeholder={t('dashboard.realms.deployBrokerModal.domainOrIpPlaceholder')}
                  className="w-full bg-black/50 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-accent-cyan"
                />
              </div>
            </div>

            <div>
              <label className="text-xs font-medium text-text-muted block mb-1.5">
                {t('dashboard.realms.deployBrokerModal.tokenLabel')}
              </label>
              <input
                type="text"
                value={brokerToken}
                onChange={(e) => setBrokerToken(e.target.value)}
                placeholder={t('dashboard.realms.deployBrokerModal.tokenPlaceholder')}
                className="w-full bg-black/50 border border-white/10 rounded-xl px-3 py-2 text-xs font-mono text-zinc-300 focus:outline-none focus:border-accent-cyan"
              />
            </div>

            <div className="pt-3 border-t border-white/10 flex items-center justify-end gap-3">
              <Button
                type="button"
                variant="ghost"
                onClick={() => setDeployBrokerOpen(false)}
                className="text-xs text-text-muted hover:text-white"
              >
                {t('common.cancel')}
              </Button>
              <Button
                type="submit"
                variant="primary"
                disabled={deployingBroker}
                className="bg-accent-cyan hover:bg-accent-cyan/90 text-black font-semibold text-xs"
              >
                {deployingBroker
                  ? t('dashboard.realms.deployBrokerModal.submitting')
                  : t('dashboard.realms.deployBrokerModal.submitBtn')}
              </Button>
            </div>
          </form>
        </div>
      )}
    </div>
  )
}
