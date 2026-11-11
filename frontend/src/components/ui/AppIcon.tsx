import link from '@/assets/icons/link.png'
import dashboard from '@/assets/icons/dashboard.png'
import servers from '@/assets/icons/servers.png'
import add from '@/assets/icons/add.png'
import settings2 from '@/assets/icons/settings2.png'
import activity2 from '@/assets/icons/activity2.png'
import latency2 from '@/assets/icons/latency2.png'
import bandwidth2 from '@/assets/icons/bandwidth2.png'
import security2 from '@/assets/icons/security2.png'
import deploy2 from '@/assets/icons/deploy2.png'
import global2 from '@/assets/icons/global2.png'
import link2 from '@/assets/icons/link2.png'
import lock from '@/assets/icons/lock.png'
import activity from '@/assets/icons/activity.png'
import latency from '@/assets/icons/latency.png'
import bandwidth from '@/assets/icons/bandwidth.png'
import security from '@/assets/icons/security.png'
import addServer from '@/assets/icons/add-server.png'
import deploy from '@/assets/icons/deploy.png'
import global from '@/assets/icons/global.png'
import noServers from '@/assets/icons/no-servers.png'
import noActiveVpn from '@/assets/icons/no-active-vpn.png'
import noConfigs from '@/assets/icons/no-configs.png'
import socks from '@/assets/icons/socks.png'
import multihop from '@/assets/icons/multihop.png'
import server from '@/assets/icons/server.png'
import settings from '@/assets/icons/settings.png'
import { cn } from '@/lib/utils'

// Legacy names retained for compatibility; new UI uses the unique artwork below.
const icons = { link, dashboard, servers, add, settings2, activity2, latency2, bandwidth2, security2, deploy2, global2, link2, server, lock, settings, activity, latency, bandwidth, security, 'add-server': addServer, deploy, global, socks, multihop, 'no-servers': noServers, 'no-active-vpn': noActiveVpn, 'no-configs': noConfigs }

export type AppIconName = keyof typeof icons

/** Decorative artwork: the adjacent UI text supplies the accessible label. */
export function AppIcon({ name, size = 28, className }: { name: AppIconName; size?: number; className?: string }) {
  return <img src={icons[name]} alt="" aria-hidden="true" draggable={false} width={size} height={size} style={{ width: size, height: size }} className={cn('object-contain shrink-0 pointer-events-none', className)} />
}
