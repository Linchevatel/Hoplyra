import { useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { LogIn } from 'lucide-react'
import { Logo } from '@/components/layout/Logo'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { useAuth } from '@/lib/auth'
import { useI18n } from '@/i18n/I18nProvider'

export function LoginPage() {
  const { t } = useI18n()
  const { loading, authenticated, login } = useAuth()
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (!loading && authenticated) {
    return <Navigate to="/" replace />
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(username.trim(), password)
    } catch {
      setError(t('auth.loginFailed'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen mesh-bg flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="flex justify-center mb-8">
          <Logo size="lg" showSlogan />
        </div>

        <div className="glass glow-border rounded-2xl p-6 sm:p-8 shadow-2xl shadow-cyan-500/5">
          <div className="mb-6 text-center">
            <h1 className="text-2xl font-bold text-zinc-100">{t('auth.title')}</h1>
            <p className="mt-2 text-sm text-zinc-400">{t('auth.subtitle')}</p>
          </div>

          <form onSubmit={onSubmit} className="space-y-4">
            <p className="text-xs text-zinc-500 text-center -mt-2">{t('auth.defaultHint')}</p>
            <Input
              label={t('auth.username')}
              name="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={submitting || loading}
              required
            />
            <Input
              label={t('auth.password')}
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={submitting || loading}
              error={error ?? undefined}
              required
            />
            <Button
              type="submit"
              size="lg"
              className="w-full mt-2"
              disabled={submitting || loading}
            >
              <LogIn size={18} />
              {submitting ? t('auth.signingIn') : t('auth.signIn')}
            </Button>
          </form>
        </div>
      </div>
    </div>
  )
}
