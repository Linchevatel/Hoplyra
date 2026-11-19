import { useState, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { LogIn } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import logoCandidate from '@/assets/logo/candidate-tl.png'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { ApiError } from '@/lib/api'
import { useAuth } from '@/lib/auth-context'
import { useI18n } from '@/i18n/i18n-context'

export function LoginPage() {
  const { t } = useI18n()
  const { loading, authenticated, defaultPassword, login } = useAuth()
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [totpCode, setTotpCode] = useState('')
  const [twoFactorNeeded, setTwoFactorNeeded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [igniting, setIgniting] = useState(false)

  if (!loading && authenticated && !igniting) {
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
      } else {
        setIgniting(true)
        setTimeout(() => {
          setIgniting(false)
        }, 3000)
      }
    } catch (err) {
      setSubmitting(false)
      if (err instanceof ApiError) {
        if (err.status === 429) {
          setError(err.detail || err.message)
        } else if (err.status === 401) {
          setError(twoFactorNeeded ? t('auth.invalid2faCode') : t('auth.loginFailed'))
        } else {
          setError(err.detail || t('common.error'))
        }
      } else if (err instanceof Error && err.message) {
        setError(err.message)
      } else {
        setError(twoFactorNeeded ? t('auth.invalid2faCode') : t('auth.loginFailed'))
      }
    }
  }

  // Распределенные соты по всему экрану (координаты 1920x1080)
  const screenNodes = [
    { id: 'nw1',  x: 280, y: 180, label: 'SEATTLE',     r: 12, color: '#F59E0B' },
    { id: 'nw2',  x: 580, y: 260, label: 'CHICAGO',     r: 11, color: '#D4A24C' },
    { id: 'nw3',  x: 140, y: 420, label: 'WEST-GATE',   r: 10, color: '#9D4EDD' },
    { id: 'ne1',  x: 1380, y: 220, label: 'RU-MSK-01',   r: 13, color: '#F59E0B' },
    { id: 'ne2',  x: 1680, y: 140, label: 'RU-SPB-02',   r: 11, color: '#D4A24C' },
    { id: 'ne3',  x: 1780, y: 380, label: 'SIBERIA',     r: 10, color: '#9D4EDD' },
    { id: 'top',  x: 960,  y: 120, label: 'STOCKHOLM',   r: 12, color: '#F59E0B' },
    { id: 'sw1',  x: 220, y: 880, label: 'SAO-PAULO',   r: 11, color: '#9D4EDD' },
    { id: 'sw2',  x: 520, y: 780, label: 'FRANKFURT',   r: 13, color: '#D4A24C' },
    { id: 'sw3',  x: 380, y: 580, label: 'AMSTERDAM',   r: 12, color: '#F59E0B' },
    { id: 'se1',  x: 1420, y: 820, label: 'TOKYO-AP',    r: 13, color: '#F59E0B' },
    { id: 'se2',  x: 1720, y: 720, label: 'SINGAPORE',   r: 11, color: '#9D4EDD' },
    { id: 'se3',  x: 1260, y: 640, label: 'DUBAI-GATE',  r: 12, color: '#D4A24C' },
    { id: 'bot',  x: 960,  y: 940, label: 'GLOBAL-EXIT', r: 11, color: '#F59E0B' },
  ]

  // Генератор вершин соты (шестиугольника)
  function getHexPoints(cx: number, cy: number, r: number) {
    const pts: string[] = []
    for (let i = 0; i < 6; i++) {
      const angle = (Math.PI / 3) * i - Math.PI / 6
      const x = cx + r * Math.cos(angle)
      const y = cy + r * Math.sin(angle)
      pts.push(`${x.toFixed(1)},${y.toFixed(1)}`)
    }
    return pts.join(' ')
  }

  // Молнии, вырывающиеся прямо из краев центрального ЩИТА (x: 960, y: 540)
  const fullLightningArcs = [
    // 1. Первичные разряды из вершин и граней золотого щита наружу (задержка 0.4s после материализации щита)
    { d: 'M910,500 L810,440 L840,360 L680,310 L580,260', delay: 0.45 },
    { d: 'M1010,500 L1120,430 L1080,340 L1260,280 L1380,220', delay: 0.5 },
    { d: 'M920,580 L820,620 L860,700 L680,740 L520,780', delay: 0.55 },
    { d: 'M1000,580 L1140,580 L1110,680 L1220,660 L1420,820', delay: 0.6 },
    { d: 'M960,460 L940,410 L990,320 L930,220 L960,120', delay: 0.65 },
    { d: 'M960,620 L980,670 L930,760 L1010,850 L960,940', delay: 0.7 },

    // 2. Вторичные молнии к дальним углам монитора
    { d: 'M580,260 L460,240 L380,210 L280,180', delay: 0.8 },
    { d: 'M580,260 L480,360 L420,460 L380,580', delay: 0.9 },
    { d: 'M280,180 L220,290 L180,350 L140,420', delay: 1.05 },
    { d: 'M1380,220 L1510,180 L1600,165 L1680,140', delay: 0.85 },
    { d: 'M1380,220 L1520,280 L1620,320 L1780,380', delay: 1.0 },
    { d: 'M960,120 L1120,110 L1260,160 L1380,220', delay: 1.1 },
    { d: 'M960,120 L780,130 L660,190 L580,260', delay: 1.15 },

    // 3. Нижняя полусфера экрана
    { d: 'M520,780 L410,810 L320,860 L220,880', delay: 0.95 },
    { d: 'M380,580 L320,670 L260,770 L220,880', delay: 1.2 },
    { d: 'M1420,820 L1540,780 L1630,760 L1720,720', delay: 0.9 },
    { d: 'M1420,820 L1220,890 L1110,910 L960,940', delay: 1.25 },
    { d: 'M520,780 L680,840 L810,910 L960,940', delay: 1.3 },
    { d: 'M1720,720 L1810,560 L1790,460 L1780,380', delay: 1.4 },
    { d: 'M140,420 L120,580 L170,740 L220,880', delay: 1.45 },

    // 4. Замыкание контура паутины
    { d: 'M380,580 L560,540 L740,560 L920,540', delay: 1.55 },
    { d: 'M1260,640 L1110,580 L1040,550 L1000,540', delay: 1.6 },
    { d: 'M960,120 L1040,70 L1520,90 L1680,140', delay: 1.7 },
    { d: 'M280,180 L480,90 L810,70 L960,120', delay: 1.8 },
  ]

  return (
    <main className="relative isolate min-h-screen mesh-bg flex items-center justify-center px-4 py-8 overflow-hidden">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(ellipse_at_center,rgba(212,162,76,0.045),transparent_65%)]" />

      {/* Полноэкранная неоновая паутина молний, берущая начало из ЩИТА */}
      <AnimatePresence>
        {igniting && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/92 backdrop-blur-2xl pointer-events-none overflow-hidden"
          >
            {/* Тонкие золотые импульсные кольца вокруг щита */}
            <motion.div
              initial={{ scale: 0.1, opacity: 0.95 }}
              animate={{ scale: 5.5, opacity: 0 }}
              transition={{ duration: 2.4, delay: 0.3, ease: 'easeOut' }}
              className="absolute w-80 h-80 rounded-full border border-accent-cyan/70 shadow-[0_0_80px_rgba(212,162,76,0.6)]"
            />
            <motion.div
              initial={{ scale: 0.1, opacity: 0.85 }}
              animate={{ scale: 4.8, opacity: 0 }}
              transition={{ duration: 2.2, delay: 0.6, ease: 'easeOut' }}
              className="absolute w-96 h-96 rounded-full border border-amber-400/50 shadow-[0_0_70px_rgba(245,158,11,0.5)]"
            />

            {/* Векторный холст молний на весь экран */}
            <svg viewBox="0 0 1920 1080" preserveAspectRatio="none" className="absolute inset-0 w-full h-full">
              <defs>
                <linearGradient id="thinLightningGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stopColor="#D4A24C" />
                  <stop offset="45%" stopColor="#F59E0B" />
                  <stop offset="75%" stopColor="#EAB308" />
                  <stop offset="100%" stopColor="#9D4EDD" />
                </linearGradient>

                <linearGradient id="shieldGoldGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stopColor="#FDE68A" />
                  <stop offset="50%" stopColor="#D4A24C" />
                  <stop offset="100%" stopColor="#B45309" />
                </linearGradient>

                {/* Нежный ореол свечения молний и щита */}
                <filter id="softLightningGlow" x="-50%" y="-50%" width="200%" height="200%">
                  <feGaussianBlur in="SourceGraphic" stdDeviation="2.5" result="blur1" />
                  <feGaussianBlur in="SourceGraphic" stdDeviation="6" result="blur2" />
                  <feMerge>
                    <feMergeNode in="blur2" />
                    <feMergeNode in="blur1" />
                    <feMergeNode in="SourceGraphic" />
                  </feMerge>
                </filter>
              </defs>

              {/* Тонкие элегантные нити молний */}
              {fullLightningArcs.map((arc, i) => (
                <g key={i}>
                  {/* Полупрозрачное свечение нити */}
                  <motion.path
                    d={arc.d}
                    fill="none"
                    stroke="url(#thinLightningGrad)"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    filter="url(#softLightningGlow)"
                    initial={{ pathLength: 0, opacity: 0 }}
                    animate={{ pathLength: 1, opacity: [0, 0.85, 0.65] }}
                    transition={{ duration: 0.4, delay: arc.delay, ease: 'easeOut' }}
                  />
                  {/* Изящная раскаленная жила молнии */}
                  <motion.path
                    d={arc.d}
                    fill="none"
                    stroke="#FFFDF5"
                    strokeWidth="1.1"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    initial={{ pathLength: 0, opacity: 0 }}
                    animate={{ pathLength: 1, opacity: [0, 1, 0.9] }}
                    transition={{ duration: 0.28, delay: arc.delay, ease: 'easeOut' }}
                  />
                </g>
              ))}

              {/* Внешние узлы-соты по экрану */}
              {screenNodes.map((node, idx) => (
                <g key={node.id}>
                  {/* Внешний пульсирующий контур соты */}
                  <motion.polygon
                    points={getHexPoints(node.x, node.y, node.r * 2.2)}
                    fill="none"
                    stroke="rgba(245,158,11,0.4)"
                    strokeWidth="1.2"
                    initial={{ scale: 0, opacity: 0 }}
                    animate={{ scale: [0, 1.4, 1], opacity: [0, 0.7, 0.35] }}
                    transition={{ duration: 0.6, delay: 0.4 + idx * 0.08 }}
                  />
                  {/* Внутренняя светящаяся сота */}
                  <motion.polygon
                    points={getHexPoints(node.x, node.y, node.r)}
                    fill="rgba(13,17,23,0.85)"
                    stroke={node.color}
                    strokeWidth="1.8"
                    initial={{ scale: 0, opacity: 0 }}
                    animate={{ scale: [0, 1.3, 1], opacity: 1 }}
                    transition={{ duration: 0.4, delay: 0.4 + idx * 0.08, ease: 'backOut' }}
                    filter="url(#softLightningGlow)"
                  />
                  {/* Точка кристалла соты */}
                  <motion.circle
                    cx={node.x}
                    cy={node.y}
                    r={node.r * 0.35}
                    fill="#FFFBEB"
                    initial={{ scale: 0 }}
                    animate={{ scale: [0, 1.2, 1] }}
                    transition={{ duration: 0.3, delay: 0.5 + idx * 0.08 }}
                  />
                  {/* Подпись узла */}
                  <motion.text
                    x={node.x}
                    y={node.y + (node.y > 540 ? 32 : -22)}
                    textAnchor="middle"
                    fill="#FDE68A"
                    fontSize="10"
                    fontWeight="600"
                    letterSpacing="2"
                    initial={{ opacity: 0, y: 5 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.4, delay: 0.5 + idx * 0.08 }}
                  >
                    {node.label}
                  </motion.text>
                </g>
              ))}

              {/* ЦЕНТРАЛЬНЫЙ ОРИГИНАЛЬНЫЙ ЩИТ-ЛОГОТИП HOPLYRA (ИСТОЧНИК МОЛНИЙ) */}
              <g transform="translate(960, 540)">
                {/* Расширяющийся неоновый золотой ореол под щитом */}
                <motion.circle
                  cx="0"
                  cy="0"
                  r="80"
                  fill="radial-gradient(circle, rgba(212,162,76,0.4) 0%, transparent 70%)"
                  initial={{ scale: 0, opacity: 0 }}
                  animate={{ scale: [0, 1.4, 1.1], opacity: [0, 0.9, 0.6] }}
                  transition={{ duration: 0.5, ease: 'easeOut' }}
                />

                {/* Сам оригинальный 3D-логотип Hoplyra в центре */}
                <motion.image
                  href={logoCandidate}
                  x="-70"
                  y="-70"
                  width="140"
                  height="140"
                  initial={{ scale: 0, opacity: 0 }}
                  animate={{ scale: [0, 1.25, 1], opacity: 1 }}
                  transition={{ duration: 0.5, ease: 'backOut' }}
                  className="drop-shadow-[0_0_35px_rgba(212,162,76,0.9)]"
                />

                {/* Подпись щита */}
                <motion.text
                  x="0"
                  y="95"
                  textAnchor="middle"
                  fill="#FDE68A"
                  fontSize="12"
                  fontWeight="800"
                  letterSpacing="3"
                  initial={{ opacity: 0, y: 5 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.3, delay: 0.35 }}
                >
                  HOPLYRA
                </motion.text>
              </g>
            </svg>

            {/* Финальная мягкая золотая вспышка шлюза */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: [0, 0.85, 0] }}
              transition={{ duration: 0.6, delay: 2.2 }}
              className="absolute inset-0 bg-gradient-to-t from-amber-500/25 via-accent-cyan/20 to-transparent pointer-events-none"
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
              disabled={submitting || loading || igniting}
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
              disabled={submitting || loading || twoFactorNeeded || igniting}
              required
            />
            {twoFactorNeeded && (
              <Input
                label={t('auth.twoFactorCode')}
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
              {submitting || igniting ? t('auth.signingIn') : twoFactorNeeded ? t('auth.verify') : t('auth.signIn')}
            </Button>
            {twoFactorNeeded && (
              <button
                type="button"
                onClick={() => { setTwoFactorNeeded(false); setTotpCode(''); setError(null) }}
                className="w-full text-center text-xs text-text-muted hover:text-zinc-300 transition-colors pt-1 cursor-pointer"
              >
                {t('common.cancel')}
              </button>
            )}
          </form>
      </motion.section>
    </main>
  )
}
