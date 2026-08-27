import { useId } from 'react'
import { cn } from '@/lib/utils'

interface HoplyraMarkProps {
  size?: number
  className?: string
}

const HEX =
  'M16 2.75 L27.35 9.25 V22.25 L16 28.75 L4.65 22.25 V9.25 Z'

const HOP_PATH = 'M8.5 24.5 H14.25 V16.75 H20.75 L23.75 9.25'

const NODES = [
  { cx: 8.5, cy: 24.5, r: 3.1, halo: 7.5, inner: '#22d3ee', outer: '#0369a1' },
  { cx: 14.25, cy: 16.75, r: 2.9, halo: 6.5, inner: '#5eead4', outer: '#0f766e' },
  { cx: 23.75, cy: 9.25, r: 3.3, halo: 8.5, inner: '#c4b5fd', outer: '#7c3aed' },
] as const

/** Hex badge with glowing hop pipeline */
export function HoplyraMark({ size = 24, className }: HoplyraMarkProps) {
  const id = useId().replace(/:/g, '')
  const grad = `grad-${id}`
  const fill = `fill-${id}`
  const glow = `glow-${id}`
  const bloom = `bloom-${id}`

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={cn('shrink-0', className)}
      aria-hidden
    >
      <defs>
        <linearGradient id={grad} x1="4" y1="3" x2="28" y2="29" gradientUnits="userSpaceOnUse">
          <stop stopColor="#22d3ee" />
          <stop offset="0.5" stopColor="#2dd4bf" />
          <stop offset="1" stopColor="#a78bfa" />
        </linearGradient>
        <linearGradient id={fill} x1="16" y1="3" x2="16" y2="29" gradientUnits="userSpaceOnUse">
          <stop stopColor="#22d3ee" stopOpacity="0.14" />
          <stop offset="1" stopColor="#7c3aed" stopOpacity="0.08" />
        </linearGradient>
        <filter id={glow} x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="2.2" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <filter id={bloom} x="-80%" y="-80%" width="260%" height="260%">
          <feGaussianBlur stdDeviation="1.6" result="blur" />
          <feColorMatrix
            in="blur"
            type="matrix"
            values="0 0 0 0 0.13  0 0 0 0 0.83  0 0 0 0 0.93  0 0 0 0.65 0"
            result="cyanBlur"
          />
          <feMerge>
            <feMergeNode in="cyanBlur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        {NODES.map((node, i) => (
          <radialGradient
            key={node.cx}
            id={`${id}-node-${i}`}
            cx="32%"
            cy="28%"
            r="72%"
          >
            <stop offset="0%" stopColor="#ffffff" stopOpacity="0.95" />
            <stop offset="35%" stopColor={node.inner} />
            <stop offset="100%" stopColor={node.outer} />
          </radialGradient>
        ))}
      </defs>

      <path d={HEX} fill={`url(#${fill})`} />
      <path
        d={HEX}
        stroke={`url(#${grad})`}
        strokeWidth="1.5"
        strokeLinejoin="round"
        opacity="0.85"
      />

      <path
        d={HOP_PATH}
        stroke={`url(#${grad})`}
        strokeWidth="4.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity="0.35"
        filter={`url(#${glow})`}
      />
      <path
        d={HOP_PATH}
        stroke={`url(#${grad})`}
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        filter={`url(#${bloom})`}
      />

      {NODES.map((node, i) => (
        <g key={node.cx}>
          <circle
            cx={node.cx}
            cy={node.cy}
            r={node.halo}
            fill={node.inner}
            opacity={i === 2 ? 0.28 : 0.18}
            filter={`url(#${glow})`}
          />
          <circle cx={node.cx} cy={node.cy} r={node.r} fill={`url(#${id}-node-${i})`} />
          <circle
            cx={node.cx - 0.7}
            cy={node.cy - 0.7}
            r={node.r * 0.28}
            fill="white"
            opacity="0.75"
          />
        </g>
      ))}

      <path
        d="M21.2 8.2 L23.75 9.25 L21.2 10.3"
        stroke="#e9d5ff"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        filter={`url(#${bloom})`}
      />

      <circle cx="26.5" cy="6.5" r="1" fill="#22d3ee" opacity="0.9" />
      <circle cx="28" cy="8.5" r="0.65" fill="#a78bfa" opacity="0.7" />
      <circle cx="5.5" cy="27" r="0.75" fill="#22d3ee" opacity="0.55" />
    </svg>
  )
}
