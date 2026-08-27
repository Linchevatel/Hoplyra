import { useState, useRef, useLayoutEffect } from 'react'
import { createPortal } from 'react-dom'
import { Bell, Check, X } from 'lucide-react'
import { useDashboard } from '@/lib/dashboard'
import { useI18n } from '@/i18n/I18nProvider'
import { cn } from '@/lib/utils'

const PANEL_MARGIN = 12
const PANEL_WIDTH = 320

function computePanelPosition(rect: DOMRect) {
  const vw = window.innerWidth
  const vh = window.innerHeight
  const width = Math.min(PANEL_WIDTH, vw - PANEL_MARGIN * 2)

  let left = rect.right - width
  left = Math.max(PANEL_MARGIN, Math.min(left, vw - width - PANEL_MARGIN))

  const spaceBelow = vh - rect.bottom - PANEL_MARGIN
  const maxHeight = Math.min(384, Math.max(160, spaceBelow - PANEL_MARGIN))
  let top = rect.bottom + PANEL_MARGIN

  if (spaceBelow < 180 && rect.top > maxHeight + PANEL_MARGIN * 2) {
    top = rect.top - maxHeight - PANEL_MARGIN
  }

  top = Math.max(PANEL_MARGIN, Math.min(top, vh - maxHeight - PANEL_MARGIN))

  return { top, left, width, maxHeight }
}

export function NotificationBell() {
  const { t, formatRelativeTime } = useI18n()
  const {
    notifications,
    unreadNotifications,
    markNotificationRead,
    markAllNotificationsRead,
    dismissNotification,
    clearAllNotifications,
  } = useDashboard()
  const [open, setOpen] = useState(false)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const [panelStyle, setPanelStyle] = useState({
    top: PANEL_MARGIN,
    left: PANEL_MARGIN,
    width: PANEL_WIDTH,
    maxHeight: 384,
  })

  useLayoutEffect(() => {
    if (!open || !buttonRef.current) return

    function updatePosition() {
      const button = buttonRef.current
      if (!button) return
      setPanelStyle(computePanelPosition(button.getBoundingClientRect()))
    }

    updatePosition()
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    return () => {
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [open])

  const panel =
    open &&
    createPortal(
      <>
        <div className="fixed inset-0 z-[100]" onClick={() => setOpen(false)} aria-hidden />
        <div
          role="dialog"
          aria-label={t('dashboard.notifications.title')}
          className="fixed z-[101] box-border overflow-x-hidden overflow-y-auto dropdown-panel rounded-xl p-2 shadow-xl"
          style={{
            top: panelStyle.top,
            left: panelStyle.left,
            width: panelStyle.width,
            maxWidth: `calc(100vw - ${PANEL_MARGIN * 2}px)`,
            maxHeight: panelStyle.maxHeight,
          }}
        >
            <div className="flex items-center justify-between px-2 py-1.5 mb-1 gap-2 min-w-0">
              <span className="text-xs font-semibold text-zinc-400 uppercase tracking-wider truncate">
                {t('dashboard.notifications.title')}
              </span>
              <div className="flex items-center gap-2 shrink-0">
                {unreadNotifications > 0 && (
                  <button
                    type="button"
                    onClick={markAllNotificationsRead}
                    className="text-[11px] text-cyan-400 hover:text-cyan-300 cursor-pointer flex items-center gap-1 whitespace-nowrap"
                  >
                    <Check size={12} />
                    {t('common.markAllRead')}
                  </button>
                )}
                {notifications.length > 0 && (
                  <button
                    type="button"
                    onClick={clearAllNotifications}
                    className="text-[11px] text-zinc-500 hover:text-red-400 cursor-pointer whitespace-nowrap"
                  >
                    {t('common.clear')}
                  </button>
                )}
              </div>
            </div>

          {notifications.length === 0 ? (
            <p className="text-xs text-zinc-500 text-center py-6 px-2">{t('dashboard.notifications.empty')}</p>
          ) : (
            notifications.slice(0, 20).map((n) => (
              <div
                key={n.id}
                className={cn(
                  'group grid grid-cols-[minmax(0,1fr)_auto] gap-2 p-2.5 rounded-lg mb-1 last:mb-0 min-w-0',
                  n.read ? 'opacity-70' : 'bg-cyan-500/5 border border-cyan-500/10',
                )}
              >
                <div className="min-w-0 overflow-hidden">
                  <p className="text-xs font-medium text-zinc-200 [overflow-wrap:anywhere] break-words">
                    {n.title}
                  </p>
                  <p className="text-[11px] text-zinc-500 mt-0.5 [overflow-wrap:anywhere] break-words leading-relaxed">
                    {n.message}
                  </p>
                  <p className="text-[10px] text-zinc-600 mt-1">{formatRelativeTime(n.createdAt)}</p>
                </div>
                <div className="flex flex-col gap-1 shrink-0">
                  {!n.read && (
                    <button
                      type="button"
                      onClick={() => markNotificationRead(n.id)}
                      className="p-1 text-zinc-600 hover:text-cyan-400 cursor-pointer"
                      aria-label={t('common.markRead')}
                    >
                      <Check size={12} />
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => dismissNotification(n.id)}
                    className="p-1 text-zinc-600 hover:text-red-400 cursor-pointer opacity-100 sm:opacity-0 sm:group-hover:opacity-100"
                    aria-label={t('dashboard.notifications.dismiss')}
                  >
                    <X size={12} />
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </>,
      document.body,
    )

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="relative p-2 rounded-lg text-zinc-400 hover:text-white hover:bg-white/5 transition-colors cursor-pointer"
        aria-label={t('dashboard.notifications.title')}
        aria-expanded={open}
      >
        <Bell size={18} />
        {unreadNotifications > 0 && (
          <span className="absolute top-1 right-1 min-w-[16px] h-4 px-1 rounded-full bg-cyan-500 text-[10px] font-bold text-black flex items-center justify-center">
            {unreadNotifications > 9 ? '9+' : unreadNotifications}
          </span>
        )}
      </button>
      {panel}
    </>
  )
}
