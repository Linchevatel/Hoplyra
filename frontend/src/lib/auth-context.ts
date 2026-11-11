import { createContext, useContext } from 'react'
export type AuthState = {
  loading: boolean
  authenticated: boolean
  authRequired: boolean
  username: string | null
  defaultPassword: boolean
}

type AuthContextValue = AuthState & {
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refresh: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
