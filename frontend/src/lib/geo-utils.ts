const LOCAL_HOSTNAMES = new Set(['localhost', 'localhost.localdomain'])

export function isLocalHost(host: string): boolean {
  const trimmed = host.trim()
  if (!trimmed) return true
  const lower = trimmed.toLowerCase()
  if (LOCAL_HOSTNAMES.has(lower) || lower === '::1') return true
  if (/^[\d.]+$/.test(trimmed)) {
    if (/^127\./.test(trimmed) || /^0\./.test(trimmed) || /^169\.254\./.test(trimmed)) return true
  }
  return false
}

export function isPublicIp(host: string): boolean {
  const trimmed = host.trim()
  if (!trimmed || !/^[\d.]+$/.test(trimmed)) return false
  if (isLocalHost(trimmed)) return false
  return !/^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(trimmed)
}

export interface IpGeoResult {
  location: string
  countryCode: string
  city: string
  country: string
}

export async function lookupIpLocation(host: string): Promise<IpGeoResult | null> {
  const trimmed = host.trim()
  if (!trimmed || isLocalHost(trimmed)) return null
  if (/^[\d.]+$/.test(trimmed) && !isPublicIp(trimmed)) return null

  try {
    const res = await fetch(`https://ipwho.is/${encodeURIComponent(trimmed)}`)
    if (!res.ok) return null

    const data = (await res.json()) as {
      success?: boolean
      country_code?: string
      country?: string
      city?: string
    }

    if (!data.success || !data.country_code) return null

    const countryCode = data.country_code.toUpperCase()
    const city = data.city?.trim()
    const location = city ? `${countryCode}, ${city}` : countryCode

    return {
      location,
      countryCode,
      city: city ?? countryCode,
      country: data.country ?? countryCode,
    }
  } catch {
    return null
  }
}
