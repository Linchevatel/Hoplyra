import { useEffect, useState } from 'react'
import { deployLogStreamUrl, fetchDeployLog, ApiError } from './api'
import { emptyDeployLog, mergeDeployLog, type DeployLogPayload, type DeployLogState } from './deploy-log-state'

export function useDeployLog(configId: string | null) {
  const [current, setCurrent] = useState<{ id: string | null; state: DeployLogState }>({ id: null, state: emptyDeployLog })
  useEffect(() => {
    if (!configId) return
    let stopped = false
    let finished = false
    let cursor = 0
    let attempts = 0
    let source: EventSource | undefined
    let pollTimer: ReturnType<typeof setTimeout> | undefined
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    const accept = (payload: DeployLogPayload) => {
      if (stopped) return
      cursor = Math.max(cursor, ...(payload.events ?? []).map((event) => event.seq))
      finished = payload.status.finished
      setCurrent((previous) => ({ id: configId, state: mergeDeployLog(previous.id === configId ? previous.state : emptyDeployLog, payload) }))
      if (finished) {
        source?.close()
        clearTimeout(pollTimer)
        clearTimeout(retryTimer)
      }
    }
    const poll = async () => {
      try { accept(await fetchDeployLog(configId, cursor)) } catch (error) {
        if (error instanceof ApiError && (error.status === 401 || error.status === 404)) {
          finished = true
          source?.close()
          clearTimeout(retryTimer)
          clearTimeout(pollTimer)
          setCurrent((previous) => {
            const base = previous.id === configId ? previous.state : emptyDeployLog
            return {
              id: configId,
              state: {
                ...base,
                status: { ...base.status, finished: true, active: false },
              },
            }
          })
          return
        }
      }
      if (!stopped && !finished && source?.readyState !== EventSource.OPEN) {
        pollTimer = setTimeout(() => void poll(), 1500)
      }
    }
    const connect = () => {
      if (stopped || finished) return
      if (typeof EventSource === 'undefined') { void poll(); return }
      source = new EventSource(deployLogStreamUrl(configId, cursor), { withCredentials: true })
      source.onmessage = (event) => {
        try { accept(JSON.parse(event.data) as DeployLogPayload); attempts = 0 } catch { /* reconnect/poll provides the next valid frame */ }
      }
      source.addEventListener('end', (event) => {
        try { accept(JSON.parse((event as MessageEvent).data) as DeployLogPayload) } catch { /* ignore invalid frame */ }
        source?.close()
      })
      source.onopen = () => { clearTimeout(pollTimer) }
      source.onerror = () => {
        source?.close()
        if (stopped || finished) return
        clearTimeout(pollTimer)
        void poll()
        retryTimer = setTimeout(connect, Math.min(30000, 1000 * 2 ** attempts++))
      }
    }
    connect()
    return () => { stopped = true; source?.close(); clearTimeout(pollTimer); clearTimeout(retryTimer) }
  }, [configId])
  return current.id === configId ? current.state : emptyDeployLog
}
