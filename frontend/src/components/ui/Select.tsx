import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { Check, ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useI18n } from '@/i18n/I18nProvider'

export interface SelectOption {
  value: string
  label: string
  description?: string
  meta?: string
  disabled?: boolean
  icon?: ReactNode
  leading?: ReactNode
}

interface MenuPosition {
  top: number
  left: number
  width: number
  maxHeight: number
  placement: 'bottom' | 'top'
}

interface SelectProps {
  value: string
  onChange: (value: string) => void
  options: SelectOption[]
  placeholder?: string
  label?: string
  disabled?: boolean
  emptyMessage?: string
  className?: string
  onPointerDown?: (e: React.PointerEvent) => void
  renderOption?: (option: SelectOption, selected: boolean) => ReactNode
  renderValue?: (option: SelectOption | undefined) => ReactNode
}

const MENU_GAP = 8
const VIEWPORT_PADDING = 12

function computeMenuPosition(trigger: HTMLElement): MenuPosition {
  const rect = trigger.getBoundingClientRect()
  const viewportHeight = window.innerHeight
  const spaceBelow = viewportHeight - rect.bottom - VIEWPORT_PADDING
  const spaceAbove = rect.top - VIEWPORT_PADDING
  const preferredMax = 256
  const openUp = spaceBelow < 180 && spaceAbove > spaceBelow

  if (openUp) {
    const maxHeight = Math.min(preferredMax, spaceAbove - MENU_GAP)
    return {
      top: rect.top - MENU_GAP,
      left: rect.left,
      width: rect.width,
      maxHeight,
      placement: 'top',
    }
  }

  const maxHeight = Math.min(preferredMax, spaceBelow - MENU_GAP)
  return {
    top: rect.bottom + MENU_GAP,
    left: rect.left,
    width: rect.width,
    maxHeight,
    placement: 'bottom',
  }
}

