import { useState, useEffect } from 'react'
import { Shield, Loader2, Pause, Play, RefreshCw, Trash2 } from 'lucide-react'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { useDashboard } from '@/lib/dashboard'
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
import { useI18n } from '@/i18n/I18nProvider'
import { configUsesAwg } from '@/lib/deploy-history'
import { getChainLockedServerIds, isServerLockedByActiveChain } from '@/lib/chain-utils'

export function VpnTab() {
  const { t } = useI18n()
  const { servers, configs, deployProtocol, stopConfig, restartConfig, upgradeAwgConfig, upgradingAwgIds, deletingConfigIds, updateConfig, removeConfig, removingConfigIds } =
    useDashboard()
  const [selectedServer, setSelectedServer] = useState('')
  const [selectedProtocol, setSelectedProtocol] = useState<Protocol | null>(null)
  const [openVpnTransport, setOpenVpnTransport] = useState<OpenVpnTransport>('udp')
  const [xrayBypass, setXrayBypass] = useState(false)
  const [awgVersion, setAwgVersion] = useState<'awg' | 'awg1.5' | 'awg2.0'>('awg2.0')
  const [deploying, setDeploying] = useState(false)
  const [configModalId, setConfigModalId] = useState<string | null>(null)

  const onlineServers = servers.filter((s) => s.status === 'online')
  const chainLockedServerIds = getChainLockedServerIds(configs)
  const blockedServers = servers.filter((s) => s.status === 'error' || s.status === 'offline')
  const singleConfigs = configs.filter((c) => !c.hops || c.hops.length < 2)
  const deployedListConfigs = singleConfigs.filter((c) => c.status !== 'deploying')

  useEffect(() => {
    if (onlineServers.length === 0) return
    const available = onlineServers.filter((s) => !chainLockedServerIds.has(s.id))
    if (selectedServer && chainLockedServerIds.has(selectedServer)) {
      setSelectedServer(available[0]?.id ?? '')
      return
    }
    if (!selectedServer && available.length > 0) {
      setSelectedServer(available[0].id)
    }
  }, [onlineServers, selectedServer, chainLockedServerIds])

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
    setDeploying(true)
    try {
      const deployed = await deployProtocol(selectedServer, selectedProtocol, {
        ...(selectedProtocol === 'openvpn' ? { transport: openVpnTransport } : {}),
        ...(selectedProtocol === 'xray' && xrayBypass ? { xrayBypass: true } : {}),
        ...(selectedProtocol === 'awg' ? { awgVersion } : {}),
      })
      if (deployed?.id) {
        setConfigModalId(deployed.id)
      }
    } finally {
      setDeploying(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">{t('dashboard.vpn.title')}</h1>
        <p className="text-zinc-400 mt-1">{t('dashboard.vpn.subtitle')}</p>
      </div>

      {onlineServers.length === 0 ? (
        <Card className="text-center py-16">
          <Shield size={40} className="mx-auto text-zinc-600 mb-4" />
          <h3 className="font-semibold text-lg">{t('dashboard.vpn.noServers')}</h3>
          <p className="text-sm text-zinc-500 mt-2">
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
                          ? 'border-cyan-500/50 bg-cyan-500/5'
                          : 'surface-panel glow-border-hover'),
                    )}
                  >
                    <div className="w-10 h-10 rounded-lg surface-panel flex items-center justify-center text-xl leading-none shrink-0">
                      {flag}
                    </div>
                    <div className="min-w-0">
                      <p className="font-medium text-sm truncate">{server.name}</p>
                      <p className="text-xs text-zinc-500 font-mono mt-0.5 truncate">{server.host}</p>
                      {(countryCode || city) && (
                        <p className="text-xs text-zinc-600 mt-1 truncate">
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
              <p className="text-xs text-zinc-500 mt-1">
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
                  <p className="text-sm font-medium mb-1">Версия AmneziaWG (AWG)</p>
                  <p className="text-xs text-zinc-400 mb-3">
                    Выберите версию обфускации для совместимости с вашим клиентом
                  </p>
                  <div className="inline-flex flex-wrap rounded-lg border border-white/10 p-0.5 gap-0.5">
                    {[
                      { id: 'awg2.0', label: 'AWG 2.0 (Стандартный)', desc: 'Стандарт AmneziaWG 2.0 (Рекомендуется)' },
                      { id: 'awg1.5', label: 'AWG 1.5 (Расширенный)', desc: 'Заголовки и размеры пакетов, без I1-пакетов' },
                      { id: 'awg', label: 'AWG 1.0 (Базовый)', desc: 'Совместимость со старыми клиентами и роутерами' },
                    ].map((v) => (
                      <button
                        key={v.id}
                        type="button"
                        onClick={() => setAwgVersion(v.id as any)}
                        className={cn(
                          'px-3 py-1.5 rounded-md text-xs font-medium transition-colors cursor-pointer',
                          awgVersion === v.id
                            ? 'bg-emerald-500/25 text-emerald-200 border border-emerald-500/40'
                            : 'text-zinc-400 hover:text-zinc-200 hover:bg-white/5',
                        )}
                        title={v.desc}
                      >
                        {v.label}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {selectedProtocol === 'xray' && (
                <div className="mt-4 p-4 rounded-xl border border-violet-500/20 bg-gradient-to-br from-violet-500/[0.08] to-transparent">
                  <Checkbox
                    checked={xrayBypass}
                    onChange={setXrayBypass}
                    label={t('dashboard.vpn.xrayBypass')}
                    accent="violet"
                  />
                </div>
              )}


            {selectedProtocol === 'openvpn' && (
              <div className="mt-4 p-4 rounded-xl border border-orange-500/20 bg-orange-500/5">
                <p className="text-sm font-medium mb-2">{t('dashboard.vpn.openvpnTransport')}</p>
                <p className="text-xs text-zinc-500 mb-3">
                  {t('dashboard.vpn.openvpnTransportHint')}
                </p>
                <div className="inline-flex rounded-lg border border-white/10 p-0.5 gap-0.5">
                  {(['udp', 'tcp'] as const).map((t) => (
                    <button
                      key={t}
                      type="button"
                      onClick={() => setOpenVpnTransport(t)}
                      className={cn(
                        'px-4 py-2 rounded-md text-sm font-medium transition-colors cursor-pointer',
                        openVpnTransport === t
                          ? 'bg-orange-500/20 text-orange-200'
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
                    <p className="text-xs text-zinc-500 mt-0.5">
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

          {activeConfig?.status === 'deploying' && (
            <AnimatedRemoveWrapper removing={removingConfigIds.has(activeConfig.id)}>
            <Card className="border border-amber-500/20 bg-amber-500/5">
              <div className="flex items-center gap-3">
                <Loader2 size={20} className="text-amber-400 animate-spin" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium">{t('dashboard.vpn.deployingProgress')}</p>
                  <p className="text-xs text-zinc-500 mt-0.5">
                    {activeConfig.protocol === 'openvpn'
                      ? openVpnLabel(activeConfig.transport)
                      : PROTOCOLS[activeConfig.protocol].label}
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
            </Card>
            </AnimatedRemoveWrapper>
          )}

          <div className="flex flex-col items-end gap-2">
            {activeConfig?.status === 'active' && (
              <p className="text-xs text-zinc-500 text-right max-w-sm">{t('dashboard.vpn.vpnAlreadyActive')}</p>
            )}
            {stoppedConfig && canDeploy && (
              <p className="text-xs text-zinc-500 text-right max-w-sm">
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
                  <Shield size={18} />
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
                        <p className="text-xs text-zinc-500">
                          {config.protocol === 'openvpn'
                            ? openVpnLabel(config.transport)
                            : config.protocol === 'awg'
                              ? (config.awgVersion ? `AmneziaWG (${config.awgVersion.toUpperCase().replace('AWG', 'AWG ')})` : 'AmneziaWG (AWG 3.1)')
                              : PROTOCOLS[config.protocol].label}
                        </p>
                      </div>
                      <Badge
                        variant={
                          config.status === 'active'
                            ? 'success'
                            : config.status === 'deploying'
                              ? 'warning'
                              : 'default'
                        }
                      >
                        {config.status === 'inactive' ? t('common.stop') : config.status}
                      </Badge>
                      {config.status === 'active' && (
                        <>
                          <ClientConfigButton config={config} onClick={() => setConfigModalId(config.id)} compact />
                          {configUsesAwg(config) && (
                            <Button
                              variant="secondary"
                              size="sm"
                              title={t('dashboard.vpn.upgradeAwg')}
                              disabled={upgradingAwgIds.has(config.id)}
                              onClick={() => upgradeAwgConfig(config.id)}
                            >
                              {upgradingAwgIds.has(config.id) ? (
                                <Loader2 size={14} className="animate-spin" />
                              ) : (
                                <RefreshCw size={14} />
                              )}
                            </Button>
                          )}
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
