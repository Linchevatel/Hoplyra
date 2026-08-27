import type { LucideIcon } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'
import { LayoutGroup, motion } from 'framer-motion'
import {
  LayoutDashboard,
  Server,
  Shield,
  GitBranch,
  Globe,
  ChevronLeft,
  Menu,
  FileKey,
  Activity,
  LogOut,
  Settings,
} from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Logo } from './Logo'
import { NotificationBell } from '@/components/dashboard/NotificationBell'
import { LanguageSwitcher } from '@/i18n/LanguageSwitcher'
import { useI18n } from '@/i18n/I18nProvider'
import { useAuth } from '@/lib/auth'
import { isDesktopApp } from '@/lib/desktop'
import { cn } from '@/lib/utils'

type NavItemDef = {
  path: string
  label: string
  icon: LucideIcon
  exact?: boolean
}

type NavGroupDef = {
  id: 'infrastructure' | 'connections' | 'manage'
  label: string
  accent: 'cyan' | 'violet' | 'neutral'
  items: NavItemDef[]
}

const groupAccentStyles = {
  cyan: {
    panel: 'nav-group nav-group-cyan',
    bar: 'bg-gradient-to-b from-cyan-400/80 to-cyan-400/20',
    label: 'text-cyan-500/70',
  },
  violet: {
    panel: 'nav-group nav-group-violet',
    bar: 'bg-gradient-to-b from-violet-400/80 to-violet-400/20',
    label: 'text-violet-400/70',
  },
  neutral: {
    panel: 'nav-group nav-group-neutral',
    bar: 'bg-gradient-to-b from-zinc-400/50 to-zinc-500/10',
    label: 'text-zinc-500',
  },
} as const

const navActiveTransition = { type: 'spring' as const, stiffness: 420, damping: 34 }

function navActiveSurface(accent: NavGroupDef['accent']) {
  if (accent === 'violet') {
    return 'border border-violet-500/20 bg-gradient-to-r from-violet-500/14 via-violet-500/8 to-cyan-500/5 shadow-[0_0_20px_rgba(167,139,250,0.12)]'
  }
  if (accent === 'cyan') {
    return 'border border-cyan-500/20 bg-gradient-to-r from-cyan-500/14 via-cyan-500/8 to-violet-500/5 shadow-[0_0_20px_rgba(34,211,238,0.1)]'
  }
  return 'border border-white/10 bg-white/[0.06] shadow-[0_0_12px_rgba(255,255,255,0.04)]'
}

function NavLink({
  item,
  active,
  accent,
  onNavigate,
  animateNav,
}: {
  item: NavItemDef
  active: boolean
  accent: NavGroupDef['accent']
  onNavigate?: () => void
  animateNav: boolean
}) {
  const Icon = item.icon
  const activeIconClass =
    accent === 'violet'
      ? 'bg-gradient-to-br from-violet-500/25 to-cyan-500/15 text-violet-200 shadow-[0_0_12px_rgba(167,139,250,0.25)]'
      : accent === 'cyan'
        ? 'bg-gradient-to-br from-cyan-500/25 to-violet-500/10 text-cyan-200 shadow-[0_0_12px_rgba(34,211,238,0.2)]'
        : 'bg-gradient-to-br from-zinc-400/15 to-zinc-500/5 text-zinc-200'

  return (
    <Link
      to={item.path}
      onClick={onNavigate}
      className={cn(
        'nav-item group/nav relative overflow-hidden',
        active && !animateNav && 'nav-item-active',
        active && !animateNav && accent === 'violet' && 'nav-item-active-violet',
        !active && 'hover:text-white hover:bg-white/[0.04]',
      )}
    >
      {animateNav && active && (
        <motion.span
          layoutId={`nav-active-${accent}`}
          className={cn('absolute inset-0 rounded-[10px]', navActiveSurface(accent))}
          transition={navActiveTransition}
        />
      )}
      <span className="relative z-10 flex flex-1 min-w-0 items-center gap-3">
        <span
          className={cn(
            'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-all duration-200',
            active
              ? activeIconClass
              : 'bg-white/[0.04] text-zinc-500 group-hover/nav:text-zinc-300 group-hover/nav:bg-white/[0.07]',
          )}
        >
          <Icon size={16} />
        </span>
        <span className="flex-1 truncate">{item.label}</span>
        {active && (
          <span
            className={cn(
              'h-1.5 w-1.5 shrink-0 rounded-full',
              accent === 'violet' && 'bg-violet-400 shadow-[0_0_8px_rgba(167,139,250,0.8)]',
              accent === 'cyan' && 'bg-cyan-400 shadow-[0_0_8px_rgba(34,211,238,0.8)]',
              accent === 'neutral' && 'bg-zinc-400 shadow-[0_0_6px_rgba(161,161,170,0.5)]',
            )}
          />
        )}
      </span>
    </Link>
  )
}

