import { Navigate, Route, Routes } from 'react-router-dom'
import { OverviewTab } from '@/components/dashboard/OverviewTab'
import { ServersTab } from '@/components/dashboard/ServersTab'
import { VpnTab } from '@/components/dashboard/VpnTab'
import { ChainsTab } from '@/components/dashboard/ChainsTab'
import { ClientConfigsTab } from '@/components/dashboard/ClientConfigsTab'
import { StatusTab } from '@/components/dashboard/StatusTab'
import { SettingsTab } from '@/components/dashboard/SettingsTab'
import { ProxyTab } from '@/components/dashboard/ProxyTab'

export function DashboardRoutes() {
  return (
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
  )
}
