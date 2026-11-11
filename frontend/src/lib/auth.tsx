import { AuthContext, type AuthState } from './auth-context'
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { fetchAuthMe, loginApi, logoutApi, setUnauthorizedHandler } from '@/lib/api'


export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    loading: true,
    authenticated: false,
    authRequired: true,
    username: null,
    defaultPassword: false,
  })

  const refresh = useCallback(async () => {
    try {
      const me = await fetchAuthMe()
      setState({
        loading: false,
        authenticated: me.authenticated,
        authRequired: me.authRequired,
        username: me.username ?? null,
        defaultPassword: me.defaultPassword ?? false,
      })
    } catch {
      setState({
        loading: false,
        authenticated: false,
        authRequired: true,
        username: null,
        defaultPassword: false,
      })
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    void fetchAuthMe().then((me) => {
      if (cancelled) return
      setState({ loading: false, authenticated: me.authenticated, authRequired: me.authRequired,
        username: me.username ?? null, defaultPassword: me.defaultPassword ?? false })
    }).catch(() => {
      if (cancelled) return
      setState({ loading: false, authenticated: false, authRequired: true,
        username: null, defaultPassword: false })
    })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setState((prev) => ({
        ...prev,
        loading: false,
        authenticated: false,
        username: null,
      }))
    })
    return () => setUnauthorizedHandler(null)
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const result = await loginApi(username, password)
    setState({
      loading: false,
      authenticated: true,
      authRequired: true,
      username: result.username,
      defaultPassword: result.defaultPassword ?? false,
    })
  }, [])

  const logout = useCallback(async () => {
    try {
      await logoutApi()
    } catch {
      /* session may already be expired */
    }
    setState({
      loading: false,
      authenticated: false,
      authRequired: true,
      username: null,
      defaultPassword: false,
    })
  }, [])

  const value = useMemo(
    () => ({
      ...state,
      login,
      logout,
      refresh,
    }),
    [state, login, logout, refresh],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
