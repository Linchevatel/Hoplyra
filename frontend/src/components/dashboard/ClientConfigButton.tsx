import { Download } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import type { VpnConfig } from '@/lib/types'
import { canShowClientConfig } from '@/lib/client-config'
import { useI18n } from '@/i18n/I18nProvider'

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

  return (
    <Button variant={compact ? 'ghost' : 'secondary'} size="sm" onClick={onClick}>
      <Download size={14} />
      {compact ? t('common.config') : t('dashboard.configs.clientConfig')}
    </Button>
  )
}
