import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { ErrorBoundary } from '@/components/ui/ErrorBoundary'

const OverviewTab = lazy(() => import('@/components/dashboard/OverviewTab').then((m) => ({ default: m.OverviewTab })))
const ServersTab = lazy(() => import('@/components/dashboard/ServersTab').then((m) => ({ default: m.ServersTab })))
const StatusTab = lazy(() => import('@/components/dashboard/StatusTab').then((m) => ({ default: m.StatusTab })))
const VpnTab = lazy(() => import('@/components/dashboard/VpnTab').then((m) => ({ default: m.VpnTab })))
const ChainsTab = lazy(() => import('@/components/dashboard/ChainsTab').then((m) => ({ default: m.ChainsTab })))
const ProxyTab = lazy(() => import('@/components/dashboard/ProxyTab').then((m) => ({ default: m.ProxyTab })))
const ClientConfigsTab = lazy(() => import('@/components/dashboard/ClientConfigsTab').then((m) => ({ default: m.ClientConfigsTab })))
const SettingsTab = lazy(() => import('@/components/dashboard/SettingsTab').then((m) => ({ default: m.SettingsTab })))

function RouteLoadingFallback() {
  return (
    <div className="flex items-center justify-center py-20 text-zinc-400" role="status" aria-label="Loading">
      <Loader2 className="h-7 w-7 animate-spin text-accent-cyan/80" />
    </div>
  )
}

export function DashboardRoutes() {
  const { pathname } = useLocation()
  return (
    <ErrorBoundary key={pathname}>
      <Suspense fallback={<RouteLoadingFallback />}>
        <Routes>
          <Route index element={<OverviewTab />} />
          <Route path="servers" element={<ServersTab />} />
          <Route path="status" element={<StatusTab />} />
          <Route path="vpn" element={<VpnTab />} />
          <Route path="chains" element={<ChainsTab />} />
          <Route path="proxy" element={<ProxyTab />} />
          <Route path="configs" element={<ClientConfigsTab />} />
          <Route path="settings" element={<SettingsTab />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </ErrorBoundary>
  )
}
