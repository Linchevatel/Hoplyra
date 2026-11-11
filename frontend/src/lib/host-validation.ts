export function isValidHost(host: string): boolean {
  if (!host || /[\s/\\?#@]/.test(host)) return false
  if (host.includes(':')) {
    try { const u = new URL(`http://[${host}]/`); return u.hostname.startsWith('[') } catch { return false }
  }
  if (/^[\d.]+$/.test(host)) return host.split('.').length === 4 && host.split('.').every((n) => /^(0|[1-9]\d{0,2})$/.test(n) && Number(n) <= 255)
  return host.length <= 253 && host.replace(/\.$/, '').split('.').every((label) => /^[a-z\d](?:[a-z\d-]{0,61}[a-z\d])?$/i.test(label))
}
