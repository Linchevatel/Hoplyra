import { useState, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { LogIn } from 'lucide-react'
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion'
import logoCandidate from '@/assets/logo/candidate-tl.png'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { ApiError } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'

export function LoginPage() {
  const { loading, authenticated, defaultPassword, login } = useAuth()
  const shouldReduceMotion = useReducedMotion()
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [totpCode, setTotpCode] = useState('')
  const [twoFactorNeeded, setTwoFactorNeeded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [igniting, setIgniting] = useState(false)

  // Перенаправляем на главную сразу при prefers-reduced-motion или после окончания анимации
  if (!loading && authenticated && (!igniting || shouldReduceMotion)) {
    return <Navigate to="/" replace />
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const res = await login(username.trim(), password, twoFactorNeeded ? totpCode.trim() : undefined)
      if (res.twoFactorRequired) {
        setTwoFactorNeeded(true)
        setSubmitting(false)
      } else if (shouldReduceMotion) {
        // При включенном ограничении движения сразу завершаем вход без пауз и анимаций
        setSubmitting(false)
      } else {
        // Успешный вход: запускаем анимацию "Кибер-шлюз / Голографический щит" (English only)
        setIgniting(true)
        setTimeout(() => {
          setIgniting(false)
        }, 2800)
      }
    } catch (err) {
      setSubmitting(false)
      if (err instanceof ApiError) {
        if (err.status === 429) {
          if (err.retryAfter) {
            setError(`Too many attempts. Try again in ${err.retryAfter} seconds.`)
          } else {
            setError(err.detail || 'Too many attempts. Try again later.')
          }
        } else if (err.status === 401) {
          setError(twoFactorNeeded ? 'Invalid 6-digit 2FA code' : 'Invalid username or password')
        } else {
          setError(err.detail || 'Authentication error')
        }
      } else if (err instanceof Error && err.message) {
        setError(err.message)
      } else {
        setError(twoFactorNeeded ? 'Invalid 6-digit 2FA code' : 'Invalid username or password')
      }
    }
  }

  return (
    <main className="relative isolate min-h-screen mesh-bg flex items-center justify-center px-4 py-8 overflow-hidden">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(ellipse_at_center,rgba(212,162,76,0.045),transparent_65%)]" />

      {/* Анимация: "Кибер-шлюз / Голографический щит" (English UI) */}
      <AnimatePresence>
        {igniting && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 backdrop-blur-2xl pointer-events-none overflow-hidden"
          >
            {/* Оптический визор шлюза */}
            <div className="absolute inset-0 flex items-center justify-center">
              {/* Внешняя шкала синхронизации */}
              <motion.div
                initial={{ scale: 0.4, opacity: 0, rotate: 0 }}
                animate={{ scale: [0.4, 1.25, 1.15], opacity: [0, 0.7, 0.45], rotate: 360 }}
                transition={{ duration: 2.8, ease: 'linear' }}
                className="w-[520px] h-[520px] rounded-full border border-dashed border-accent-cyan/35 shadow-[0_0_60px_rgba(212,162,76,0.3)]"
              />

              {/* Внутреннее быстрое кольцо синхронизации */}
              <motion.div
                initial={{ scale: 0.3, opacity: 0, rotate: 0 }}
                animate={{ scale: [0.3, 1.15, 1], opacity: [0, 0.9, 0.6], rotate: -360 }}
                transition={{ duration: 2.4, ease: 'linear' }}
                className="absolute w-[400px] h-[400px] rounded-full border-2 border-t-amber-400 border-r-transparent border-b-accent-violet border-l-transparent shadow-[0_0_40px_rgba(245,158,11,0.5)]"
              />

              {/* Импульсная волна верификации */}
              <motion.div
                initial={{ scale: 0.1, opacity: 1 }}
                animate={{ scale: 4.8, opacity: 0 }}
                transition={{ duration: 1.8, delay: 0.5, ease: 'easeOut' }}
                className="absolute w-64 h-64 rounded-full border-2 border-emerald-400 shadow-[0_0_80px_rgba(16,185,129,0.8)]"
              />

              {/* Лазерные перекрестия визора */}
              <motion.div
                initial={{ opacity: 0, scaleX: 0 }}
                animate={{ opacity: [0, 0.6, 0.3], scaleX: [0, 1.2, 1] }}
                transition={{ duration: 0.8, delay: 0.2 }}
                className="absolute w-[600px] h-[1px] bg-gradient-to-r from-transparent via-accent-cyan/60 to-transparent"
              />
              <motion.div
                initial={{ opacity: 0, scaleY: 0 }}
                animate={{ opacity: [0, 0.6, 0.3], scaleY: [0, 1.2, 1] }}
                transition={{ duration: 0.8, delay: 0.2 }}
                className="absolute h-[600px] w-[1px] bg-gradient-to-b from-transparent via-accent-cyan/60 to-transparent"
              />
            </div>

            {/* Центральный блок: Голографический щит с лазерным сканером */}
            <div className="relative flex flex-col items-center justify-center z-10">
              <div className="relative">
                <motion.img
                  src={logoCandidate}
                  alt="Hoplyra Security Shield"
                  draggable={false}
                  initial={{ scale: 0, opacity: 0, y: 20 }}
                  animate={{ scale: [0, 1.3, 1], opacity: 1, y: 0 }}
                  transition={{ duration: 0.7, ease: 'backOut' }}
                  className="h-36 w-36 sm:h-44 sm:w-44 object-contain drop-shadow-[0_0_45px_rgba(212,162,76,0.9)]"
                />

                {/* Сканирующий луч */}
                <motion.div
                  initial={{ top: '-10%', opacity: 0 }}
                  animate={{ top: ['0%', '100%'], opacity: [0, 1, 1, 0] }}
                  transition={{ duration: 1.2, delay: 0.6, ease: 'easeInOut' }}
                  className="absolute left-0 right-0 h-1 bg-gradient-to-r from-transparent via-emerald-300 to-transparent shadow-[0_0_20px_#10B981]"
                />
              </div>

              {/* Статус шлюза на английском */}
              <motion.div
                initial={{ opacity: 0, y: 15 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4, delay: 0.8 }}
                className="mt-6 flex flex-col items-center gap-2"
              >
                <div className="flex items-center gap-2 px-4 py-1.5 rounded-full border border-emerald-500/40 bg-emerald-500/15 shadow-[0_0_25px_rgba(16,185,129,0.4)]">
                  <span className="h-2 w-2 rounded-full bg-emerald-400 animate-ping" />
                  <span className="text-xs uppercase tracking-widest font-mono font-bold text-emerald-300">
                    ACCESS GRANTED
                  </span>
                </div>
                <span className="text-[11px] font-mono tracking-wider text-accent-cyan/80">
                  Login successful
                </span>
              </motion.div>
            </div>

            {/* Финальная золотая вспышка */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: [0, 0.85, 0] }}
              transition={{ duration: 0.7, delay: 2.2 }}
              className="absolute inset-0 bg-gradient-to-t from-emerald-500/25 via-amber-400/20 to-transparent pointer-events-none"
            />
          </motion.div>
        )}
      </AnimatePresence>

      <motion.section
        aria-labelledby="login-title"
        animate={igniting ? { scale: 0.94, opacity: 0, filter: 'blur(12px)' } : { scale: 1, opacity: 1, filter: 'blur(0px)' }}
        transition={{ duration: 0.6 }}
        className="glass bento-card glow-cyan w-full max-w-sm p-6 sm:p-8"
      >
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
            <p className="mt-2 text-xs text-text-muted">Orchestrate your hop chains</p>
            <h2 className="mt-6 text-lg text-text-primary">Sign in</h2>
            <p className="mt-1 text-sm text-text-secondary">Enter your administrator credentials</p>
          </header>

          <form onSubmit={onSubmit} className="space-y-4">
            {defaultPassword && (
              <p role="status" className="rounded-xl border border-accent-cyan/20 bg-accent-cyan/5 p-3 text-xs leading-relaxed text-amber-300">
                Default password is in use. After install: admin / admin
              </p>
            )}
            <Input
              label="Username"
              className="surface-panel"
              name="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={submitting || loading || igniting}
              required
            />
            <Input
              label="Password"
              className="surface-panel"
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={submitting || loading || twoFactorNeeded || igniting}
              required
            />
            {twoFactorNeeded && (
              <Input
                label="Two-factor code (2FA)"
                className="surface-panel font-mono text-center tracking-widest text-lg"
                name="totpCode"
                autoComplete="one-time-code"
                placeholder="123456"
                maxLength={6}
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, ''))}
                disabled={submitting || loading || igniting}
                error={error ?? undefined}
                autoFocus
                required
              />
            )}
            {!twoFactorNeeded && error && (
              <p className="text-xs text-rose-400">{error}</p>
            )}
            <Button
              type="submit"
              size="lg"
              className="w-full mt-2"
              disabled={submitting || loading || igniting || (twoFactorNeeded && totpCode.length !== 6)}
            >
              <LogIn size={18} />
              {submitting || igniting ? 'Signing in…' : twoFactorNeeded ? 'Verify' : 'Sign in'}
            </Button>
            {twoFactorNeeded && (
              <button
                type="button"
                onClick={() => { setTwoFactorNeeded(false); setTotpCode(''); setError(null) }}
                className="w-full text-center text-xs text-text-muted hover:text-zinc-300 transition-colors pt-1 cursor-pointer"
              >
                Cancel
              </button>
            )}
          </form>
      </motion.section>
    </main>
  )
}
