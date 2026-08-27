import { useState, type ReactNode } from 'react'
import { GitBranch, ArrowRight, Loader2, Plus, BookmarkPlus, Trash2, RotateCcw } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { ChainHopEditor, ChainPreview } from '@/components/dashboard/ChainHopEditor'
import { useDashboard } from '@/lib/dashboard'
import { PROTOCOLS, CHAIN_TEMPLATES, FREE_PROTOCOLS, CHAIN_ENTRY_BLOCKED_PROTOCOLS } from '@/lib/constants'
import type { Protocol, UserChainTemplate } from '@/lib/types'
import type { ChainTemplate } from '@/lib/constants'
import {
  createHop,
  isChainValid,
  formatChainHops,
  formatChainProtocols,
  getChainEndpointHosts,
  getChainValidationError,
  pickDefaultServerForNewHop,
  hopsFromBuiltInTemplate,
  hopsFromUserTemplate,
  getChainLockedServerIds,
} from '@/lib/chain-utils'
import { ProtocolIcon } from '@/components/ui/ProtocolIcon'
import { RouteTestPanel } from '@/components/dashboard/RouteTestPanel'
import { ChainDeployStatusBar } from '@/components/dashboard/ChainDeployStatusBar'
import { ClientConfigModal } from '@/components/dashboard/ClientConfigModal'
import { ClientConfigButton } from '@/components/dashboard/ClientConfigButton'
import { AnimatedRemoveWrapper } from '@/components/dashboard/AnimatedRemoveWrapper'
import { initialHopDeployStatus, normalizeHopDeployStatus } from '@/lib/chain-deploy'
import { resolveChainDeployProgress } from '@/lib/chain-deploy-ui'
import { readPendingChainDeploy } from '@/lib/deploy-recover'
import { cn } from '@/lib/utils'
import { useI18n } from '@/i18n/I18nProvider'

function TemplateHopIcons({ hops, compact }: { hops: Protocol[]; compact?: boolean }) {
  return (
    <span className={cn('inline-flex items-center gap-0.5', compact ? 'flex-nowrap' : 'flex-wrap gap-1')}>
      {hops.map((p, j) => (
        <span key={j} className="flex items-center gap-0.5 shrink-0">
          {j > 0 && <ArrowRight size={8} className="text-zinc-600" />}
          <ProtocolIcon protocol={p} size="xs" withGradient title={PROTOCOLS[p].label} />
        </span>
      ))}
    </span>
  )
}

function TemplateGrid({ children }: { children: ReactNode }) {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(148px,1fr))] gap-2.5">
      {children}
    </div>
  )
}

function BuiltInTemplateButton({
  template,
  onApply,
}: {
  template: ChainTemplate
  onApply: () => void
}) {
  return (
    <button
      type="button"
      onClick={onApply}
      className="flex flex-col gap-2 p-2.5 rounded-lg surface-panel glow-border-hover text-left transition-all cursor-pointer min-w-0"
    >
      <span className="text-xs font-medium text-zinc-200 leading-snug line-clamp-2">{template.name}</span>
      <TemplateHopIcons hops={template.hops} compact />
    </button>
  )
}

function UserTemplateCard({
  template,
  onApply,
  onRemove,
}: {
  template: UserChainTemplate
  onApply: () => void
  onRemove: () => void
}) {
  const { t } = useI18n()

  return (
    <div className="group relative flex flex-col gap-2 p-2.5 pr-8 rounded-lg surface-panel glow-border-hover min-w-0">
      <button
        type="button"
        onClick={onApply}
        className="flex flex-col gap-2 min-w-0 text-left cursor-pointer"
      >
        <span className="text-xs font-medium text-zinc-200 leading-snug line-clamp-2">{template.name}</span>
        <TemplateHopIcons hops={template.hops.map((h) => h.protocol)} compact />
      </button>
      <button
        type="button"
        onClick={onRemove}
        className="absolute top-1.5 right-1.5 p-1 text-zinc-600 hover:text-red-400 opacity-0 group-hover:opacity-100 focus:opacity-100 transition-all cursor-pointer"
        aria-label={t('dashboard.chains.deleteTemplate', { name: template.name })}
      >
        <Trash2 size={12} />
      </button>
    </div>
  )
}

