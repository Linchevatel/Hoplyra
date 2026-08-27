import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useRef,
  type ReactNode,
} from 'react'
import { t } from '@/i18n/core'
import type {
  VpsServer,
  VpnConfig,
  Protocol,
  OpenVpnTransport,
  ChainHop,
  UserChainTemplate,
  DeployHistoryEntry,
  AppNotification,
} from './types'
import { lookupIpLocation, isLocalHost } from './geo-utils'
import { stripDemoData, isDemoId } from './server-utils'
import { sanitizeChainDraft, userTemplateFromHops } from './chain-utils'
import { generateClientConfig, configRequiresServerSecrets } from './client-config'
import * as api from './api'
import {
  createNotification,
  getInvolvedServerIds,
  notificationForDeploy,
  notificationForOffline,
  reconcileDeployHistory,
  upsertDeployHistory,
} from './deploy-history'
import { initialHopDeployStatus, normalizeHopDeployStatus } from './chain-deploy'
import {
  createChainDeploySession,
  mergeApiConfigsWithLocalDeploy,
  syncSessionFromConfigs,
  type ChainDeploySession,
} from './chain-deploy-ui'
import { pollServerPrepare } from './server-prepare'
import {
  CONFIG_POLL_MS,
  clearPendingChainDeploy,
  hasDeployingConfigs,
  hasPendingChainDeploy,
  readPendingChainDeploy,
  recoverDeployedChain,
  recoverDeployedConfig,
  savePendingChainDeploy,
  waitForConfigDeploy,
  waitForMatchingChainDeploy,
} from './deploy-recover'
import { generateId } from './utils'

interface DashboardContextValue {
  servers: VpsServer[]
  configs: VpnConfig[]
  chainDraft: ChainHop[]
  userChainTemplates: UserChainTemplate[]
  deployHistory: DeployHistoryEntry[]
  notifications: AppNotification[]
  unreadNotifications: number
  setChainDraft: (hops: ChainHop[] | ((prev: ChainHop[]) => ChainHop[])) => void
  saveUserChainTemplate: (name: string, hops: ChainHop[]) => void
  removeUserChainTemplate: (id: string) => void
  addServer: (
    server: Omit<VpsServer, 'id' | 'status' | 'port'> & {
      port?: number
      authSecret: string
    },
  ) => Promise<void>
  updateServer: (id: string, patch: Partial<Pick<VpsServer, 'tags' | 'notes' | 'name'>>) => void
  removeServer: (id: string) => void
  pingServerById: (id: string) => Promise<void>
  deployProtocol: (
    serverId: string,
    protocol: Protocol,
    options?: { transport?: OpenVpnTransport; xrayBypass?: boolean; awgVersion?: 'awg' | 'awg1.5' | 'awg2.0' },
  ) => Promise<void>
  deployChain: (hops: ChainHop[]) => Promise<void>
  chainDeployUi: ChainDeploySession | null
  stopConfig: (id: string) => void
  restartConfig: (id: string) => void
  upgradeAwgConfig: (id: string) => void
  upgradingAwgIds: ReadonlySet<string>
  enableSocksProxy: (id: string) => void
  disableSocksProxy: (id: string) => void
  socksProxyLoadingIds: ReadonlySet<string>
  deletingConfigIds: ReadonlySet<string>
  removingConfigIds: ReadonlySet<string>
  removeConfig: (id: string) => void
  updateConfig: (config: VpnConfig) => void
  markNotificationRead: (id: string) => void
  markAllNotificationsRead: () => void
  dismissNotification: (id: string) => void
  clearAllNotifications: () => void
}

const DashboardContext = createContext<DashboardContextValue | null>(null)

const SERVERS_KEY = 'hoplyra_servers'
const CONFIGS_KEY = 'hoplyra_configs'
const CHAIN_DRAFT_KEY = 'hoplyra_chain_draft'
const USER_TEMPLATES_KEY = 'hoplyra_user_chain_templates'
const HISTORY_KEY = 'hoplyra_deploy_history'
const NOTIFICATIONS_KEY = 'hoplyra_notifications'

function loadJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

function getActor(): { userId: string; userName: string } {
  return { userId: 'local', userName: t('dashboard.actor.you') }
}

function isDemoServerId(id: string): boolean {
  return isDemoId(id)
}

function loadInitialConfigs(servers: VpsServer[]): VpnConfig[] {
  const configs = loadJson<VpnConfig[]>(CONFIGS_KEY, [])

  return configs.map((c) => {
    let next = c

    if (c.status === 'deploying') {
      const isChain = Boolean(c.hops && c.hops.length >= 2)
      next = {
        ...c,
        status: 'error',
        statusMessage: t('dashboard.notify.deployInterrupted'),
        hopDeployStatus: isChain
          ? (c.hopDeployStatus ?? initialHopDeployStatus(c.hops!.length)).map((status) =>
              status === 'done' ? 'done' : 'error',
            )
          : c.hopDeployStatus,
      }
    }

    if (next.status === 'active' && !next.clientConfig && !configRequiresServerSecrets(next)) {
      const server = servers.find((s) => s.id === next.serverId)
      if (server) {
        next = { ...next, clientConfig: generateClientConfig(next, server) }
      }
    }

    return next
  })
}

function loadInitialState(): { servers: VpsServer[]; configs: VpnConfig[] } {
  const savedServers = loadJson<VpsServer[]>(SERVERS_KEY, [])
  const savedConfigs = loadInitialConfigs(savedServers)
  return stripDemoData(savedServers, savedConfigs)
}

const INITIAL_STATE = loadInitialState()

function clearServerVpnState(serverId: string): (s: VpsServer) => VpsServer {
  return (s) =>
    s.id === serverId
      ? { ...s, activeProtocol: undefined, activeChainHops: undefined, activeChain: undefined }
      : s
}

