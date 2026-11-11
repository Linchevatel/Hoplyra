export type AwgParams = Partial<Record<'jc' | 'jmin' | 'jmax' | 's1' | 's2' | 's3' | 's4' | 'h1' | 'h2' | 'h3' | 'h4', number>> & Partial<Record<'i1' | 'i2' | 'i3' | 'i4' | 'i5' | 'headerProtectionKey' | 'profile' | 'contentPaddingAddition', string>> & { randomTrailers?: boolean; disableCookies?: boolean }
export const v31Fields = ['s3', 's4', 'i1', 'i2', 'i3', 'i4', 'i5', 'headerProtectionKey', 'contentPaddingAddition', 'randomTrailers', 'disableCookies'] as const
export function versionParams(params: AwgParams | undefined, version = 'awg3.1'): AwgParams | undefined {
  if (!params) return undefined
  const result = { ...params }
  if (version !== 'awg3.1') for (const key of v31Fields) delete result[key]
  return Object.keys(result).length ? result : undefined
}
export function validateAwg(params: AwgParams = {}): Record<string, string> {
  const errors: Record<string, string> = {}
  for (const key of ['jc', 'jmin', 'jmax', 's1', 's2', 's3', 's4', 'h1', 'h2', 'h3', 'h4'] as const) {
    const n = params[key]
    if (n !== undefined && (!Number.isSafeInteger(n) || n < 0 || (key === 'jc' && n > 128) || (key.startsWith('h') && n > 4294967295) || (key.startsWith('s') && n > (({ s1: 1132, s2: 1188, s3: 1132, s4: 32 } as Record<string, number>)[key] ?? Infinity)))) errors[key] = 'range'
  }
  if (params.jmin !== undefined && params.jmax !== undefined && params.jmin >= params.jmax) errors.jmax = 'order'
  if (params.headerProtectionKey) for (const key of ['s1', 's2', 's3', 's4'] as const) if ((params[key] ?? 0) < 12) errors[key] = 'hpk'
  const headers = ['h1', 'h2', 'h3', 'h4'] as const
  for (const key of headers) if (params[key] !== undefined && ((params[key]! >= 1 && params[key]! <= 4) || headers.some(other => other !== key && params[other] === params[key]))) errors[key] = 'header'
  return errors
}
export function assertAwg(params: AwgParams | undefined) {
  if (Object.keys(validateAwg(params)).length) throw new Error('AWG: invalid obfuscation parameters')
}

export function normalizeGeneratedAwg(params: AwgParams): AwgParams {
  const result = { ...params }
  for (const key of ['h1', 'h2', 'h3', 'h4'] as const) {
    if (result[key] !== undefined) result[key] = Number(String(result[key]).split('-')[0])
  }
  return result
}
