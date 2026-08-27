import type { Protocol } from '@/lib/types'
import { PROTOCOLS } from '@/lib/constants'
import { useI18n } from '@/i18n/I18nProvider'

const SETUP_KEYS: Record<Protocol, 'simple' | 'medium' | 'advanced'> = {
  awg: 'simple',
  wg: 'simple',
  openvpn: 'medium',
  xray: 'advanced',
  tor: 'advanced',
  hysteria2: 'simple',
  tuic: 'simple',
}

export function useProtocolMeta(protocol: Protocol) {
  const { t, tList } = useI18n()
  const base = PROTOCOLS[protocol] || {
    label: protocol,
    shortLabel: protocol.toUpperCase(),
    description: '',
    color: 'from-gray-500 to-slate-600',
    bestFor: '',
    setup: 'simple',
    traits: [],
    qrConfig: false,
  }
  const setupKey = SETUP_KEYS[protocol] || 'simple'

  return {
    ...base,
    description: base.description || t(`protocol.${protocol}.description`),
    bestFor: base.bestFor || t(`protocol.${protocol}.bestFor`),
    setup: t(`setup.${setupKey}`),
    traits: base.traits.length > 0 ? base.traits : tList(`protocol.${protocol}.traits`),
  }
}

export function useAllProtocolMeta() {
  const { t, tList } = useI18n()
  return (Object.keys(PROTOCOLS) as Protocol[]).reduce(
    (acc, protocol) => {
      const base = PROTOCOLS[protocol]
      const setupKey = SETUP_KEYS[protocol] || 'simple'
      acc[protocol] = {
        ...base,
        description: t(`protocol.${protocol}.description`),
        bestFor: t(`protocol.${protocol}.bestFor`),
        setup: t(`setup.${setupKey}`),
        traits: tList(`protocol.${protocol}.traits`),
      }
      return acc
    },
    {} as Record<Protocol, ReturnType<typeof useProtocolMeta>>,
  )
}
