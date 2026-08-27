import { useState, useEffect, useCallback, type FormEvent } from 'react'
import { Plus, Trash2, Monitor, Shield, GitBranch, Tag, StickyNote, X, RefreshCw, Pencil, ChevronDown } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Badge } from '@/components/ui/Badge'
import { useDashboard } from '@/lib/dashboard'
import { findActiveChainConfig, getHopRole } from '@/lib/chain-utils'
import { PROTOCOLS, SERVER_TAG_SUGGESTIONS } from '@/lib/constants'
import { getLocationFlag, getLocationCity, countryCodeFromLocation } from '@/lib/country-utils'
import { isLocalHost } from '@/lib/geo-utils'
import { ProtocolIcon } from '@/components/ui/ProtocolIcon'
import { ServerPrepareBar } from '@/components/dashboard/ServerPrepareBar'
import type { VpsServer } from '@/lib/types'
import { useI18n } from '@/i18n/I18nProvider'
import { cn, formatLatencyMs, hasLatencyMs } from '@/lib/utils'

export function ServersTab() {
  const { t, pluralServers } = useI18n()
  const { servers, addServer, removeServer } = useDashboard()
  const [showForm, setShowForm] = useState(false)
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set())

  const toggleExpanded = useCallback((serverId: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(serverId)) next.delete(serverId)
      else next.add(serverId)
      return next
    })
  }, [])

  const expandServer = useCallback((serverId: string) => {
    setExpandedIds((prev) => {
      if (prev.has(serverId)) return prev
      const next = new Set(prev)
      next.add(serverId)
      return next
    })
  }, [])

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">{t('dashboard.servers.title')}</h1>
          <p className="text-zinc-400 mt-1">
            {servers.length} {pluralServers(servers.length)}
          </p>
        </div>
        <Button onClick={() => setShowForm(true)} size="sm">
          <Plus size={16} /> {t('dashboard.servers.addServer')}
        </Button>
      </div>

      <AnimatePresence>
        {showForm && (
          <AddServerForm
            onClose={() => setShowForm(false)}
            onAdd={async (data) => {
              await addServer(data)
              setShowForm(false)
            }}
          />
        )}
      </AnimatePresence>

      {servers.length === 0 ? (
        <Card className="text-center py-16">
          <span className="text-4xl mb-4 block">🌐</span>
          <h3 className="font-semibold text-lg">{t('dashboard.servers.noServers')}</h3>
          <p className="text-sm text-zinc-500 mt-2 max-w-sm mx-auto">
            {t('dashboard.servers.noServersDesc')}
          </p>
          <Button className="mt-6" onClick={() => setShowForm(true)}>
            <Plus size={16} /> {t('dashboard.servers.addFirst')}
          </Button>
        </Card>
      ) : (
        <div className="space-y-2">
          {servers.map((server) => (
            <ServerCard
              key={server.id}
              server={server}
              expanded={expandedIds.has(server.id)}
              onToggle={() => toggleExpanded(server.id)}
              onExpand={() => expandServer(server.id)}
              onRemove={() => removeServer(server.id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function ServerActiveConfig({
  server,
  servers,
}: {
  server: VpsServer
  servers: VpsServer[]
}) {
  const { t } = useI18n()
  const { configs } = useDashboard()
  const chainConfig = findActiveChainConfig(server.id, configs)
  const hopRole = chainConfig?.hops ? getHopRole(chainConfig.hops, server.id) : null

  if (chainConfig?.hops) {
    const hop = chainConfig.hops.find((h) => h.serverId === server.id)
    const isEntry = hopRole === 'entry'

    return (
      <div className="mt-3 rounded-xl border border-cyan-500/15 bg-gradient-to-r from-cyan-500/[0.06] to-violet-500/[0.04] px-3 py-2.5">
        <div className="flex items-center gap-2 mb-2">
          <GitBranch size={13} className="text-cyan-400 shrink-0" />
          <span className="text-[11px] font-semibold uppercase tracking-wider text-cyan-400/90">
            {isEntry ? t('dashboard.servers.vpnChain') : hopRole === 'relay' ? t('dashboard.servers.relayNode') : t('dashboard.servers.exitNode')}
          </span>
        </div>
        {isEntry ? (
          <div className="flex flex-wrap items-center gap-1.5">
            {chainConfig.hops.map((h, i) => {
              const hopServer = servers.find((s) => s.id === h.serverId)
              return (
                <span key={h.id} className="flex items-center gap-1.5">
                  {i > 0 && <span className="text-zinc-600 text-xs">→</span>}
                  <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md bg-black/20 border border-white/5">
                    <ProtocolIcon protocol={h.protocol} size="xs" withGradient title={PROTOCOLS[h.protocol].label} />
                    {hopServer && (
                      <span className="text-[11px] text-zinc-400">{hopServer.name.split(' ')[0]}</span>
                    )}
                  </span>
                </span>
              )
            })}
          </div>
        ) : hop ? (
          <div className="flex items-center gap-2">
            <ProtocolIcon protocol={hop.protocol} size="xs" withGradient title={PROTOCOLS[hop.protocol].label} />
            <span className="text-xs text-zinc-500">{t('common.onThisServer')}</span>
          </div>
        ) : null}
      </div>
    )
  }

  if (server.activeProtocol) {
    const p = PROTOCOLS[server.activeProtocol]
    return (
      <div className="mt-3 rounded-xl border border-emerald-500/15 bg-emerald-500/[0.05] px-3 py-2.5">
        <div className="flex items-center gap-2">
          <Shield size={13} className="text-emerald-400 shrink-0" />
          <span className="text-[11px] font-semibold uppercase tracking-wider text-emerald-400/90">
            {t('dashboard.servers.activeVpn')}
          </span>
          <ProtocolIcon protocol={server.activeProtocol} size="xs" withGradient className="ml-auto" title={p.label} />
        </div>
        <p className="text-xs text-zinc-500 mt-1.5">{p.label}</p>
      </div>
    )
  }

  return (
    <div className="mt-3 rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2 text-xs text-zinc-600">
      {t('dashboard.servers.vpnNotConfigured')}
    </div>
  )
}

function ServerCard({
  server,
  expanded,
  onToggle,
  onExpand,
  onRemove,
}: {
  server: VpsServer
  expanded: boolean
  onToggle: () => void
  onExpand: () => void
  onRemove: () => void
}) {
  const { t } = useI18n()
  const { servers, updateServer, pingServerById } = useDashboard()
  const [editingName, setEditingName] = useState(false)
  const [nameDraft, setNameDraft] = useState(server.name)
  const [editingNotes, setEditingNotes] = useState(false)
  const [notesDraft, setNotesDraft] = useState(server.notes ?? '')
  const [pinging, setPinging] = useState(false)
  const flag = getLocationFlag(server.location)
  const city = getLocationCity(server.location)
  const countryCode = countryCodeFromLocation(server.location)?.toUpperCase()
  const tags = server.tags ?? []
  const visibleTags = tags.slice(0, 4)
  const hiddenTagCount = Math.max(0, tags.length - visibleTags.length)

  useEffect(() => {
    if (!editingName) setNameDraft(server.name)
  }, [server.name, editingName])

  useEffect(() => {
    if (server.status === 'connecting') onExpand()
  }, [server.status, server.id, onExpand])

  useEffect(() => {
    if (editingName || editingNotes) onExpand()
  }, [editingName, editingNotes, onExpand])

  function toggleTag(tag: string) {
    const next = tags.includes(tag) ? tags.filter((t) => t !== tag) : [...tags, tag]
    updateServer(server.id, { tags: next })
  }

  function addCustomTag(raw: string) {
    const tag = raw.trim().toLowerCase()
    if (!tag || tags.includes(tag)) return
    updateServer(server.id, { tags: [...tags, tag] })
  }

  function saveName() {
    const next = nameDraft.trim()
    if (!next) {
      setNameDraft(server.name)
      setEditingName(false)
      return
    }
    if (next !== server.name) {
      updateServer(server.id, { name: next })
    }
    setEditingName(false)
  }

  function saveNotes() {
    updateServer(server.id, { notes: notesDraft.trim() || undefined })
    setEditingNotes(false)
  }

  async function handlePing() {
    setPinging(true)
    try {
      await pingServerById(server.id)
    } finally {
      setPinging(false)
    }
  }

  const statusLabels: Record<string, { label: string; variant: 'success' | 'warning' | 'error' | 'default' }> = {
    online: { label: t('status.online'), variant: 'success' },
    offline: { label: t('status.offline'), variant: 'default' },
    connecting: { label: t('status.connectingDots'), variant: 'warning' },
    error: { label: t('common.error'), variant: 'error' },
  }
  const st = statusLabels[server.status]

  return (
    <Card className="p-0 overflow-hidden">
      <button
        type="button"
        onClick={onToggle}
        className="w-full text-left px-3 py-2.5 sm:px-4 hover:bg-white/[0.03] transition-colors cursor-pointer"
        aria-expanded={expanded}
      >
        <div className="flex items-center gap-2 min-w-0">
          <ChevronDown
            size={16}
            className={cn(
              'shrink-0 text-zinc-500 transition-transform duration-200',
              expanded && 'rotate-180',
            )}
          />
          <span
            className={cn(
              'w-2 h-2 rounded-full shrink-0',
              server.status === 'online' && 'bg-emerald-400',
              server.status === 'offline' && 'bg-zinc-500',
              server.status === 'connecting' && 'bg-amber-400 animate-pulse',
              server.status === 'error' && 'bg-red-400',
            )}
          />
          <span className="font-semibold text-sm truncate min-w-0 flex-1">{server.name}</span>
          <Badge variant={st.variant} className="shrink-0">
            {st.label}
          </Badge>
        </div>

        <div className="mt-1.5 pl-6 sm:pl-7 flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0">
          <span className="text-xs text-zinc-500 font-mono truncate max-w-full">{server.host}</span>
          {visibleTags.length > 0 && (
            <span className="flex flex-wrap items-center gap-1 min-w-0">
              {visibleTags.map((tag) => (
                <span
                  key={tag}
                  className="inline-flex px-2 py-0.5 rounded text-xs bg-cyan-500/10 text-cyan-300/90 border border-cyan-500/15 truncate max-w-[96px]"
                >
                  {tag}
                </span>
              ))}
              {hiddenTagCount > 0 && (
                <span className="text-xs text-zinc-600">+{hiddenTagCount}</span>
              )}
            </span>
          )}
        </div>
      </button>

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="border-t border-white/5 px-3 py-3 sm:px-4 sm:py-4 space-y-3">
              <div className="flex items-start gap-3">
                <div className="relative shrink-0">
                  <div className="w-10 h-10 rounded-xl surface-panel flex items-center justify-center text-xl leading-none">
                    {flag}
                  </div>
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      {editingName ? (
                        <div className="space-y-2">
                          <input
                            type="text"
                            value={nameDraft}
                            onChange={(e) => setNameDraft(e.target.value)}
                            maxLength={128}
                            autoFocus
                            className="w-full font-semibold text-sm bg-black/20 border border-white/5 rounded-lg px-2 py-1 text-zinc-100 focus:outline-none focus:border-cyan-500/30"
                            placeholder={t('dashboard.servers.serverNamePlaceholder')}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') saveName()
                              if (e.key === 'Escape') {
                                setNameDraft(server.name)
                                setEditingName(false)
                              }
                            }}
                          />
                          <div className="flex gap-2 justify-end">
                            <button
                              type="button"
                              onClick={() => {
                                setNameDraft(server.name)
                                setEditingName(false)
                              }}
                              className="text-[11px] text-zinc-500 hover:text-zinc-300 cursor-pointer"
                            >
                              {t('common.cancel')}
                            </button>
                            <button
                              type="button"
                              onClick={saveName}
                              className="text-[11px] text-cyan-400 hover:text-cyan-300 cursor-pointer"
                            >
                              {t('common.save')}
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="flex items-center gap-1 min-w-0">
                          <h3 className="font-semibold truncate">{server.name}</h3>
                          <button
                            type="button"
                            onClick={() => {
                              setNameDraft(server.name)
                              setEditingName(true)
                            }}
                            className="p-1 rounded-md text-zinc-600 hover:text-cyan-400 hover:bg-cyan-500/10 transition-colors cursor-pointer shrink-0"
                            aria-label={t('dashboard.servers.renameServer')}
                            title={t('dashboard.servers.renameServer')}
                          >
                            <Pencil size={13} />
                          </button>
                        </div>
                      )}
                      <p className="text-xs text-zinc-500 font-mono mt-0.5">
                        {server.host}
                        {server.port !== 22 ? `:${server.port}` : ''}
                      </p>
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                      <button
                        type="button"
                        onClick={handlePing}
                        disabled={pinging}
                        className="p-1.5 rounded-lg text-zinc-600 hover:text-cyan-400 hover:bg-cyan-500/10 transition-colors cursor-pointer disabled:opacity-50"
                        aria-label={t('dashboard.servers.checkSsh')}
                        title={t('dashboard.servers.checkSshPodman')}
                      >
                        <RefreshCw size={15} className={pinging ? 'animate-spin' : undefined} />
                      </button>
                      <button
                        type="button"
                        onClick={onRemove}
                        className="p-1.5 rounded-lg text-zinc-600 hover:text-red-400 hover:bg-red-500/10 transition-colors cursor-pointer"
                        aria-label={t('dashboard.servers.deleteServer')}
                        title={t('common.delete')}
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>
                  </div>

                  <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-500">
                    {countryCode && city ? (
                      <span className="text-zinc-400">
                        {countryCode} · {city}
                      </span>
                    ) : server.status === 'connecting' ? (
                      <span className="text-zinc-600 italic">{t('common.detectingLocation')}</span>
                    ) : null}
                    {server.os && (
                      <span className="flex items-center gap-1">
                        <Monitor size={11} /> {server.os}
                      </span>
                    )}
                    {hasLatencyMs(server.latencyMs) && (
                      <span className="text-zinc-600">{formatLatencyMs(server.latencyMs)}</span>
                    )}
                    {server.podmanVersion && (
                      <span className="text-zinc-600 truncate max-w-[140px]" title={server.podmanVersion}>
                        {server.podmanVersion.split(' ')[0]}
                      </span>
                    )}
                  </div>

                  {server.statusMessage && server.status !== 'connecting' && (
                    <p className="mt-2 text-xs text-amber-400/90">{server.statusMessage}</p>
                  )}

                  {server.status === 'connecting' && (
                    <ServerPrepareBar
                      progress={
                        server.prepareProgress ?? {
                          serverId: server.id,
                          percent: 5,
                          stage: 'ssh',
                          message: t('dashboard.servers.prepareTitle'),
                          status: 'running',
                        }
                      }
                    />
                  )}

                  <div className="mt-3 space-y-2">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Tag size={14} className="text-zinc-600 shrink-0" />
                      {tags.map((tag) => (
                        <button
                          key={tag}
                          type="button"
                          onClick={() => toggleTag(tag)}
                          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs bg-cyan-500/10 text-cyan-300 border border-cyan-500/20 hover:bg-cyan-500/15 cursor-pointer"
                        >
                          {tag}
                          <X size={12} />
                        </button>
                      ))}
                      {SERVER_TAG_SUGGESTIONS.filter((t) => !tags.includes(t)).map((tag) => (
                        <button
                          key={tag}
                          type="button"
                          onClick={() => toggleTag(tag)}
                          className="px-2.5 py-1 rounded-md text-xs text-zinc-500 border border-white/5 hover:border-white/10 hover:text-zinc-300 cursor-pointer"
                        >
                          + {tag}
                        </button>
                      ))}
                      <input
                        type="text"
                        placeholder={t('common.tagPlaceholder')}
                        className="w-20 px-2.5 py-1 rounded-md text-xs bg-white/[0.03] border border-white/5 text-zinc-400 placeholder:text-zinc-600 focus:outline-none focus:border-cyan-500/30"
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            addCustomTag((e.target as HTMLInputElement).value)
                            ;(e.target as HTMLInputElement).value = ''
                          }
                        }}
                      />
                    </div>

                    <div className="rounded-xl border border-white/5 bg-white/[0.02] px-3 py-2">
                      <div className="flex items-center gap-1.5 mb-1">
                        <StickyNote size={11} className="text-zinc-600" />
                        <span className="text-[10px] font-semibold uppercase tracking-wider text-zinc-600">
                          {t('common.notes')}
                        </span>
                      </div>
                      {editingNotes ? (
                        <div className="space-y-2">
                          <textarea
                            value={notesDraft}
                            onChange={(e) => setNotesDraft(e.target.value)}
                            rows={2}
                            className="w-full text-xs bg-black/20 border border-white/5 rounded-lg px-2 py-1.5 text-zinc-300 resize-none focus:outline-none focus:border-cyan-500/30"
                            placeholder={t('dashboard.servers.notesPlaceholder')}
                          />
                          <div className="flex gap-2 justify-end">
                            <button
                              type="button"
                              onClick={() => {
                                setNotesDraft(server.notes ?? '')
                                setEditingNotes(false)
                              }}
                              className="text-[11px] text-zinc-500 hover:text-zinc-300 cursor-pointer"
                            >
                              {t('common.cancel')}
                            </button>
                            <button
                              type="button"
                              onClick={saveNotes}
                              className="text-[11px] text-cyan-400 hover:text-cyan-300 cursor-pointer"
                            >
                              {t('common.save')}
                            </button>
                          </div>
                        </div>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setEditingNotes(true)}
                          className="text-xs text-left w-full text-zinc-500 hover:text-zinc-300 cursor-pointer"
                        >
                          {server.notes || t('dashboard.servers.addNote')}
                        </button>
                      )}
                    </div>
                  </div>

                  <ServerActiveConfig server={server} servers={servers} />
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </Card>
  )
}

function AddServerForm({
  onClose,
  onAdd,
}: {
  onClose: () => void
  onAdd: (data: Omit<VpsServer, 'id' | 'status' | 'port'> & {
    port?: number
    authSecret: string
  }) => Promise<void>
}) {
  const { t } = useI18n()
  const [name, setName] = useState('')
  const [host, setHost] = useState('')
  const [port, setPort] = useState('22')
  const [username, setUsername] = useState('root')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setFormError(null)
    if (submitting) return
    if (!name.trim() || !host.trim() || !password) {
      setFormError(t('dashboard.servers.fillRequiredFields'))
      return
    }
    if (isLocalHost(host)) {
      setFormError(t('dashboard.notify.localhostBlocked'))
      return
    }
    setSubmitting(true)
    try {
      await onAdd({
        name: name.trim(),
        host: host.trim(),
        port: Number(port) || 22,
        username: username.trim() || 'root',
        authSecret: password,
      })
      onClose()
    } catch (err) {
      setFormError(err instanceof Error ? err.message : t('dashboard.notify.addServerFailed'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.2 }}
      className="relative z-10"
    >
      <Card glow="cyan" className="relative z-10">
        <h3 className="font-semibold mb-4">{t('dashboard.servers.newServer')}</h3>
        <form onSubmit={handleSubmit} className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Input label={t('common.name')} placeholder="Frankfurt Node" value={name} onChange={(e) => setName(e.target.value)} required />
          <Input
            label={t('dashboard.servers.ipAddress')}
            placeholder="185.22.33.44"
            value={host}
            onChange={(e) => setHost(e.target.value)}
            hint={t('dashboard.servers.locationHint')}
            error={formError && isLocalHost(host) ? formError : undefined}
            required
          />
          <Input label={t('dashboard.servers.sshPort')} type="number" min={1} max={65535} value={port} onChange={(e) => setPort(e.target.value)} />
          <Input label={t('dashboard.servers.user')} value={username} onChange={(e) => setUsername(e.target.value)} />

          <div className="sm:col-span-2">
            <Input
              label={t('dashboard.servers.sshPassword')}
              type="password"
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              hint={t('dashboard.servers.passwordHint')}
              required
            />
          </div>

          {formError && !isLocalHost(host) && (
            <p className="sm:col-span-2 text-xs text-red-400">{formError}</p>
          )}

          <div className="sm:col-span-2 flex flex-wrap items-center gap-3 justify-end">
            <Button variant="ghost" type="button" onClick={onClose} disabled={submitting}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? t('dashboard.servers.checkingSsh') : t('dashboard.servers.addAndVerify')}
            </Button>
          </div>
          {submitting && (
            <p className="sm:col-span-2 text-xs text-zinc-500 text-right">{t('dashboard.servers.checkingSshHint')}</p>
          )}
        </form>
      </Card>
    </motion.div>
  )
}