export function ChainsTab() {
  const { t } = useI18n()
  const {
    servers,
    configs,
    chainDraft: hops,
    setChainDraft: setHops,
    userChainTemplates,
    saveUserChainTemplate,
    removeUserChainTemplate,
    deployChain,
    chainDeployUi,
    restartConfig,
    deletingConfigIds,
    updateConfig,
    removeConfig,
    removingConfigIds,
  } = useDashboard()
  const [templateName, setTemplateName] = useState('')
  const [templateSaved, setTemplateSaved] = useState(false)
  const [configModalId, setConfigModalId] = useState<string | null>(null)

  const onlineServers = servers.filter((s) => s.status === 'online')
  const chainConfigs = configs.filter((c) => c.hops && c.hops.length >= 2)
  const deployedChainConfigs = chainConfigs.filter(
    (c) => c.status !== 'deploying' && c.id !== 'pending-chain',
  )
  const chainLockedServerIds = [...getChainLockedServerIds(configs)]
  const deployProgress = resolveChainDeployProgress(
    chainDeployUi,
    configs,
    readPendingChainDeploy(),
  )
  const justDeployedChain = configs.find(
    (c) =>
      c.hops &&
      c.hops.length >= 2 &&
      c.status === 'active' &&
      Date.now() - new Date(c.createdAt).getTime() < 120_000,
  )
  const chainError = getChainValidationError(hops, servers, configs)
  const canSaveTemplate = isChainValid(hops, servers, configs)

  function addHop(protocol: Protocol) {
    if (hops.length >= 4 || onlineServers.length === 0) return
    if (hops.length === 0 && CHAIN_ENTRY_BLOCKED_PROTOCOLS.includes(protocol)) return
    const defaultServer = pickDefaultServerForNewHop(
      hops,
      onlineServers,
      protocol,
      getChainLockedServerIds(configs),
    )
    if (!defaultServer) return
    setHops((prev) => [...prev, createHop(protocol, defaultServer)])
  }

  function applyBuiltInTemplate(template: ChainTemplate) {
    setHops(hopsFromBuiltInTemplate(template, onlineServers, getChainLockedServerIds(configs)))
  }

  function applyUserTemplate(template: UserChainTemplate) {
    setHops(hopsFromUserTemplate(template, onlineServers, getChainLockedServerIds(configs)))
  }

  function handleSaveTemplate() {
    if (!canSaveTemplate) return
    saveUserChainTemplate(templateName, hops)
    setTemplateName('')
    setTemplateSaved(true)
    setTimeout(() => setTemplateSaved(false), 2000)
  }

  const configModal = configModalId
    ? configs.find((c) => c.id === configModalId)
    : null
  const configModalServer = configModal
    ? servers.find((s) => s.id === configModal.serverId)
    : null

  function handleDeploy() {
    if (!isChainValid(hops, servers, configs) || deployProgress) return
    deployChain(hops)
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <GitBranch size={24} className="text-violet-400" />
          {t('dashboard.chains.title')}
        </h1>
        <p className="text-zinc-400 mt-1">
          {t('dashboard.chains.subtitle')}
        </p>
      </div>

      {onlineServers.length === 0 ? (
        <Card className="text-center py-12">
          <p className="text-zinc-400">{t('dashboard.chains.needOnlineVps')}</p>
          <Link to="/servers" className="inline-block mt-4">
            <Button size="sm">{t('dashboard.chains.addServers')}</Button>
          </Link>
        </Card>
      ) : (
        <>
          <Card glow="violet">
            <div
              className={cn(
                'flex flex-col sm:flex-row sm:items-center justify-between gap-3',
                hops.length === 0 ? 'mb-2' : 'mb-4',
              )}
            >
              <div className="min-w-0">
                <h3 className="font-semibold">{t('common.route')}</h3>
                {hops.length === 0 && (
                  <p className="text-xs text-zinc-500 mt-1">
                    {t('dashboard.chains.routeHint')}
                  </p>
                )}
              </div>
              {hops.length >= 2 && (
                <ChainPreview hops={hops} servers={servers} compact />
              )}
            </div>
            {hops.length > 0 && (
              <ChainHopEditor
                hops={hops}
                servers={servers}
                chainLockedServerIds={chainLockedServerIds}
                onChange={setHops}
                onRemove={(hopId) => setHops((prev) => prev.filter((h) => h.id !== hopId))}
              />
            )}

            {hops.length > 0 && (
              <div className="mt-4">
                <RouteTestPanel hops={hops} servers={servers} disabled={!isChainValid(hops, servers, configs)} />
              </div>
            )}

            {deployProgress && (
              <ChainDeployStatusBar
                hops={deployProgress.hops}
                servers={servers}
                hopStatuses={deployProgress.hopStatuses}
                className="mt-4"
              />
            )}

            {justDeployedChain && justDeployedChain.status === 'active' && (
              <div className="mt-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3 rounded-xl border border-emerald-500/20 bg-emerald-500/5">
                <div>
                  <p className="text-sm font-medium text-emerald-300">{t('dashboard.chains.deployed')}</p>
                  <p className="text-xs text-zinc-500 mt-0.5">{t('dashboard.chains.downloadEntry')}</p>
                </div>
                <ClientConfigButton
                  config={justDeployedChain}
                  onClick={() => setConfigModalId(justDeployedChain.id)}
                />
              </div>
            )}

            <div
              className={cn(
                'flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-t border-white/5',
                hops.length === 0 ? 'mt-3 pt-3' : 'mt-5 pt-4',
              )}
            >
              <div className="min-w-0">
                {hops.length >= 2 && (
                  <p className="text-xs text-zinc-500 font-mono truncate">
                    {formatChainHops(hops, servers)}
                  </p>
                )}
                {chainError && (
                  <p className="text-xs text-red-400 mt-1">{chainError}</p>
                )}
              </div>
              <Button
                onClick={handleDeploy}
                disabled={!isChainValid(hops, servers, configs) || !!deployProgress}
                size="lg"
                className="sm:ml-auto shrink-0 w-full sm:w-auto"
              >
                {deployProgress ? (
                  <>
                    <Loader2 size={18} className="animate-spin" />
                    {t('dashboard.chains.deployingTo', {
                      count: new Set(deployProgress.hops.map((h) => h.serverId)).size,
                    })}
                  </>
                ) : (
                  <>
                    <GitBranch size={18} />
                    {t('dashboard.chains.deployChain')}
                  </>
                )}
              </Button>
            </div>
          </Card>

          <Card>
            <h3 className="font-semibold mb-3">{t('dashboard.chains.addHop')}</h3>
            <p className="text-xs text-zinc-500 mb-3">
              {t('dashboard.chains.pickProtocol')}
            </p>
            <div className="flex flex-wrap gap-2">
              {FREE_PROTOCOLS.map((p) => {
                const blockedAsEntry = hops.length === 0 && CHAIN_ENTRY_BLOCKED_PROTOCOLS.includes(p)
                return (
                <button
                  key={p}
                  type="button"
                  onClick={() => addHop(p)}
                  disabled={onlineServers.length === 0 || hops.length >= 4 || blockedAsEntry}
                  title={blockedAsEntry ? t('dashboard.chains.torFirstBlocked') : undefined}
                  className="flex items-center gap-2 px-3 py-2 rounded-xl surface-panel glow-border-hover text-sm transition-all disabled:opacity-40 cursor-pointer disabled:cursor-not-allowed"
                >
                  <Plus size={14} />
                  <ProtocolIcon protocol={p} size="sm" withGradient />
                  <span className="sr-only">{PROTOCOLS[p].label}</span>
                </button>
                )
              })}
            </div>
          </Card>

          <Card>
            <h3 className="font-semibold mb-3">{t('common.templates')}</h3>
            <p className="text-xs text-zinc-500 mb-4">
              {t('dashboard.chains.templatesDesc')}
            </p>

            <p className="text-xs font-medium text-zinc-400 mb-2 uppercase tracking-wider">{t('common.builtIn')}</p>
            <TemplateGrid>
              {CHAIN_TEMPLATES.map((template) => (
                <BuiltInTemplateButton
                  key={template.name}
                  template={template}
                  onApply={() => applyBuiltInTemplate(template)}
                />
              ))}
            </TemplateGrid>

            {userChainTemplates.length > 0 && (
              <>
                <p className="text-xs font-medium text-zinc-400 mb-2 mt-4 uppercase tracking-wider">{t('common.myTemplates')}</p>
                <TemplateGrid>
                  {userChainTemplates.map((template) => (
                    <UserTemplateCard
                      key={template.id}
                      template={template}
                      onApply={() => applyUserTemplate(template)}
                      onRemove={() => removeUserChainTemplate(template.id)}
                    />
                  ))}
                </TemplateGrid>
              </>
            )}

            <div className="pt-4 border-t border-white/5">
              <p className="text-xs font-medium text-zinc-400 mb-3 uppercase tracking-wider">{t('dashboard.chains.saveRoute')}</p>
              <div className="flex flex-col sm:flex-row gap-3">
                <Input
                  label={t('dashboard.chains.templateName')}
                  placeholder={hops.length >= 2 ? formatChainProtocols(hops) : t('dashboard.chains.buildFirst')}
                  value={templateName}
                  onChange={(e) => setTemplateName(e.target.value)}
                  className="flex-1"
                />
                <div className="flex items-end gap-2 sm:pb-0 pb-1">
                  <Button
                    variant="secondary"
                    onClick={handleSaveTemplate}
                    disabled={!canSaveTemplate}
                    className="whitespace-nowrap"
                  >
                    <BookmarkPlus size={16} />
                    {templateSaved ? t('common.saved') : t('dashboard.chains.saveTemplate')}
                  </Button>
                </div>
              </div>
              {!canSaveTemplate && hops.length >= 2 && chainError && (
                <p className="text-xs text-zinc-500 mt-2">{t('dashboard.chains.fixBeforeSave')}</p>
              )}
            </div>
          </Card>

          {deployedChainConfigs.length > 0 && (
            <Card>
              <h3 className="font-semibold mb-4">{t('dashboard.chains.deployedChains')}</h3>
              <div className="space-y-3">
                {deployedChainConfigs.map((config) => (
                  <AnimatedRemoveWrapper key={config.id} removing={removingConfigIds.has(config.id)}>
                  <div
                    className="p-3 rounded-xl surface-panel flex flex-col gap-3"
                  >
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                      <div className="min-w-0 flex flex-col gap-1.5">
                        <ChainPreview hops={config.hops!} servers={servers} />
                        {(() => {
                          const { entry, exit } = getChainEndpointHosts(config.hops!, servers)
                          return (
                            <p className="text-[11px] text-zinc-500 font-mono flex flex-wrap gap-x-3 gap-y-0.5">
                              <span>
                                {t('dashboard.chains.entryIp')}:{' '}
                                <span className="text-zinc-400">{entry}</span>
                              </span>
                              <span>
                                {t('dashboard.chains.exitIp')}:{' '}
                                <span className="text-zinc-400">{exit}</span>
                              </span>
                            </p>
                          )
                        })()}
                      </div>
                      <div className="flex items-center gap-2 shrink-0 flex-wrap">
                        <span
                          className={cn(
                            'text-xs',
                            config.status === 'active' && 'text-emerald-400',
                            config.status === 'error' && 'text-red-400',
                            config.status === 'inactive' && 'text-zinc-500',
                          )}
                        >
                          {config.status === 'inactive'
                            ? t('common.stopped')
                            : config.status === 'error'
                              ? t('common.error')
                              : t('status.active')}
                        </span>
                        {(config.status === 'active' || config.status === 'inactive') && (
                          <>
                            <ClientConfigButton
                              config={config}
                              onClick={() => setConfigModalId(config.id)}
                              compact
                            />
                            <Button
                              variant="secondary"
                              size="sm"
                              disabled={deletingConfigIds.has(config.id)}
                              onClick={() => removeConfig(config.id)}
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
                          <Button variant="secondary" size="sm" onClick={() => restartConfig(config.id)}>
                            <RotateCcw size={14} />
                          </Button>
                        )}
                        {(config.status === 'error') && (
                          <Button
                            variant="secondary"
                            size="sm"
                            disabled={deletingConfigIds.has(config.id)}
                            onClick={() => removeConfig(config.id)}
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
                    {config.status === 'error' && config.hops && (
                      <ChainDeployStatusBar
                        hops={config.hops}
                        servers={servers}
                        hopStatuses={normalizeHopDeployStatus(
                          config.hopDeployStatus ?? initialHopDeployStatus(config.hops.length),
                        )}
                      />
                    )}
                  </div>
                  </AnimatedRemoveWrapper>
                ))}
              </div>
            </Card>
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

        </>
      )}
    </div>
  )
}
