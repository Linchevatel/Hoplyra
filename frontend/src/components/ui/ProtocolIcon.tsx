import type { Protocol } from '@/lib/types'
import { PROTOCOLS } from '@/lib/constants'
import { cn } from '@/lib/utils'

import wireguard from '@/assets/icons/wireguard.png'
import amneziawg from '@/assets/icons/amneziawg.png'
import openvpn from '@/assets/icons/openvpn.png'
import tor from '@/assets/icons/tor.png'
import xray from '@/assets/icons/xray.png'
import vless from '@/assets/icons/vless.png'
import socks from '@/assets/icons/socks.png'
import chain from '@/assets/icons/chain.png'
import hysteria2 from '@/assets/icons/hysteria2.png'
import tuic from '@/assets/icons/tuic.png'

type IconProtocol = Protocol | 'vless' | 'socks' | 'chain'

const EXTRA_LABELS = { vless: 'VLESS', socks: 'SOCKS', chain: 'Multi-hop' } as const

const PROTOCOL_ICON_PATH: Record<IconProtocol, string> = {
  awg: amneziawg,
  wg: wireguard,
  openvpn: openvpn,
  xray: xray,
  tor: tor,
  vless,
  socks,
  chain,
  hysteria2,
  tuic,
}

const SIZE = {
  xs: { box: 'w-5 h-5', icon: 'w-5 h-5', rounded: 'rounded' },
  sm: { box: 'w-7 h-7', icon: 'w-7 h-7', rounded: 'rounded-md' },
  md: { box: 'w-10 h-10', icon: 'w-10 h-10', rounded: 'rounded-lg' },
  lg: { box: 'w-10 h-10', icon: 'w-10 h-10', rounded: 'rounded-lg' },
  xl: { box: 'w-12 h-12', icon: 'w-12 h-12', rounded: 'rounded-xl' },
} as const

type ProtocolIconSize = keyof typeof SIZE

interface ProtocolIconProps {
  protocol: IconProtocol
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
  const meta = protocol in EXTRA_LABELS
    ? { label: EXTRA_LABELS[protocol as keyof typeof EXTRA_LABELS], color: '' }
    : PROTOCOLS[protocol as Protocol]
  const isAppIcon = true
  const s = SIZE[size]

  const icon = (
    <img
      src={PROTOCOL_ICON_PATH[protocol]}
      alt={meta.label}
      title={title ?? meta.label}
      className={cn(s.icon, 'object-contain pointer-events-none shrink-0', !withGradient && className)}
      draggable={false}
    />
  )

  if (withGradient) {
    return (
      <span
        className={cn(
          'flex shrink-0 items-center justify-center',
          !isAppIcon && 'bg-gradient-to-br',
          !isAppIcon && meta.color,
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
