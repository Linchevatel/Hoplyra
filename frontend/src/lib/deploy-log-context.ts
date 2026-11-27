import { createContext, useContext } from 'react'
import type { DeployLogState } from './deploy-log-state'

type DeployLogController = DeployLogState & {
  target: { configId: string; title: string; subtitle: string } | null
  isOpen: boolean
  isCollapsed: boolean
  open: (configId: string, labels: { title: string; subtitle: string }) => void
  close: () => void
  collapse: () => void
  expand: () => void
}
export const DeployLogContext = createContext<DeployLogController | null>(null)
export function useDeployLogDrawer() {
  const value = useContext(DeployLogContext)
  if (!value) throw new Error('useDeployLogDrawer must be used within DeployLogProvider')
  return value
}