function SidebarNav({
  onNavigate,
  isActive,
}: {
  onNavigate?: () => void
  isActive: (path: string, exact?: boolean) => boolean
}) {
  const { t } = useI18n()
  const animateNav = isDesktopApp()

  const navGroups: NavGroupDef[] = [
    {
      id: 'infrastructure',
      label: t('nav.sections.infrastructure'),
      accent: 'cyan',
      items: [
        { path: '/', label: t('nav.overview'), icon: LayoutDashboard, exact: true },
        { path: '/servers', label: t('nav.servers'), icon: Server },
        { path: '/status', label: t('nav.status'), icon: Activity },
      ],
    },
    {
      id: 'connections',
      label: t('nav.sections.connections'),
      accent: 'violet',
      items: [
        { path: '/vpn', label: 'VPN', icon: Shield },
        { path: '/chains', label: t('nav.chains'), icon: GitBranch },
        { path: '/proxy', label: t('nav.proxy'), icon: Globe },
      ],
    },
    {
      id: 'manage',
      label: t('nav.sections.manage'),
      accent: 'neutral',
      items: [
        { path: '/configs', label: t('nav.configs'), icon: FileKey },
        { path: '/settings', label: t('nav.settings'), icon: Settings },
      ],
    },
  ]

  return (
    <LayoutGroup>
      <nav className="space-y-3">
        {navGroups.map((group) => {
          const styles = groupAccentStyles[group.accent]
          const groupActive = group.items.some((item) => isActive(item.path, item.exact))

          return (
            <div key={group.id} className={cn(styles.panel, groupActive && 'nav-group-active')}>
              <div className="flex items-center gap-2 px-2.5 pt-2 pb-1.5">
                <span className={cn('h-3.5 w-0.5 shrink-0 rounded-full', styles.bar)} />
                <p className={cn('text-[10px] font-semibold uppercase tracking-[0.18em]', styles.label)}>
                  {group.label}
                </p>
              </div>
              <div className="space-y-0.5 px-1 pb-1">
                {group.items.map((item) => (
                  <NavLink
                    key={item.path}
                    item={item}
                    accent={group.accent}
                    active={isActive(item.path, item.exact)}
                    onNavigate={onNavigate}
                    animateNav={animateNav}
                  />
                ))}
              </div>
            </div>
          )
        })}
      </nav>
    </LayoutGroup>
  )
}

export function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { t } = useI18n()
  const { logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [sidebarOpen, setSidebarOpen] = useState(false)

  async function handleLogout() {
    await logout()
    navigate('/login', { replace: true })
  }

  const exitLabel = t('auth.logout')

  function isActive(path: string, exact?: boolean) {
    if (exact) return location.pathname === path
    return location.pathname.startsWith(path)
  }

  return (
    <div className="min-h-screen mesh-bg flex">
      <aside className="hidden lg:flex flex-col w-[18rem] border-r border-white/[0.06] bg-surface-900/40 backdrop-blur-xl relative">
        <div className="pointer-events-none absolute inset-y-0 right-0 w-px bg-gradient-to-b from-transparent via-cyan-400/20 to-violet-400/20" />
        <div className="flex items-center px-5 py-5 border-b border-white/[0.06] bg-gradient-to-b from-white/[0.03] to-transparent">
          <Logo size="sm" showSlogan />
        </div>

        <div className="flex-1 p-3 overflow-y-auto sidebar-scroll">
          <SidebarNav isActive={isActive} />
        </div>
      </aside>

      {sidebarOpen && (
        <div
          className="lg:hidden fixed inset-0 z-40 bg-black/60 backdrop-blur-sm"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <aside
        className={cn(
          'lg:hidden fixed inset-y-0 left-0 z-50 w-[18rem] bg-surface-900/95 backdrop-blur-xl border-r border-white/[0.06] transform transition-transform',
          sidebarOpen ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        <div className="flex items-center justify-between px-5 py-5 border-b border-white/[0.06] bg-gradient-to-b from-white/[0.03] to-transparent">
          <Logo size="sm" showSlogan />
          <button
            onClick={() => setSidebarOpen(false)}
            className="p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-white/5 cursor-pointer"
            aria-label={t('nav.closeMenu')}
          >
            <ChevronLeft size={20} />
          </button>
        </div>
        <div className="flex flex-col flex-1 min-h-0">
          <div className="flex-1 p-3 overflow-y-auto">
            <SidebarNav isActive={isActive} onNavigate={() => setSidebarOpen(false)} />
          </div>
        </div>
      </aside>

      <div className="flex-1 flex flex-col min-h-screen">
        <header className="sticky top-0 z-30 flex items-center h-14 px-4 sm:px-6 lg:px-8 border-b border-white/5 bg-surface-950/80 backdrop-blur-xl gap-3">
          <button
            onClick={() => setSidebarOpen(true)}
            className="lg:hidden p-2 -ml-2 rounded-lg text-zinc-400 hover:text-white hover:bg-white/5 cursor-pointer"
            aria-label={t('nav.openMenu')}
          >
            <Menu size={20} />
          </button>

          <Logo size="sm" showText={false} className="lg:hidden shrink-0" />

          <div className="flex-1" />

          <div className="flex items-center gap-2 shrink-0">
            <LanguageSwitcher />
            <NotificationBell />
            <button
              type="button"
              onClick={() => void handleLogout()}
              className="p-2 rounded-lg text-zinc-400 hover:text-white hover:bg-white/5 cursor-pointer"
              aria-label={exitLabel}
              title={exitLabel}
            >
              <LogOut size={18} />
            </button>
          </div>
        </header>

        <main className="flex-1 p-4 sm:p-6 lg:p-8 overflow-auto">{children}</main>
      </div>
    </div>
  )
}
