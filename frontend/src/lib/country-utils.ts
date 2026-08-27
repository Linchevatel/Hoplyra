/** ISO 3166-1 alpha-2 код из строки локации вида "DE, Frankfurt" */
export function countryCodeFromLocation(location?: string): string | null {
  if (!location) return null
  const raw = location.split(',')[0]?.trim().toUpperCase()
  if (!raw) return null

  const aliases: Record<string, string> = {
    UK: 'GB',
  }

  const code = aliases[raw] ?? raw
  return /^[A-Z]{2}$/.test(code) ? code.toLowerCase() : null
}

export function flagEmoji(countryCode: string): string {
  const code = countryCode.toUpperCase()
  if (code.length !== 2) return '🌐'
  const points = [...code].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65)
  return String.fromCodePoint(...points)
}

export function getLocationFlag(location?: string): string {
  const code = countryCodeFromLocation(location)
  return code ? flagEmoji(code) : '🌐'
}

export function getLocationCity(location?: string): string | null {
  if (!location) return null
  const parts = location.split(',').map((p) => p.trim())
  return parts.length > 1 ? parts.slice(1).join(', ') : parts[0] ?? null
}