export function Select({
  value,
  onChange,
  options,
  placeholder,
  label,
  disabled,
  emptyMessage,
  className,
  onPointerDown,
  renderOption,
  renderValue,
}: SelectProps) {
  const { t } = useI18n()
  const resolvedPlaceholder = placeholder ?? t('common.select')
  const resolvedEmptyMessage = emptyMessage ?? t('common.noOptions')
  const [open, setOpen] = useState(false)
  const [menuStyle, setMenuStyle] = useState<MenuPosition | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const listId = useId()

  const selected = options.find((o) => o.value === value)

  const updatePosition = useCallback(() => {
    if (!triggerRef.current) return
    setMenuStyle(computeMenuPosition(triggerRef.current))
  }, [])

  useLayoutEffect(() => {
    if (!open) return
    updatePosition()
  }, [open, updatePosition, options.length])

  useEffect(() => {
    if (!open) return

    function handleClickOutside(e: MouseEvent) {
      const target = e.target as Node
      if (
        rootRef.current?.contains(target) ||
        menuRef.current?.contains(target)
      ) {
        return
      }
      setOpen(false)
    }

    function handleEscape(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }

    function handleReposition() {
      updatePosition()
    }

    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleEscape)
    window.addEventListener('resize', handleReposition)
    window.addEventListener('scroll', handleReposition, true)

    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleEscape)
      window.removeEventListener('resize', handleReposition)
      window.removeEventListener('scroll', handleReposition, true)
    }
  }, [open, updatePosition])

  function selectOption(option: SelectOption) {
    if (option.disabled) return
    onChange(option.value)
    setOpen(false)
  }

  const menu =
    open &&
    menuStyle &&
    createPortal(
      <AnimatePresence>
        <motion.div
          ref={menuRef}
          initial={{
            opacity: 0,
            y: menuStyle.placement === 'bottom' ? -6 : 6,
            scale: 0.98,
          }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{
            opacity: 0,
            y: menuStyle.placement === 'bottom' ? -6 : 6,
            scale: 0.98,
          }}
          transition={{ duration: 0.15 }}
          style={{
            position: 'fixed',
            top: menuStyle.placement === 'bottom' ? menuStyle.top : undefined,
            bottom:
              menuStyle.placement === 'top'
                ? window.innerHeight - menuStyle.top
                : undefined,
            left: menuStyle.left,
            width: menuStyle.width,
            maxHeight: menuStyle.maxHeight,
            zIndex: 9999,
          }}
          className="dropdown-panel p-1.5 rounded-xl overflow-y-auto"
          role="listbox"
          id={listId}
          onPointerDown={(e) => e.stopPropagation()}
        >
          {options.length === 0 ? (
            <p className="px-3 py-4 text-sm text-zinc-500 text-center">{resolvedEmptyMessage}</p>
          ) : (
            options.map((option) => {
              const isSelected = option.value === value
              return (
                <button
                  key={option.value}
                  type="button"
                  role="option"
                  aria-selected={isSelected}
                  disabled={option.disabled}
                  onClick={() => selectOption(option)}
                  className={cn(
                    'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left transition-all',
                    isSelected
                      ? 'bg-gradient-to-r from-cyan-500/15 to-violet-500/15 border border-cyan-500/20 shadow-sm shadow-cyan-500/5'
                      : 'hover:bg-white/[0.06] hover:border-white/5 border border-transparent',
                    option.disabled && 'opacity-40 cursor-not-allowed',
                    !option.disabled && 'cursor-pointer',
                  )}
                >
                  {renderOption ? (
                    renderOption(option, isSelected)
                  ) : (
                    <DefaultOption option={option} selected={isSelected} />
                  )}
                </button>
              )
            })
          )}
        </motion.div>
      </AnimatePresence>,
      document.body,
    )

  return (
    <div ref={rootRef} className={cn('relative', className)}>
      {label && <label className="block text-xs text-zinc-500 mb-1.5">{label}</label>}

      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        onPointerDown={onPointerDown}
        onClick={() => {
          if (disabled) return
          setOpen((v) => !v)
        }}
        className={cn(
          'w-full flex items-center justify-between gap-2 px-3 py-2.5 rounded-xl',
          'bg-white/[0.04] glow-border text-left text-sm transition-all touch-auto',
          open
            ? 'border-cyan-500/35 ring-2 ring-cyan-500/15 bg-gradient-to-r from-cyan-500/8 to-violet-500/8 shadow-[0_0_0_1px_rgba(34,211,238,0.1),0_4px_24px_rgba(34,211,238,0.08)]'
            : 'glow-border-hover',
          disabled && 'opacity-50 cursor-not-allowed',
          !disabled && 'cursor-pointer',
        )}
      >
        <span className="flex-1 min-w-0">
          {renderValue ? (
            renderValue(selected)
          ) : selected ? (
            <DefaultTrigger option={selected} />
          ) : (
            <span className="text-zinc-500">{resolvedPlaceholder}</span>
          )}
        </span>
        <ChevronDown
          size={16}
          className={cn(
            'text-zinc-500 shrink-0 transition-transform duration-200',
            open && 'rotate-180 text-violet-400',
          )}
        />
      </button>

      {menu}
    </div>
  )
}

function DefaultTrigger({ option }: { option: SelectOption }) {
  return (
    <span className="flex items-center gap-2 min-w-0">
      {option.leading}
      <span className="truncate text-zinc-200">{option.label}</span>
    </span>
  )
}

function DefaultOption({ option, selected }: { option: SelectOption; selected: boolean }) {
  return (
    <>
      <span className="flex-1 min-w-0">
        <span className="flex items-center gap-2">
          {option.leading}
          <span className={cn('text-sm truncate', selected ? 'text-white' : 'text-zinc-200')}>
            {option.label}
          </span>
        </span>
        {(option.description || option.meta) && (
          <span className="flex items-center gap-2 mt-0.5">
            {option.description && (
              <span className="text-xs text-zinc-500 truncate">{option.description}</span>
            )}
            {option.meta && (
              <span className="text-[11px] text-zinc-600 font-mono truncate">{option.meta}</span>
            )}
          </span>
        )}
      </span>
      {selected && <Check size={16} className="text-cyan-400 shrink-0" />}
    </>
  )
}
