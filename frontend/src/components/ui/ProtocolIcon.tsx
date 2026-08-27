import type { Protocol } from '@/lib/types'
import { PROTOCOLS } from '@/lib/constants'
import { cn } from '@/lib/utils'

export const PROTOCOL_ICON_PATH: Record<Protocol, string> = {
  awg: '/protocols/awg.svg',
  wg: '/protocols/wg.svg',
  openvpn: '/protocols/openvpn.svg',
  xray: '/protocols/xray.svg',
  tor: '/protocols/tor.svg',
  hysteria2: '/protocols/hysteria2.svg',
  tuic: '/protocols/tuic.svg',
}

const SIZE = {
  xs: { box: 'w-5 h-5', icon: 'w-3 h-3', rounded: 'rounded' },
  sm: { box: 'w-7 h-7', icon: 'w-4 h-4', rounded: 'rounded-md' },
  md: { box: 'w-8 h-8', icon: 'w-5 h-5', rounded: 'rounded-lg' },
  lg: { box: 'w-10 h-10', icon: 'w-6 h-6', rounded: 'rounded-lg' },
  xl: { box: 'w-12 h-12', icon: 'w-7 h-7', rounded: 'rounded-xl' },
} as const

type ProtocolIconSize = keyof typeof SIZE

interface ProtocolIconProps {
  protocol: Protocol
  size?: ProtocolIconSize
  /** К img; при withGradient — к обёртке */
  className?: string
  withGradient?: boolean
  title?: string
}

export function ProtocolIcon({
  protocol,
  size = 'md',
  className,
  withGradient = false,
  title,
}: ProtocolIconProps) {
  const meta = PROTOCOLS[protocol]
  const s = SIZE[size]

  const icon = (
    <img
      src={PROTOCOL_ICON_PATH[protocol]}
      alt={meta.label}
      title={title ?? meta.label}
      className={cn(s.icon, 'object-contain pointer-events-none', !withGradient && className)}
      draggable={false}
    />
  )

  if (withGradient) {
    return (
      <span
        className={cn(
          'flex shrink-0 items-center justify-center bg-gradient-to-br',
          meta.color,
          s.box,
          s.rounded,
          className,
        )}
        title={title ?? meta.label}
      >
        {icon}
      </span>
    )
  }

  return icon
}
