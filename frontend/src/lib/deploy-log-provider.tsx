import { useCallback, useState, type ReactNode } from 'react'
import { DeployLogContext } from './deploy-log-context'
import { useDeployLog } from './use-deploy-log'

type Target = { configId: string; title: string; subtitle: string }
function useLogController() {
  const [target, setTarget] = useState<Target | null>(null)
  const [isCollapsed, setCollapsed] = useState(false)
  const log = useDeployLog(target?.configId ?? null)
  const open = useCallback((configId: string, labels: { title: string; subtitle: string }) => {
    setTarget({ configId, ...labels }); setCollapsed(false)
  }, [])
  const close = useCallback(() => setTarget(null), [])
  const collapse = useCallback(() => setCollapsed(true), [])
  const expand = useCallback(() => setCollapsed(false), [])
  return { ...log, target, isOpen: target !== null, isCollapsed, open, close, collapse, expand }
}
export function DeployLogProvider({ children }: { children: ReactNode }) {
  const value = useLogController()
  return <DeployLogContext.Provider value={value}>{children}</DeployLogContext.Provider>
}
