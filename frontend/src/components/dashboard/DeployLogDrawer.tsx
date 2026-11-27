import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { X } from 'lucide-react'
import { useDeployLogDrawer } from '@/lib/deploy-log-context'
import { deployLogOutcome } from '@/lib/deploy-log-state'
import { useI18n } from '@/i18n/i18n-context'
import { cn } from '@/lib/utils'

export function DeployLogDrawer() {
  const { t } = useI18n()
  const log = useDeployLogDrawer()
  const reduced = useReducedMotion()
  const body = useRef<HTMLDivElement>(null)
  const follow = useRef(true)
  const [paused, setPaused] = useState(false)
  const [copied, setCopied] = useState(false)
  const outcome = deployLogOutcome(log)
  const failed = outcome === 'error' || outcome === 'cancelled'
  const { expand } = log
  useEffect(() => { if (failed) expand() }, [failed, expand])
  useEffect(() => {
    if (follow.current && body.current) body.current.scrollTop = body.current.scrollHeight
  }, [log.events, log.isCollapsed])
  useEffect(() => {
    follow.current = true
    setPaused(false)
  }, [log.target?.configId])
  useEffect(() => {
    if (!copied) return
    const timer = setTimeout(() => setCopied(false), 2000)
    return () => clearTimeout(timer)
  }, [copied])
  const latest = () => {
    follow.current = true; setPaused(false)
    if (body.current) body.current.scrollTop = body.current.scrollHeight
  }
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(log.events.map((event) => `${new Date(event.ts * 1000).toLocaleTimeString()} [${event.level}] ${event.stage ? `[${event.stage}] ` : ''}${event.message}`).join('\n'))
      setCopied(true)
    } catch { setCopied(false) }
  }
  return <AnimatePresence>
    {log.isOpen && (log.isCollapsed ? (
      <motion.button key="pill" type="button" onClick={log.expand} aria-label={t('deployLog.expand')}
        className={cn(
          'fixed bottom-4 right-4 z-50 flex items-center gap-2 rounded-full border border-white/20 bg-surface-900/95 backdrop-blur px-4 py-2.5 shadow-2xl text-xs font-medium cursor-pointer hover:bg-surface-800 transition-colors',
          failed && 'text-rose-400 border-rose-500/30 bg-rose-950/80',
          outcome === 'ok' && 'text-emerald-400 border-emerald-500/30',
        )}
        initial={false} animate={{ opacity: 1 }}>
        {outcome === 'ok' ? '✓ ' : failed ? '✕ ' : <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />}
        <span>{t(`deployLog.status.${outcome}`)} · {log.events.length}</span>
      </motion.button>
    ) : (
      <motion.aside key="drawer" role="region" aria-label={t('deployLog.title')}
        className="fixed inset-y-0 right-0 z-50 flex w-full sm:w-[420px] flex-col border-l border-white/10 bg-surface-950 shadow-2xl"
        initial={reduced ? false : { x: 460, opacity: 0 }} animate={{ x: 0, opacity: 1 }}
        exit={reduced ? { opacity: 0 } : { x: 460, opacity: 0 }}
        transition={reduced ? { duration: 0 } : { type: 'spring', stiffness: 260, damping: 30 }}>
        <header className={cn('p-4 border-b border-white/10 flex items-start gap-3', failed && 'bg-rose-950/20 border-rose-500/20 text-rose-400')}>
          <div className="min-w-0 flex-1">
            <h2 className="font-semibold text-sm">{t('deployLog.title')} · {log.target?.title}</h2>
            <p className="text-xs text-text-muted truncate mt-0.5">{log.target?.subtitle}</p>
            <div className="mt-2">
              <span role="status" className={cn(
                'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium',
                outcome === 'deploying' && 'bg-amber-500/10 text-amber-300 border border-amber-500/20',
                outcome === 'ok' && 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20',
                failed && 'bg-rose-500/10 text-rose-400 border border-rose-500/20',
              )}>
                {outcome === 'deploying' && <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />}
                {outcome === 'ok' && <span>✓</span>}
                {failed && <span>✕</span>}
                {t(`deployLog.status.${outcome}`)}
              </span>
            </div>
          </div>
          <button type="button" onClick={log.close} aria-label={t('common.close')} className="p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-white/5 cursor-pointer transition-colors"><X size={18} /></button>
        </header>
        <div ref={body} className="flex-1 overflow-y-auto p-4 font-mono text-xs space-y-2 bg-black/30" onScroll={() => {
          const node = body.current
          if (node) { follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 48; setPaused(!follow.current) }
        }}>
          {log.events.length === 0 && <p className="text-text-muted">{t('deployLog.empty')}</p>}
          {log.events.map((event) => <p key={event.seq} className={cn('whitespace-pre-wrap break-words leading-relaxed', event.level === 'warn' && 'text-amber-300', event.level === 'error' && 'text-rose-400', event.level === 'success' && 'text-emerald-400')}>
            <span className="text-text-muted/70">{new Date(event.ts * 1000).toLocaleTimeString()} </span>{event.stage && <span className="text-accent-cyan/80">[{event.stage}] </span>}{event.message}
          </p>)}
        </div>
        {paused && (
          <button type="button" onClick={latest} className="w-full py-1.5 bg-accent-cyan/10 hover:bg-accent-cyan/20 border-t border-accent-cyan/20 text-accent-cyan text-xs font-medium text-center transition-colors cursor-pointer">
            {t('deployLog.toLatest')} ↓
          </button>
        )}
        <footer className="flex items-center justify-between gap-3 p-4 border-t border-white/10 bg-surface-900/50 text-sm">
          <button type="button" onClick={log.collapse} className="px-3 py-1.5 rounded-lg border border-white/10 hover:bg-white/5 text-zinc-300 text-xs transition-colors cursor-pointer">
            {t('deployLog.collapse')}
          </button>
          <button type="button" onClick={() => void copy()} className="px-3 py-1.5 rounded-lg border border-white/10 hover:bg-white/5 text-zinc-300 text-xs transition-colors cursor-pointer flex items-center gap-1.5">
            {copied ? '✓ ' : ''}{t(copied ? 'deployLog.copied' : 'deployLog.copy')}
          </button>
        </footer>
      </motion.aside>
    ))}
  </AnimatePresence>
}
