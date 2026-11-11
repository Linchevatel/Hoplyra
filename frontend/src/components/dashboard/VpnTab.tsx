import { ConfigStatusBadge } from './ClientConfigCard'
import { AppIcon } from '@/components/ui/AppIcon'
import { PageHeader } from '@/components/ui/PageHeader'
import { AwgAdvanced } from './AwgAdvanced'
import { validateAwg, versionParams, type AwgParams } from '@/lib/awg'
import { useState } from 'react'
import { Loader2, Pause, Play, Trash2 } from 'lucide-react'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { useDashboard } from '@/lib/dashboard-context'
import { PROTOCOLS, VPN_PROTOCOLS } from '@/lib/constants'
import type { Protocol, OpenVpnTransport } from '@/lib/types'
import { cn } from '@/lib/utils'
import { getLocationFlag, getLocationCity, countryCodeFromLocation } from '@/lib/country-utils'
import { ProtocolIcon } from '@/components/ui/ProtocolIcon'
import { ProtocolSelectCard } from '@/components/dashboard/ProtocolComparisonTable'
import { Checkbox } from '@/components/ui/Checkbox'
import { ClientConfigModal } from '@/components/dashboard/ClientConfigModal'
import { ClientConfigButton } from '@/components/dashboard/ClientConfigButton'
import { AnimatedRemoveWrapper } from '@/components/dashboard/AnimatedRemoveWrapper'
import { useI18n } from '@/i18n/i18n-context'
import { resolveSelectedServer } from '@/lib/selection'
import { getChainLockedServerIds, isServerLockedByActiveChain } from '@/lib/chain-utils'