export function DashboardProvider({ children }: { children: ReactNode }) {
  const [servers, setServers] = useState<VpsServer[]>(INITIAL_STATE.servers)
  const [configs, setConfigs] = useState<VpnConfig[]>(INITIAL_STATE.configs)
  const [chainDraft, setChainDraftState] = useState<ChainHop[]>(() =>
    loadJson<ChainHop[]>(CHAIN_DRAFT_KEY, []),
  )
  const [userChainTemplates, setUserChainTemplates] = useState<UserChainTemplate[]>(() =>
    loadJson<UserChainTemplate[]>(USER_TEMPLATES_KEY, []),
  )
  const [deployHistory, setDeployHistory] = useState<DeployHistoryEntry[]>(() =>
    reconcileDeployHistory(
      loadJson<DeployHistoryEntry[]>(HISTORY_KEY, []),
      INITIAL_STATE.configs,
    ),
  )
  const [notifications, setNotifications] = useState<AppNotification[]>(() =>
    loadJson<AppNotification[]>(NOTIFICATIONS_KEY, []),
  )
  const [apiEnabled, setApiEnabled] = useState(false)
  const [chainDeployUi, setChainDeployUi] = useState<ChainDeploySession | null>(null)
  const [upgradingAwgIds, setUpgradingAwgIds] = useState<Set<string>>(() => new Set())
  const [socksProxyLoadingIds, setSocksProxyLoadingIds] = useState<Set<string>>(() => new Set())
  const [deletingConfigIds, setDeletingConfigIds] = useState<Set<string>>(() => new Set())
  const deletingConfigIdsRef = useRef<Set<string>>(new Set())
  const [removingConfigIds, setRemovingConfigIds] = useState<Set<string>>(() => new Set())
  const removingConfigIdsRef = useRef<Set<string>>(new Set())

  const geoBackfilled = useRef(false)
  const draftSanitized = useRef(false)
  const skipChainDraftPersist = useRef(true)
  const skipTemplatesPersist = useRef(true)
  const skipHistoryPersist = useRef(true)
  const skipNotificationsPersist = useRef(true)
  const skipServersPersist = useRef(true)
  const skipConfigsPersist = useRef(true)
  const serversRef = useRef(servers)
  serversRef.current = servers
  const configsRef = useRef(configs)
  configsRef.current = configs
  const chainDeployUiRef = useRef(chainDeployUi)
  chainDeployUiRef.current = chainDeployUi
  const prevServerStatus = useRef<Record<string, VpsServer['status']>>({})
  const serverStatusInitialized = useRef(false)
  const preparePollingRef = useRef(new Set<string>())

  const pushHistory = useCallback((entry: Omit<DeployHistoryEntry, 'id' | 'createdAt'>) => {
    setDeployHistory((prev) => upsertDeployHistory(prev, entry))
  }, [])

  const pushNotification = useCallback((n: AppNotification) => {
    setNotifications((prev) => [n, ...prev].slice(0, 50))
  }, [])

  const resetChainDeploySession = useCallback(() => {
    clearPendingChainDeploy()
    setChainDeployUi(null)
    chainDeployUiRef.current = null
  }, [])

  const watchServerPrepare = useCallback(
    (serverId: string) => {
      if (preparePollingRef.current.has(serverId)) return
      preparePollingRef.current.add(serverId)
      pollServerPrepare(serverId, (prep) => {
        setServers((prev) =>
          prev.map((s) =>
            s.id === serverId
              ? {
                  ...s,
                  prepareProgress: prep,
                  status: prep.status === 'error' ? 'error' : s.status,
                  statusMessage: prep.status === 'error' ? prep.message : s.statusMessage,
                }
              : s,
          ),
        )
      })
        .then((final) => {
          setServers((prev) =>
            prev.map((s) => (s.id === serverId ? { ...final, prepareProgress: undefined } : s)),
          )
        })
        .catch((err) => {
          pushNotification(
            createNotification({
              type: 'deploy_error',
              title: t('dashboard.notify.addServerFailed'),
              message: err instanceof Error ? err.message : t('dashboard.notify.checkSsh'),
            }),
          )
          setServers((prev) =>
            prev.map((s) =>
              s.id === serverId
                ? {
                    ...s,
                    status: 'error',
                    prepareProgress: undefined,
                    statusMessage: err instanceof Error ? err.message : undefined,
                  }
                : s,
            ),
          )
        })
        .finally(() => {
          preparePollingRef.current.delete(serverId)
        })
    },
    [pushNotification],
  )

  const failLocalOnlyDeploy = useCallback(
    (
      failed: VpnConfig,
      opts: {
        historyType: 'vpn' | 'chain'
        serverId: string
        protocol: Protocol
        hops?: ChainHop[]
        involvedIds?: Set<string>
        historyMessage?: string
      },
    ) => {
      const actor = getActor()
      const errMsg = opts.historyMessage ?? t('dashboard.notify.apiRequiredForDeploy')
      const withError: VpnConfig = { ...failed, status: 'error', statusMessage: errMsg }
      const involved = opts.involvedIds ?? new Set([failed.serverId])
      setConfigs((prev) => [
        ...prev.filter(
          (c) =>
            !involved.has(c.serverId) && !c.hops?.some((h) => involved.has(h.serverId)),
        ),
        withError,
      ])
      setServers((prev) =>
        prev.map((s) =>
          involved.has(s.id)
            ? { ...s, activeProtocol: undefined, activeChainHops: undefined, activeChain: undefined }
            : s,
        ),
      )
      pushNotification(notificationForDeploy(withError, serversRef.current, false))
      pushHistory({
        userId: actor.userId,
        userName: actor.userName,
        type: opts.historyType,
        serverId: opts.serverId,
        protocol: opts.protocol,
        hops: opts.hops,
        status: 'error',
        message: errMsg,
      })
    },
    [pushHistory, pushNotification],
  )

  useEffect(() => {
    skipChainDraftPersist.current = false
    skipTemplatesPersist.current = false
    skipHistoryPersist.current = false
    skipNotificationsPersist.current = false
    skipServersPersist.current = false
    skipConfigsPersist.current = false
  }, [])

  useEffect(() => {
    let cancelled = false
    api.apiHealth().then(async (ok) => {
      if (cancelled || !ok) return
      try {
        const [srv, cfg] = await Promise.all([api.fetchServers(), api.fetchConfigs()])
        if (cancelled) return
        setApiEnabled(true)
        const cleaned = stripDemoData(srv, cfg)
        setServers(cleaned.servers)
        setConfigs(cleaned.configs)
        localStorage.removeItem(SERVERS_KEY)
        localStorage.removeItem(CONFIGS_KEY)
        localStorage.removeItem('hoplyra_demo_seed')
      } catch {
        /* keep local/demo state */
      }
    })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (draftSanitized.current || servers.length === 0) return
    draftSanitized.current = true
    setChainDraftState((prev) => sanitizeChainDraft(prev, servers))
  }, [servers])

  useEffect(() => {
    if (servers.length === 0) return

    if (!serverStatusInitialized.current) {
      prevServerStatus.current = Object.fromEntries(servers.map((s) => [s.id, s.status]))
      serverStatusInitialized.current = true
      return
    }

    servers.forEach((server) => {
      const prev = prevServerStatus.current[server.id]
      if (prev && prev !== 'offline' && server.status === 'offline') {
        pushNotification(notificationForOffline(server))
      }
      prevServerStatus.current[server.id] = server.status
    })
  }, [servers, pushNotification])

  useEffect(() => {
    if (!apiEnabled) return
    servers.filter((s) => s.status === 'connecting').forEach((s) => watchServerPrepare(s.id))
  }, [apiEnabled, servers, watchServerPrepare])

  useEffect(() => {
    if (!apiEnabled) return

    let cancelled = false

    const syncFromApi = async () => {
      if (cancelled) return
      try {
        const [srv, cfg] = await Promise.all([api.fetchServers(), api.fetchConfigs()])
        if (cancelled) return
        const cleaned = stripDemoData(srv, cfg)
        setServers(cleaned.servers)
        setConfigs((prev) =>
          mergeApiConfigsWithLocalDeploy(cleaned.configs, prev, chainDeployUiRef.current),
        )
        setChainDeployUi((session) => {
          if (!session) return null
          const merged = mergeApiConfigsWithLocalDeploy(
            cleaned.configs,
            configsRef.current,
            session,
          )
          return syncSessionFromConfigs(session, merged)
        })
      } catch {
        /* retry on next tick */
      }
    }

    const onVisible = () => {
      if (
        document.visibilityState === 'visible' &&
        (hasDeployingConfigs(configsRef.current) ||
          hasPendingChainDeploy() ||
          chainDeployUiRef.current?.status === 'deploying')
      ) {
        void syncFromApi()
      }
    }

    const loop = async () => {
      while (!cancelled) {
        await syncFromApi()
        const isDeploying =
          hasDeployingConfigs(configsRef.current) ||
          hasPendingChainDeploy() ||
          chainDeployUiRef.current?.status === 'deploying'
        await new Promise((r) => setTimeout(r, isDeploying ? CONFIG_POLL_MS : 3000))
      }
    }

    document.addEventListener('visibilitychange', onVisible)
    void loop()

    return () => {
      cancelled = true
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [apiEnabled])

  useEffect(() => {
    if (!apiEnabled) return
    if (!readPendingChainDeploy()) return
    const deployInFlight =
      chainDeployUi?.status === 'deploying' ||
      configs.some((c) => c.hops && c.hops.length >= 2 && c.status === 'deploying')
    if (!deployInFlight) {
      clearPendingChainDeploy()
    }
  }, [apiEnabled, configs, chainDeployUi])

  useEffect(() => {
    if (!apiEnabled) return
    const pendingHops = readPendingChainDeploy()
    if (!pendingHops) return

    let cancelled = false
    const involvedIds = new Set(pendingHops.map((h) => h.serverId))

    void (async () => {
      try {
        setChainDeployUi(createChainDeploySession(pendingHops))
        const recovered = await waitForMatchingChainDeploy(pendingHops, {
          onUpdate: (cfg) => {
            if (cancelled) return
            setConfigs((prev) => [...prev.filter((c) => c.id !== cfg.id), cfg])
            setChainDeployUi((session) =>
              session
                ? {
                    ...session,
                    status: 'deploying',
                    configId: cfg.id,
                    hopDeployStatus: normalizeHopDeployStatus(
                      cfg.hopDeployStatus ?? session.hopDeployStatus,
                    ),
                    hops: cfg.hops ?? session.hops,
                  }
                : null,
            )
          },
        })
        if (cancelled || !recovered) return
        resetChainDeploySession()
        setChainDraftState([])
        setConfigs((prev) => [
          ...prev.filter(
            (c) =>
              c.id !== recovered.id &&
              !involvedIds.has(c.serverId) &&
              !c.hops?.some((h) => involvedIds.has(h.serverId)),
          ),
          recovered,
        ])
        pushNotification(notificationForDeploy(recovered, serversRef.current, true))
      } catch {
        resetChainDeploySession()
      }
    })()

    return () => {
      cancelled = true
    }
  }, [apiEnabled, pushNotification, resetChainDeploySession])

  useEffect(() => {
    if (skipServersPersist.current || apiEnabled) return
    if (servers.length > 0) localStorage.setItem(SERVERS_KEY, JSON.stringify(servers))
  }, [servers, apiEnabled])

  useEffect(() => {
    if (skipConfigsPersist.current || apiEnabled) return
    localStorage.setItem(CONFIGS_KEY, JSON.stringify(configs))
  }, [configs, apiEnabled])

  useEffect(() => {
    if (geoBackfilled.current || servers.length === 0) return
    geoBackfilled.current = true
    servers.forEach((server) => {
      if (server.location) return
      lookupIpLocation(server.host).then((geo) => {
        if (!geo) return
        setServers((prev) => {
          const current = prev.find((s) => s.id === server.id)
          if (!current || current.location) return prev
          return prev.map((s) => (s.id === server.id ? { ...s, location: geo.location } : s))
        })
      })
    })
  }, [servers])

  useEffect(() => {
    if (skipChainDraftPersist.current) return
    localStorage.setItem(CHAIN_DRAFT_KEY, JSON.stringify(chainDraft))
  }, [chainDraft])

  useEffect(() => {
    if (skipTemplatesPersist.current) return
    localStorage.setItem(USER_TEMPLATES_KEY, JSON.stringify(userChainTemplates))
  }, [userChainTemplates])

  useEffect(() => {
    if (skipHistoryPersist.current) return
    localStorage.setItem(HISTORY_KEY, JSON.stringify(deployHistory))
  }, [deployHistory])

  useEffect(() => {
    setDeployHistory((prev) => reconcileDeployHistory(prev, configs))
  }, [configs])

  useEffect(() => {
    if (skipNotificationsPersist.current) return
    localStorage.setItem(NOTIFICATIONS_KEY, JSON.stringify(notifications))
  }, [notifications])

  const setChainDraft = useCallback(
    (hops: ChainHop[] | ((prev: ChainHop[]) => ChainHop[])) => setChainDraftState(hops),
    [],
  )

  const saveUserChainTemplate = useCallback((name: string, hops: ChainHop[]) => {
    if (hops.length < 2) return
    const template = userTemplateFromHops(name, hops)
    setUserChainTemplates((prev) => {
      const next = [...prev, template]
      localStorage.setItem(USER_TEMPLATES_KEY, JSON.stringify(next))
      return next
    })
  }, [])

  const removeUserChainTemplate = useCallback((id: string) => {
    setUserChainTemplates((prev) => {
      const next = prev.filter((t) => t.id !== id)
      localStorage.setItem(USER_TEMPLATES_KEY, JSON.stringify(next))
      return next
    })
  }, [])

  const updateServer = useCallback(
    (id: string, patch: Partial<Pick<VpsServer, 'tags' | 'notes' | 'name'>>) => {
      setServers((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } : s)))
      if (!apiEnabled) return

      const payload: api.ServerUpdatePayload = {}
      if (patch.name !== undefined) payload.name = patch.name
      if (patch.tags !== undefined) payload.tags = patch.tags
      if (patch.notes !== undefined) payload.notes = patch.notes ?? null
      if (Object.keys(payload).length === 0) return

      void (async () => {
        try {
          const updated = await api.updateServer(id, payload)
          setServers((prev) => prev.map((s) => (s.id === id ? updated : s)))
        } catch (err) {
          try {
            const list = await api.fetchServers()
            setServers(list)
          } catch {
            /* keep optimistic state if reload fails */
          }
          pushNotification(
            createNotification({
              type: 'deploy_error',
              title: t('dashboard.notify.updateServerFailed'),
              message: err instanceof Error ? err.message : t('common.error'),
            }),
          )
        }
      })()
    },
    [apiEnabled, pushNotification],
  )

  const addServer = useCallback(
    async (
      data: Omit<VpsServer, 'id' | 'status' | 'port'> & {
        port?: number
        authSecret: string
      },
    ) => {
      if (isLocalHost(data.host)) {
        const message = t('dashboard.notify.localhostBlocked')
        pushNotification(
          createNotification({
            type: 'deploy_error',
            title: t('dashboard.notify.invalidAddress'),
            message,
          }),
        )
        throw new Error(message)
      }

      if (apiEnabled) {
        try {
          const server = await api.createServer({
            name: data.name,
            host: data.host,
            port: data.port,
            username: data.username,
            authSecret: data.authSecret,
            tags: data.tags,
            notes: data.notes,
          })
          if (isLocalHost(server.host)) return
          setServers((prev) => [...prev.filter((s) => !isDemoServerId(s.id)), server])
          setConfigs((prev) => prev.filter((c) => !isDemoServerId(c.serverId)))
          if (server.status === 'connecting') {
            watchServerPrepare(server.id)
          }
          if (!server.location) {
            lookupIpLocation(data.host).then((geo) => {
              if (!geo) return
              setServers((prev) =>
                prev.map((s) => (s.id === server.id ? { ...s, location: geo.location } : s)),
              )
            })
          }
        } catch (err) {
          pushNotification(
            createNotification({
              type: 'deploy_error',
              title: t('dashboard.notify.addServerFailed'),
              message: err instanceof Error ? err.message : t('dashboard.notify.checkSsh'),
            }),
          )
          throw err
        }
        return
      }

      const id = generateId()
      const server: VpsServer = {
        ...data,
        port: data.port ?? 22,
        id,
        status: 'connecting',
        latencyMs: 60 + Math.floor(Math.random() * 80),
      }
      setServers((prev) => [...prev, server])

      lookupIpLocation(data.host).then((geo) => {
        if (!geo) return
        setServers((prev) => prev.map((s) => (s.id === id ? { ...s, location: geo.location } : s)))
      })

      setTimeout(() => {
        setServers((prev) => prev.map((s) => (s.id === id ? { ...s, status: 'online' } : s)))
      }, 2000)
    },
    [apiEnabled, pushNotification, watchServerPrepare],
  )

  const pingServerById = useCallback(
    async (id: string) => {
      if (!apiEnabled || isDemoServerId(id)) return
      try {
        const updated = await api.pingServer(id)
        setServers((prev) => prev.map((s) => (s.id === id ? { ...s, ...updated } : s)))
      } catch (err) {
        pushNotification(
          createNotification({
            type: 'deploy_error',
            title: t('dashboard.notify.serverCheckFailed'),
            message: err instanceof Error ? err.message : t('dashboard.notify.sshUnavailable'),
            serverId: id,
          }),
        )
      }
    },
    [apiEnabled, pushNotification],
  )

  const removeServer = useCallback(
    (id: string) => {
      if (apiEnabled && !isDemoServerId(id)) {
        api.deleteServer(id)
          .then(() => {
            setServers((prev) => prev.filter((s) => s.id !== id))
            setConfigs((prev) =>
              prev.filter((c) => c.serverId !== id && !c.hops?.some((h) => h.serverId === id)),
            )
          })
          .catch((err) => {
            console.error('Failed to delete server:', err)
            pushNotification(createNotification({
              type: 'deploy_error',
              title: t('error') || 'Ошибка',
              message: `Не удалось удалить сервер: ${err.message}`,
            }))
          })
        return
      }
      setServers((prev) => prev.filter((s) => s.id !== id))
      setConfigs((prev) =>
        prev.filter((c) => c.serverId !== id && !c.hops?.some((h) => h.serverId === id)),
      )
    },
    [apiEnabled, pushNotification],
  )

  const deployProtocol = useCallback(
    async (serverId: string, protocol: Protocol, options?: { transport?: OpenVpnTransport; xrayBypass?: boolean; awgVersion?: 'awg' | 'awg1.5' | 'awg2.0' }) => {
      const actor = getActor()
      if (deletingConfigIds.has(serverId)) return

      if (apiEnabled && !isDemoServerId(serverId)) {
        const targetAwgVer = options?.awgVersion ?? 'awg2.0'
        const isSameConfig = (c: VpnConfig, configId?: string) => {
          if (configId && c.id === configId) return true
          return (
            c.serverId === serverId &&
            c.protocol === protocol &&
            (protocol !== 'awg' || (c.awgVersion ?? 'awg2.0') === targetAwgVer) &&
            (!c.hops || c.hops.length < 2)
          )
        }

        const optimistic: VpnConfig = {
          id: 'pending',
          serverId,
          protocol,
          status: 'deploying',
          createdAt: new Date().toISOString(),
          ...(protocol === 'openvpn' && options?.transport ? { transport: options.transport } : {}),
          ...(protocol === 'xray' && options?.xrayBypass ? { xrayBypass: true } : {}),
          ...(protocol === 'awg' ? { awgVersion: targetAwgVer } : {}),
        }

        setConfigs((prev) => [...prev.filter((c) => !isSameConfig(c, 'pending')), optimistic])
        setServers((prev) =>
          prev.map((s) =>
            s.id === serverId
              ? { ...s, activeProtocol: protocol, activeChainHops: undefined, activeChain: undefined }
              : s,
          ),
        )

        pushHistory({
          userId: actor.userId,
          userName: actor.userName,
          type: 'vpn',
          serverId,
          protocol,
          status: 'deploying',
          message: t('dashboard.notify.deployStarting'),
        })

        try {
          const started = await api.deployConfig(serverId, protocol, options)
          setConfigs((prev) => [...prev.filter((c) => !isSameConfig(c, started.id) && c.id !== 'pending'), started])
          const deployed = await waitForConfigDeploy(started.id, {
            onUpdate: (cfg) => {
              setConfigs((prev) => [...prev.filter((c) => c.id !== cfg.id && c.id !== 'pending'), cfg])
            },
          })
          setConfigs((prev) => [...prev.filter((c) => c.id !== deployed.id && c.id !== 'pending'), deployed])
          setServers((prev) =>
            prev.map((s) => (s.id === serverId ? { ...s, activeProtocol: protocol, status: 'online' } : s)),
          )
          pushNotification(notificationForDeploy(deployed, serversRef.current, true))
          pushHistory({
            userId: actor.userId,
            userName: actor.userName,
            type: 'vpn',
            serverId,
            protocol,
            status: 'success',
            message: t('dashboard.notify.deployComplete'),
          })
          return deployed
        } catch (err) {
          const recovered = await recoverDeployedConfig(serverId, protocol)
          if (recovered) {
            setConfigs((prev) => [...prev.filter((c) => c.id !== recovered.id && c.id !== 'pending'), recovered])
            setServers((prev) =>
              prev.map((s) => (s.id === serverId ? { ...s, activeProtocol: protocol, status: 'online' } : s)),
            )
            pushNotification(notificationForDeploy(recovered, serversRef.current, true))
            pushHistory({
              userId: actor.userId,
              userName: actor.userName,
              type: 'vpn',
              serverId,
              protocol,
              status: 'success',
              message: t('dashboard.notify.deployRecovered'),
            })
            return recovered
          }

          const errMsg = err instanceof Error ? err.message : t('dashboard.notify.deployError')
          const failed: VpnConfig = {
            ...optimistic,
            id: generateId(),
            status: 'error',
            statusMessage: errMsg,
          }
          setConfigs((prev) => [...prev.filter((c) => c.id !== failed.id && c.id !== 'pending'), failed])
          pushNotification(notificationForDeploy(failed, serversRef.current, false))
          pushHistory({
            userId: actor.userId,
            userName: actor.userName,
            type: 'vpn',
            serverId,
            protocol,
            status: 'error',
            message: errMsg,
          })
        }
        return
      }

      const config: VpnConfig = {
        id: generateId(),
        serverId,
        protocol,
        status: 'error',
        createdAt: new Date().toISOString(),
        statusMessage: t('dashboard.notify.apiRequiredForDeploy'),
        ...(protocol === 'openvpn' && options?.transport ? { transport: options.transport } : {}),
        ...(protocol === 'xray' && options?.xrayBypass ? { xrayBypass: true } : {}),
      }

      failLocalOnlyDeploy(config, {
        historyType: 'vpn',
        serverId,
        protocol,
      })
    },
    [apiEnabled, failLocalOnlyDeploy, pushNotification],
  )

  const deployChain = useCallback(
    async (hops: ChainHop[]) => {
      if (hops.length < 2) return

      const actor = getActor()
      const entry = hops[0]
      const involvedIds = new Set(hops.map((h) => h.serverId))
      const useApi = apiEnabled && hops.every((h) => !isDemoServerId(h.serverId))

      if (useApi) {
        const optimistic: VpnConfig = {
          id: 'pending-chain',
          serverId: entry.serverId,
          protocol: entry.protocol,
          hops,
          status: 'deploying',
          hopDeployStatus: initialHopDeployStatus(hops.length),
          createdAt: new Date().toISOString(),
        }

        pushHistory({
          userId: actor.userId,
          userName: actor.userName,
          type: 'chain',
          serverId: entry.serverId,
          protocol: entry.protocol,
          hops,
          status: 'deploying',
          message: t('dashboard.notify.chainDeployStarting'),
        })

        setConfigs((prev) => [
          ...prev.filter(
            (c) =>
              !involvedIds.has(c.serverId) && !c.hops?.some((h) => involvedIds.has(h.serverId)),
          ),
          optimistic,
        ])
        savePendingChainDeploy(hops)
        setChainDeployUi(createChainDeploySession(hops))

        try {
          const started = await api.deployChainApi(hops)
          setChainDeployUi((session) =>
            session
              ? {
                  ...session,
                  status: 'deploying',
                  configId: started.id,
                  hopDeployStatus: normalizeHopDeployStatus(
                    started.hopDeployStatus ?? session.hopDeployStatus,
                  ),
                  hops: started.hops ?? session.hops,
                }
              : createChainDeploySession(hops),
          )
          setConfigs((prev) => [
            ...prev.filter(
              (c) =>
                c.id !== 'pending-chain' &&
                !involvedIds.has(c.serverId) &&
                !c.hops?.some((h) => involvedIds.has(h.serverId)),
            ),
            started,
          ])
          const deployed = await waitForConfigDeploy(started.id, {
            onUpdate: (cfg) => {
              setConfigs((prev) => [...prev.filter((c) => c.id !== cfg.id), cfg])
              setChainDeployUi((session) => {
                const base =
                  session ??
                  (cfg.hops && cfg.hops.length >= 2 ? createChainDeploySession(cfg.hops) : null)
                if (!base) return null
                return {
                  ...base,
                  status: 'deploying',
                  configId: cfg.id,
                  hopDeployStatus: normalizeHopDeployStatus(
                    cfg.hopDeployStatus ?? base.hopDeployStatus,
                  ),
                  hops: cfg.hops ?? base.hops,
                }
              })
            },
          })
          setConfigs((prev) => [
            ...prev.filter(
              (c) =>
                c.id !== started.id &&
                !involvedIds.has(c.serverId) &&
                !c.hops?.some((h) => involvedIds.has(h.serverId)),
            ),
            deployed,
          ])
          setServers((prev) =>
            prev.map((s) => {
              if (!involvedIds.has(s.id)) return s
              const hop = hops.find((h) => h.serverId === s.id)!
              const isEntry = s.id === entry.serverId
              return {
                ...s,
                activeProtocol: hop.protocol,
                activeChainHops: isEntry ? hops : undefined,
                status: 'online',
              }
            }),
          )
          pushNotification(notificationForDeploy(deployed, serversRef.current, true))
          pushHistory({
            userId: actor.userId,
            userName: actor.userName,
            type: 'chain',
            serverId: entry.serverId,
            protocol: entry.protocol,
            hops,
            status: 'success',
            message: t('dashboard.notify.chainActive'),
          })
          resetChainDeploySession()
          setChainDraftState([])
        } catch (err) {
          const applyRecovered = (recovered: VpnConfig) => {
            resetChainDeploySession()
            setChainDraftState([])
            setConfigs((prev) => [
              ...prev.filter(
                (c) =>
                  c.id !== 'pending-chain' &&
                  !involvedIds.has(c.serverId) &&
                  !c.hops?.some((h) => involvedIds.has(h.serverId)),
              ),
              recovered,
            ])
            setServers((prev) =>
              prev.map((s) => {
                if (!involvedIds.has(s.id)) return s
                const hop = hops.find((h) => h.serverId === s.id)!
                const isEntry = s.id === entry.serverId
                return {
                  ...s,
                  activeProtocol: hop.protocol,
                  activeChainHops: isEntry ? hops : undefined,
                  status: 'online',
                }
              }),
            )
            pushNotification(notificationForDeploy(recovered, serversRef.current, true))
            pushHistory({
              userId: actor.userId,
              userName: actor.userName,
              type: 'chain',
              serverId: entry.serverId,
              protocol: entry.protocol,
              hops,
              status: 'success',
              message: t('dashboard.notify.deployRecovered'),
            })
          }

          let recovered = await recoverDeployedChain(hops)
          if (!recovered) {
            try {
              recovered = await waitForMatchingChainDeploy(hops, {
                timeoutMs: 120_000,
                onUpdate: (cfg) => {
                  setConfigs((prev) => [...prev.filter((c) => c.id !== cfg.id), cfg])
                },
              })
            } catch (waitErr) {
              if (waitErr instanceof Error) {
                const failed: VpnConfig = {
                  ...optimistic,
                  id: generateId(),
                  status: 'error',
                  hopDeployStatus: hops.map(() => 'error' as const),
                  statusMessage: waitErr.message,
                }
                resetChainDeploySession()
                setConfigs((prev) => [...prev.filter((c) => c.id !== 'pending-chain'), failed])
                pushNotification(notificationForDeploy(failed, serversRef.current, false))
                return
              }
            }
          }
          if (recovered) {
            applyRecovered(recovered)
            return
          }

          const errMsg = err instanceof Error ? err.message : t('dashboard.notify.chainDeployError')
          const failed: VpnConfig = {
            ...optimistic,
            id: generateId(),
            status: 'error',
            hopDeployStatus: hops.map(() => 'error' as const),
            statusMessage: errMsg,
          }
          setConfigs((prev) => [...prev.filter((c) => c.id !== 'pending-chain'), failed])
          resetChainDeploySession()
          pushNotification(notificationForDeploy(failed, serversRef.current, false))
          pushHistory({
            userId: actor.userId,
            userName: actor.userName,
            type: 'chain',
            serverId: entry.serverId,
            protocol: entry.protocol,
            hops,
            status: 'error',
            message: errMsg,
          })
        }
        return
      }

      const config: VpnConfig = {
        id: generateId(),
        serverId: entry.serverId,
        protocol: entry.protocol,
        hops,
        status: 'error',
        hopDeployStatus: hops.map(() => 'error' as const),
        statusMessage: t('dashboard.notify.apiRequiredForDeploy'),
        createdAt: new Date().toISOString(),
      }

      failLocalOnlyDeploy(config, {
        historyType: 'chain',
        serverId: entry.serverId,
        protocol: entry.protocol,
        hops,
        involvedIds,
      })
    },
    [apiEnabled, failLocalOnlyDeploy, pushHistory, pushNotification, resetChainDeploySession],
  )

  const stopConfig = useCallback(
    (id: string) => {
      const actor = getActor()

      const useApi =
        apiEnabled &&
        !id.startsWith('demo-') &&
        !isDemoServerId(configs.find((c) => c.id === id)?.serverId ?? '')

      if (useApi) {
        api.stopConfigApi(id).then((updated) => {
          setConfigs((prev) => prev.map((c) => (c.id === id ? updated : c)))
          const involved = getInvolvedServerIds(updated)
          setServers((srv) =>
            srv.map((s) =>
              involved.includes(s.id)
                ? { ...s, activeProtocol: undefined, activeChainHops: undefined, activeChain: undefined }
                : s,
            ),
          )
        })
        return
      }

      setConfigs((prev) => {
        const config = prev.find((c) => c.id === id)
        if (!config) return prev

        const involved = getInvolvedServerIds(config)
        setServers((srv) =>
          srv.map((s) =>
            involved.includes(s.id)
              ? { ...s, activeProtocol: undefined, activeChainHops: undefined, activeChain: undefined }
              : s,
          ),
        )

        pushHistory({
          userId: actor.userId,
          userName: actor.userName,
          type: config.hops && config.hops.length >= 2 ? 'chain' : 'vpn',
          serverId: config.serverId,
          protocol: config.protocol,
          hops: config.hops,
          status: 'stopped',
          message: t('dashboard.notify.stoppedByUser'),
        })

        return prev.map((c) => (c.id === id ? { ...c, status: 'inactive' } : c))
      })
    },
    [apiEnabled, configs, pushHistory],
  )

  const restartConfig = useCallback(
    (id: string) => {
      const actor = getActor()

      if (apiEnabled) {
        setConfigs((prev) => prev.map((c) => (c.id === id ? { ...c, status: 'deploying' } : c)))
        void (async () => {
          try {
            const updated = await api.restartConfigApi(id)
            setConfigs((prev) => prev.map((c) => (c.id === id ? updated : c)))
            const deployed = await waitForConfigDeploy(updated.id, {
              onUpdate: (cfg) => setConfigs((prev) => prev.map((c) => (c.id === id ? cfg : c))),
            })
            setConfigs((prev) => prev.map((c) => (c.id === id ? deployed : c)))
            pushNotification(notificationForDeploy(deployed, serversRef.current, true))
          } catch {
            setConfigs((prev) => prev.map((c) => (c.id === id ? { ...c, status: 'error' } : c)))
          }
        })()
        return
      }

      const config = configsRef.current.find((c) => c.id === id)
      if (!config || config.status === 'deploying') return

      const errMsg = t('dashboard.notify.apiRequiredForDeploy')
      pushNotification(
        notificationForDeploy({ ...config, status: 'error', statusMessage: errMsg }, serversRef.current, false),
      )
      pushHistory({
        userId: actor.userId,
        userName: actor.userName,
        type: config.hops && config.hops.length >= 2 ? 'chain' : 'vpn',
        serverId: config.serverId,
        protocol: config.protocol,
        hops: config.hops,
        status: 'error',
        message: errMsg,
      })
    },
    [apiEnabled, pushHistory, pushNotification],
  )

  const upgradeAwgConfig = useCallback(
    (id: string) => {
      if (!apiEnabled || id.startsWith('demo-')) return

      setUpgradingAwgIds((prev) => new Set(prev).add(id))
      api
        .upgradeAwgConfigApi(id)
        .then(({ config, upgrade }) => {
          setConfigs((prev) => prev.map((c) => (c.id === id ? config : c)))
          const summary = upgrade
            .map((u) => {
              const ver = u.awgVersion || u.moduleVersion || Object.values(u.packages ?? {}).join(', ')
              return `${u.host}${ver ? `: ${ver}` : ''}`
            })
            .join('; ')
          pushNotification(
            createNotification({
              type: 'deploy_complete',
              title: t('dashboard.notify.awgUpgradeComplete'),
              message: summary || t('dashboard.notify.awgUpgradeDone'),
              configId: id,
              serverId: config.serverId,
            }),
          )
        })
        .catch((err: unknown) => {
          pushNotification(
            createNotification({
              type: 'deploy_error',
              title: t('dashboard.notify.awgUpgradeError'),
              message: err instanceof Error ? err.message : String(err),
              configId: id,
            }),
          )
        })
        .finally(() => {
          setUpgradingAwgIds((prev) => {
            const next = new Set(prev)
            next.delete(id)
            return next
          })
        })
    },
    [apiEnabled, pushNotification],
  )

  const enableSocksProxy = useCallback(
    (id: string) => {
      if (!apiEnabled || id.startsWith('demo-')) return

      setSocksProxyLoadingIds((prev) => new Set(prev).add(id))
      api
        .enableSocksProxyApi(id)
        .then((config) => {
          setConfigs((prev) => prev.map((c) => (c.id === id ? config : c)))
          pushNotification(
            createNotification({
              type: 'deploy_complete',
              title: t('dashboard.notify.socksEnabled'),
              message: config.socksProxy?.uri ?? t('dashboard.notify.socksEnabledHint'),
              configId: id,
              serverId: config.serverId,
            }),
          )
        })
        .catch((err: unknown) => {
          pushNotification(
            createNotification({
              type: 'deploy_error',
              title: t('dashboard.notify.socksError'),
              message: err instanceof Error ? err.message : String(err),
              configId: id,
            }),
          )
        })
        .finally(() => {
          setSocksProxyLoadingIds((prev) => {
            const next = new Set(prev)
            next.delete(id)
            return next
          })
        })
    },
    [apiEnabled, pushNotification],
  )

  const disableSocksProxy = useCallback(
    (id: string) => {
      if (!apiEnabled || id.startsWith('demo-')) return

      setSocksProxyLoadingIds((prev) => new Set(prev).add(id))
      api
        .disableSocksProxyApi(id)
        .then((config) => {
          setConfigs((prev) => prev.map((c) => (c.id === id ? config : c)))
        })
        .catch((err: unknown) => {
          pushNotification(
            createNotification({
              type: 'deploy_error',
              title: t('dashboard.notify.socksError'),
              message: err instanceof Error ? err.message : String(err),
              configId: id,
            }),
          )
        })
        .finally(() => {
          setSocksProxyLoadingIds((prev) => {
            const next = new Set(prev)
            next.delete(id)
            return next
          })
        })
    },
    [apiEnabled, pushNotification],
  )

  const updateConfig = useCallback((config: VpnConfig) => {
    setConfigs((prev) => prev.map((c) => (c.id === config.id ? config : c)))
  }, [])

  const removeConfig = useCallback(
    (id: string) => {
      if (deletingConfigIdsRef.current.has(id) || removingConfigIdsRef.current.has(id)) return

      const actor = getActor()
      const config = configsRef.current.find((c) => c.id === id)
      if (!config) return

      const finishDeleting = () => {
        deletingConfigIdsRef.current.delete(id)
        setDeletingConfigIds((prev) => {
          const next = new Set(prev)
          next.delete(id)
          return next
        })
      }

      const finishRemoving = () => {
        removingConfigIdsRef.current.delete(id)
        setRemovingConfigIds((prev) => {
          const next = new Set(prev)
          next.delete(id)
          return next
        })
      }

      const onRemoved = () => {
        const involved = getInvolvedServerIds(config)
        setConfigs((prev) => prev.filter((c) => c.id !== id))
        setServers((srv) =>
          srv.map((s) => (involved.includes(s.id) ? clearServerVpnState(s.id)(s) : s)),
        )
        const server = serversRef.current.find((s) => s.id === config.serverId)
        const isChain = Boolean(config.hops && config.hops.length >= 2)
        pushNotification(
          createNotification({
            type: 'deploy_complete',
            title: isChain
              ? t('dashboard.notify.chainContainerDeleted')
              : t('dashboard.notify.containerDeleted'),
            message: server?.name ?? config.serverId,
            configId: id,
            serverId: config.serverId,
          }),
        )
        pushHistory({
          userId: actor.userId,
          userName: actor.userName,
          type: isChain ? 'chain' : 'vpn',
          serverId: config.serverId,
          protocol: config.protocol,
          status: 'stopped',
          message: t('dashboard.notify.containerDeleted'),
        })
        finishRemoving()
      }

      const useApi =
        apiEnabled &&
        !id.startsWith('demo-') &&
        !isDemoServerId(config.serverId)

      if (!useApi) {
        removingConfigIdsRef.current.add(id)
        setRemovingConfigIds((prev) => new Set(prev).add(id))
        window.setTimeout(onRemoved, 320)
        return
      }

      deletingConfigIdsRef.current.add(id)
      setDeletingConfigIds((prev) => new Set(prev).add(id))

      api
        .deleteConfigApi(id)
        .then(() => {
          finishDeleting()
          removingConfigIdsRef.current.add(id)
          setRemovingConfigIds((prev) => new Set(prev).add(id))
          window.setTimeout(onRemoved, 320)
        })
        .catch((err) => {
          finishDeleting()
          const server = serversRef.current.find((s) => s.id === config.serverId)
          pushNotification(
            createNotification({
              type: 'deploy_error',
              title: t('dashboard.notify.containerDeleteError'),
              message: err instanceof Error ? err.message : server?.name ?? id,
              configId: id,
              serverId: config.serverId,
            }),
          )
        })
    },
    [apiEnabled, pushHistory, pushNotification],
  )

  const markNotificationRead = useCallback((id: string) => {
    setNotifications((prev) => prev.map((n) => (n.id === id ? { ...n, read: true } : n)))
  }, [])

  const markAllNotificationsRead = useCallback(() => {
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })))
  }, [])

  const dismissNotification = useCallback((id: string) => {
    setNotifications((prev) => prev.filter((n) => n.id !== id))
  }, [])

  const clearAllNotifications = useCallback(() => {
    setNotifications([])
  }, [])

  const unreadNotifications = notifications.filter((n) => !n.read).length

  return (
    <DashboardContext.Provider
      value={{
        servers,
        configs,
        chainDraft,
        userChainTemplates,
        deployHistory,
        notifications,
        unreadNotifications,
        setChainDraft,
        saveUserChainTemplate,
        removeUserChainTemplate,
        addServer,
        updateServer,
        removeServer,
        pingServerById,
        deployProtocol,
        deployChain,
        chainDeployUi,
        stopConfig,
        restartConfig,
        upgradeAwgConfig,
        upgradingAwgIds,
        enableSocksProxy,
        disableSocksProxy,
        socksProxyLoadingIds,
        deletingConfigIds,
        removingConfigIds,
        removeConfig,
        updateConfig,
        markNotificationRead,
        markAllNotificationsRead,
        dismissNotification,
        clearAllNotifications,
      }}
    >
      {children}
    </DashboardContext.Provider>
  )
}

export function useDashboard() {
  const ctx = useContext(DashboardContext)
  if (!ctx) throw new Error('useDashboard must be used within DashboardProvider')
  return ctx
}
