import { useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { LogIn } from 'lucide-react'
import logoCandidate from '@/assets/logo/candidate-tl.png'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { useAuth } from '@/lib/auth-context'
import { useI18n } from '@/i18n/i18n-context'

export function LoginPage() {
  const { t } = useI18n()
  const { loading, authenticated, defaultPassword, login } = useAuth()
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
    <main className="relative isolate min-h-screen mesh-bg flex items-center justify-center px-4 py-8">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(ellipse_at_center,rgba(212,162,76,0.045),transparent_65%)]" />
      <section aria-labelledby="login-title" className="glass bento-card glow-cyan w-full max-w-sm p-6 sm:p-8">
          <header className="mb-7 text-center">
            <img
              src={logoCandidate}
              alt="Hoplyra"
              draggable={false}
              width={96}
              height={96}
              className="mx-auto mb-4 h-24 w-24 object-contain drop-shadow-[0_0_22px_rgba(212,162,76,0.25)]"
            />
            <h1 id="login-title" className="text-3xl tracking-tight">Hop<span className="gradient-text">lyra</span></h1>
            <p className="mt-2 text-xs text-text-muted">{t('brand.slogan')}</p>
            <h2 className="mt-6 text-lg text-text-primary">{t('auth.title')}</h2>
            <p className="mt-1 text-sm text-text-secondary">{t('auth.subtitle')}</p>
          </header>

          <form onSubmit={onSubmit} className="space-y-4">
            {defaultPassword && (
              <p role="status" className="rounded-xl border border-accent-cyan/20 bg-accent-cyan/5 p-3 text-xs leading-relaxed text-amber-300">
                {t('settings.defaultPasswordTitle')}. {t('auth.defaultHint')}
              </p>
            )}
            <Input
              label={t('auth.username')}
              className="surface-panel"
              name="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={submitting || loading}
              required
            />
            <Input
              label={t('auth.password')}
              className="surface-panel"
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
      </section>
    </main>
  )
}
