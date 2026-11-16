import { Download, Users } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import type { VpnConfig } from '@/lib/types'
import { canShowClientConfig } from '@/lib/client-config'
import { useI18n } from '@/i18n/i18n-context'

export function ClientConfigButton({
  config,
  onClick,
  compact,
}: {
  config: VpnConfig
  onClick: () => void
  compact?: boolean
}) {
  const { t } = useI18n()

  if (!canShowClientConfig(config)) return null

  const isMultiPeer = (config.protocol === 'awg' || config.protocol === 'wg' || config.protocol === 'xray' || config.protocol === 'openvpn') && !config.hops?.length

  return (
    <Button variant={compact ? 'ghost' : 'secondary'} size="sm" onClick={onClick}>
      {isMultiPeer ? <Users size={14} /> : <Download size={14} />}
      {isMultiPeer ? t('common.clients') : (compact ? t('common.config') : t('dashboard.configs.clientConfig'))}
    </Button>
  )
}