export function VpnTab() {
  const { t } = useI18n()
  const { servers, configs, deployProtocol, stopConfig, restartConfig, deletingConfigIds, updateConfig, removeConfig, removingConfigIds } =
    useDashboard()
  const [selectedServer, setSelectedServer] = useState('')
  const [selectedProtocol, setSelectedProtocol] = useState<Protocol | null>(null)
  const [openVpnTransport, setOpenVpnTransport] = useState<OpenVpnTransport>('udp')
  const [xrayBypass, setXrayBypass] = useState(false)
  const [awgVersion, setAwgVersion] = useState<'awg' | 'awg1.5' | 'awg2.0' | 'awg3.1'>('awg3.1')
  const [awgMode, setAwgMode] = useState<'none' | 'auto' | 'custom'>('none')
  const [awgParams, setAwgParams] = useState<AwgParams>()
  const [localDeploying, setDeploying] = useState(false)
  const [deployError, setDeployError] = useState<string | null>(null)
  const deploying = localDeploying || configs.some((c) => c.status === 'deploying' && (c.serverId === selectedServer || c.hops?.some((h) => h.serverId === selectedServer)))
  const [configModalId, setConfigModalId] = useState<string | null>(null)

  const onlineServers = servers.filter((s) => s.status === 'online')
  const chainLockedServerIds = getChainLockedServerIds(configs)
  const blockedServers = servers.filter((s) => s.status === 'error' || s.status === 'offline')
  const singleConfigs = configs.filter((c) => !c.hops || c.hops.length < 2)
  const deployedListConfigs = singleConfigs.filter((c) => c.status !== 'deploying')

  const nextSelectedServer = resolveSelectedServer(selectedServer, onlineServers, chainLockedServerIds)
  if (nextSelectedServer !== selectedServer) setSelectedServer(nextSelectedServer)


  const selectedServerLocked = selectedServer
    ? isServerLockedByActiveChain(selectedServer, configs)
    : false

  const activeConfig = configs.find(
    (c) =>
      c.serverId === selectedServer &&
      c.status !== 'inactive' &&
      (!c.hops || c.hops.length < 2),
  )
  const stoppedConfig = configs.find(
    (c) =>
      c.serverId === selectedServer &&
      c.status === 'inactive' &&
      (!c.hops || c.hops.length < 2),
  )
  const configModal = configModalId ? configs.find((c) => c.id === configModalId) : null
  const configModalServer = configModal ? servers.find((s) => s.id === configModal.serverId) : null

  const openVpnLabel = (transport?: OpenVpnTransport) =>
    transport ? `OpenVPN (${transport.toUpperCase()})` : PROTOCOLS.openvpn.label

  const canDeploy = !selectedServerLocked

  async function handleDeploy() {
    if (!selectedServer || !selectedProtocol) return
    if (selectedProtocol === 'awg' && Object.keys(validateAwg(versionParams(awgParams, awgVersion))).length) return
    setDeployError(null)
    setDeploying(true)
    try {
      const deployed = await deployProtocol(selectedServer, selectedProtocol, {
        ...(selectedProtocol === 'openvpn' ? { transport: openVpnTransport } : {}),
        ...(selectedProtocol === 'xray' && xrayBypass ? { xrayBypass: true } : {}),
        ...(selectedProtocol === 'awg' ? { awgVersion, awgMode, ...(versionParams(awgParams, awgVersion) ? { awgParams: versionParams(awgParams, awgVersion) } : {}) } : {}),
      })
      if (deployed?.id) {
        setConfigModalId(deployed.id)
      }
    } catch (err) {
      setDeployError(err instanceof Error ? err.message : t('common.error'))
    } finally {
      setDeploying(false)
    }
  }

  return (
    <div className="dashboard-page">
      {deployError && <p role="alert" className="text-rose-400">{deployError}</p>}
      <PageHeader title={t('dashboard.vpn.title')} subtitle={t('dashboard.vpn.subtitle')} />

      {onlineServers.length === 0 ? (
        <Card className="text-center py-16">
          <AppIcon name="no-servers" size={96} className="mx-auto mb-4" />
          <h3 className="font-semibold text-lg">{t('dashboard.vpn.noServers')}</h3>
          <p className="text-sm text-text-muted mt-2">
            {blockedServers.length > 0
              ? t('dashboard.vpn.sshUnavailable')
              : t('dashboard.vpn.addVpsFirst')}
          </p>
        </Card>
      ) : (
        <>
          <Card>
            <h3 className="font-semibold mb-3">{t('dashboard.vpn.selectServer')}</h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {onlineServers.map((server) => {
                const flag = getLocationFlag(server.location)
                const city = getLocationCity(server.location)
                const countryCode = countryCodeFromLocation(server.location)?.toUpperCase()
                const chainLocked = chainLockedServerIds.has(server.id)

                return (
                  <button
                    key={server.id}
                    onClick={() => !chainLocked && setSelectedServer(server.id)}
                    disabled={chainLocked}
                    title={chainLocked ? t('dashboard.vpn.serverInChain') : undefined}
                    className={cn(
                      'p-4 rounded-xl border text-left transition-all flex items-start gap-3',
                      chainLocked
                        ? 'surface-panel opacity-45 cursor-not-allowed'
                        : 'cursor-pointer',
                      !chainLocked &&
                        (selectedServer === server.id
                          ? 'border-accent-cyan/50 bg-accent-cyan/5'
                          : 'surface-panel glow-border-hover'),
                    )}
                  >
                    <div className="w-10 h-10 rounded-lg surface-panel flex items-center justify-center text-xl leading-none shrink-0">
                      {flag}
                    </div>
                    <div className="min-w-0">
                      <p className="font-medium text-sm truncate">{server.name}</p>
                      <p className="text-xs text-text-muted font-mono mt-0.5 truncate">{server.host}</p>
                      {(countryCode || city) && (
                        <p className="text-xs text-text-muted mt-1 truncate">
                          {[countryCode, city].filter(Boolean).join(' · ')}
                        </p>
                      )}
                      {chainLocked && (
                        <p className="text-xs text-amber-400/90 mt-1">{t('dashboard.vpn.serverInChain')}</p>
                      )}
                    </div>
                  </button>
                )
              })}
            </div>
          </Card>

          <Card>
            <div className="mb-4">
              <h3 className="font-semibold">{t('dashboard.vpn.selectProtocol')}</h3>
              <p className="text-xs text-text-muted mt-1">
                {t('dashboard.vpn.specsNote')}
              </p>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
              {VPN_PROTOCOLS.map((key) => (
                <ProtocolSelectCard
                  key={key}
                  protocol={key}
                  selected={selectedProtocol === key}
                  onSelect={() => setSelectedProtocol(key)}
                />
              ))}
            </div>

              {selectedProtocol === 'awg' && (
                <div className="mt-4 p-4 rounded-xl border border-emerald-500/20 bg-emerald-500/5">
                  <p className="text-sm font-medium mb-1">{t('ui.awgTitle')}</p>
                  <p className="text-xs text-zinc-400 mb-3">
                    {t('ui.awgHint')}
                  </p>
                  <div className="inline-flex flex-wrap rounded-lg border border-white/10 p-0.5 gap-0.5">
                    {([
                      { id: 'awg3.1', label: 'AWG 3.1', desc: t('ui.awg31') },
                      { id: 'awg2.0', label: 'AWG 2.0', desc: t('ui.awg20') },
                      { id: 'awg1.5', label: 'AWG 1.5', desc: t('ui.awg15') },
                      { id: 'awg', label: 'AWG 1.0', desc: t('ui.awg10') },
                    ] as const).map((v) => (
                      <button
                        key={v.id}
                        type="button"
                        onClick={() => { setAwgVersion(v.id); setAwgMode(v.id === 'awg3.1' ? 'none' : 'auto'); setAwgParams(undefined) }}
                        className={cn(
                          'px-3 py-1.5 rounded-md text-xs font-medium transition-colors cursor-pointer',
                          awgVersion === v.id
                            ? 'bg-emerald-500/25 text-emerald-300 border border-emerald-500/40'
                            : 'text-zinc-400 hover:text-zinc-200 hover:bg-white/5',
                        )}
                        title={v.desc}
                      >
                        {v.label}
                      </button>
                    ))}
                  </div>
                  <AwgAdvanced version={awgVersion} mode={awgMode} onModeChange={(mode, params) => { setAwgMode(mode); setAwgParams(params) }} value={awgParams} onChange={setAwgParams} />
                </div>
              )}

              {selectedProtocol === 'xray' && (
                <div className="mt-4 p-4 rounded-xl border border-accent-violet/20 bg-gradient-to-br from-accent-violet/[0.08] to-transparent">
                  <Checkbox
                    checked={xrayBypass}
                    onChange={setXrayBypass}
                    label={t('dashboard.vpn.xrayBypass')}
                    accent="violet"
                  />
                </div>
              )}


            {selectedProtocol === 'openvpn' && (
              <div className="vpn-transport-panel glass mt-4 p-4">
                <p className="text-sm font-medium mb-2">{t('dashboard.vpn.openvpnTransport')}</p>
                <p className="text-xs text-text-muted mb-3">
                  {t('dashboard.vpn.openvpnTransportHint')}
                </p>
                <div className="transport-switch inline-flex flex-wrap p-0.5 gap-0.5">
                  {(['udp', 'tcp'] as const).map((t) => (
                    <button
                      key={t}
                      type="button"
                      onClick={() => setOpenVpnTransport(t)}
                      className={cn(
                        'px-4 py-2 rounded-md text-sm font-medium transition-colors cursor-pointer',
                        openVpnTransport === t
                          ? 'transport-active'
                          : 'text-zinc-400 hover:text-zinc-200',
                      )}
                    >
                      {t.toUpperCase()}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </Card>

          {stoppedConfig && !activeConfig && (
            <AnimatedRemoveWrapper removing={removingConfigIds.has(stoppedConfig.id)}>
            <Card className="border border-zinc-500/25 bg-zinc-500/5">
              <div className="flex flex-col sm:flex-row sm:items-center gap-3">
                <div className="flex items-center gap-3 flex-1 min-w-0">
                  <div className="w-10 h-10 rounded-xl bg-zinc-500/10 flex items-center justify-center shrink-0">
                    <Pause size={18} className="text-zinc-400" />
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{t('dashboard.vpn.vpnStopped')}</p>
                    <p className="text-xs text-text-muted mt-0.5">
                      {stoppedConfig.protocol === 'openvpn'
                        ? openVpnLabel(stoppedConfig.transport)
                        : PROTOCOLS[stoppedConfig.protocol].label}{' '}
                      {t('dashboard.vpn.configSaved')}
                    </p>
                  </div>
                </div>
                <div className="flex gap-2 shrink-0">
                  <ClientConfigButton config={stoppedConfig} onClick={() => setConfigModalId(stoppedConfig.id)} />
                  <Button size="sm" onClick={() => restartConfig(stoppedConfig.id)}>
                    <Play size={14} />
                    {t('common.enable')}
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={deletingConfigIds.has(stoppedConfig.id)}
                    onClick={() => removeConfig(stoppedConfig.id)}
                    title={
                      deletingConfigIds.has(stoppedConfig.id)
                        ? t('dashboard.vpn.deletingContainer')
                        : t('dashboard.vpn.deleteContainer')
                    }
                  >
                    {deletingConfigIds.has(stoppedConfig.id) ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Trash2 size={14} />
                    )}
                  </Button>
                </div>
              </div>
            </Card>
            </AnimatedRemoveWrapper>
          )}

          {activeConfig && activeConfig.status === 'deploying' && (
            <AnimatedRemoveWrapper removing={removingConfigIds.has(activeConfig.id)}>
            <Card className="border-amber-500/20 bg-amber-500/5">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-xl bg-amber-500/10 text-amber-400">
                    <Loader2 size={18} className="animate-spin" />
                  </div>
                  <div>
                    <p className="text-sm font-medium">{t('dashboard.vpn.deployingProgress')}</p>
                    <p className="text-xs text-text-muted mt-0.5">
                      {activeConfig.protocol === 'openvpn'
                        ? openVpnLabel(activeConfig.transport)
                        : (PROTOCOLS[activeConfig.protocol]?.label ?? activeConfig.protocol)}
                    </p>
                  </div>
                  <Badge variant="warning">{t('common.deploying')}</Badge>
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={deletingConfigIds.has(activeConfig.id)}
                    onClick={() => removeConfig(activeConfig.id)}
                    title={
                      deletingConfigIds.has(activeConfig.id)
                        ? t('dashboard.vpn.deletingContainer')
                        : t('dashboard.vpn.deleteContainer')
                    }
                  >
                    {deletingConfigIds.has(activeConfig.id) ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Trash2 size={14} />
                    )}
                  </Button>
                </div>
              </div>
            </Card>
            </AnimatedRemoveWrapper>
          )}

          <div className="flex flex-col items-end gap-2">
            {activeConfig?.status === 'active' && (
              <p className="text-xs text-text-muted text-right max-w-sm">{t('dashboard.vpn.vpnAlreadyActive')}</p>
            )}
            {stoppedConfig && canDeploy && (
              <p className="text-xs text-text-muted text-right max-w-sm">
                {t('dashboard.vpn.changeProtocolHint')}
              </p>
            )}
            {canDeploy && (
            <Button
              onClick={handleDeploy}
              disabled={!selectedServer || !selectedProtocol || deploying}
              size="lg"
            >
              {deploying ? (
                <>
                  <Loader2 size={18} className="animate-spin" />
                  {t('common.deploying')}
                </>
              ) : (
                <>
                  <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-surface-950"><AppIcon name="deploy2" size={18} /></span>
                  {t('dashboard.vpn.deployVpn')}
                </>
              )}
            </Button>
            )}
          </div>

          {deployedListConfigs.length > 0 && (
            <Card>
              <h3 className="font-semibold mb-3">{t('dashboard.vpn.deployedVpns')}</h3>
              <div className="space-y-2">
                {deployedListConfigs.map((config) => {
                  const server = servers.find((s) => s.id === config.serverId)
                  return (
                    <AnimatedRemoveWrapper key={config.id} removing={removingConfigIds.has(config.id)}>
                    <div className="rounded-xl surface-panel overflow-hidden">
                    <div
                      className="flex items-center gap-3 p-2.5"
                    >
                      <ProtocolIcon protocol={config.protocol} size="sm" withGradient />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium truncate">{server?.name ?? '—'}</p>
                        <p className="text-xs text-text-muted">
                          {config.protocol === 'openvpn'
                            ? openVpnLabel(config.transport)
                            : config.protocol === 'awg'
                              ? `AmneziaWG (${(config.awgVersion || 'awg3.1').toUpperCase().replace('AWG', 'AWG ')})`
                              : (PROTOCOLS[config.protocol]?.label ?? config.protocol)}
                        </p>
                      </div>
                      <ConfigStatusBadge status={config.status} liveState={config.liveState} />
                      {config.status === 'active' && (
                        <>
                          <ClientConfigButton config={config} onClick={() => setConfigModalId(config.id)} compact />
                          <Button variant="secondary" size="sm" onClick={() => stopConfig(config.id)}>
                            <Pause size={14} />
                            {t('common.stop')}
                          </Button>
                          <Button
                            variant="secondary"
                            size="sm"
                            disabled={deletingConfigIds.has(config.id)}
                            onClick={() => removeConfig(config.id)}
                            title={
                              deletingConfigIds.has(config.id)
                                ? t('dashboard.vpn.deletingContainer')
                                : t('dashboard.vpn.deleteContainer')
                            }
                          >
                            {deletingConfigIds.has(config.id) ? (
                              <Loader2 size={14} className="animate-spin" />
                            ) : (
                              <Trash2 size={14} />
                            )}
                          </Button>
                        </>
                      )}
                      {config.status === 'inactive' && (
                        <>
                          <ClientConfigButton config={config} onClick={() => setConfigModalId(config.id)} compact />
                          <Button size="sm" onClick={() => restartConfig(config.id)}>
                            <Play size={14} />
                            {t('common.enable')}
                          </Button>
                          <Button
                            variant="secondary"
                            size="sm"
                            disabled={deletingConfigIds.has(config.id)}
                            onClick={() => removeConfig(config.id)}
                            title={
                              deletingConfigIds.has(config.id)
                                ? t('dashboard.vpn.deletingContainer')
                                : t('dashboard.vpn.deleteContainer')
                            }
                          >
                            {deletingConfigIds.has(config.id) ? (
                              <Loader2 size={14} className="animate-spin" />
                            ) : (
                              <Trash2 size={14} />
                            )}
                          </Button>
                        </>
                      )}
                      {config.status === 'error' && (
                        <Button
                          variant="secondary"
                          size="sm"
                          disabled={deletingConfigIds.has(config.id)}
                          onClick={() => removeConfig(config.id)}
                          title={
                            deletingConfigIds.has(config.id)
                              ? t('dashboard.vpn.deletingContainer')
                              : t('dashboard.vpn.deleteContainer')
                          }
                        >
                          {deletingConfigIds.has(config.id) ? (
                            <Loader2 size={14} className="animate-spin" />
                          ) : (
                            <Trash2 size={14} />
                          )}
                        </Button>
                      )}
                    </div>
                    </div>
                    </AnimatedRemoveWrapper>
                  )
                })}
              </div>
            </Card>
          )}
        </>
      )}

      {configModal && configModalServer && (
        <ClientConfigModal
          config={configModal}
          server={configModalServer}
          servers={servers}
          onClose={() => setConfigModalId(null)}
          onConfigUpdated={updateConfig}
        />
      )}
    </div>
  )
}
