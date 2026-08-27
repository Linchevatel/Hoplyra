import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from '@/lib/auth'
import { DashboardProvider } from '@/lib/dashboard'
import { DashboardPage } from '@/pages/DashboardPage'
import { LoginPage } from '@/pages/LoginPage'

function ProtectedDashboard() {
  const { loading, authenticated } = useAuth()

  if (loading) {
    return (
      <div className="min-h-screen mesh-bg flex items-center justify-center text-zinc-400">
        …
      </div>
    )
  }

  if (!authenticated) {
    return <Navigate to="/login" replace />
  }

  return (
    <DashboardProvider>
      <DashboardPage />
    </DashboardProvider>
  )
}

export function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/*" element={<ProtectedDashboard />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
