import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeGeneratedAwg, validateAwg, versionParams } from '../src/lib/awg.ts'
import { readFileSync } from 'node:fs'
import { en } from '../src/i18n/locales/en.ts'
import { ru } from '../src/i18n/locales/ru.ts'

test('AWG translations have identical English/Russian leaf keys', () => {
  const keys = (tree, prefix = 'awg') => Object.entries(tree).flatMap(([key, value]) =>
    typeof value === 'object' ? keys(value, `${prefix}.${key}`) : [`${prefix}.${key}`]).sort()
  assert.deepEqual(keys(en.awg), keys(ru.awg))
})

test('AWG numeric validation rejects NaN and infinity but permits omitted empty fields', () => {
  assert.equal(validateAwg({ jc: NaN }).jc, 'range')
  assert.equal(validateAwg({ s1: Infinity }).s1, 'range')
  assert.deepEqual(validateAwg({ jc: undefined }), {})
})

test('automatic profile omits params and preserves version mapping', () => {
  assert.equal(versionParams(undefined), undefined)
  assert.equal(versionParams({}), undefined)
  const api = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8')
  assert.match(api, /if \(params && body.awgMode === 'custom'\) body.awgParams = params/)
  assert.match(api, /body.awgVersion = options.awgVersion/)
})
test('HPK requires every S field >=12 and errors stay field-specific', () => {
  assert.deepEqual(Object.keys(validateAwg({ headerProtectionKey: 'secret', s1: 12 })), ['s2', 's3', 's4'])
  assert.deepEqual(validateAwg({ headerProtectionKey: 'secret', s1: 12, s2: 12, s3: 12, s4: 12 }), {})
  assert.equal(validateAwg({ jc: 129 }).jc, 'range')
  assert.equal(validateAwg({ jmin: 10, jmax: 5 }).jmax, 'order')
  assert.equal(validateAwg({ h1: 5, h2: 5 }).h1, 'header')
  assert.equal(validateAwg({ h1: 1 }).h1, 'header')
})
test('old versions drop 3.1-only values; UI disables inputs and generates via helper', () => {
  assert.deepEqual(versionParams({ s1: 12, s3: 12, i1: 'x', headerProtectionKey: 'secret', randomTrailers: true }, 'awg'), { s1: 12 })
  const ui = readFileSync(new URL('../src/components/dashboard/AwgAdvanced.tsx', import.meta.url), 'utf8')
  assert.match(ui, /disabled=\{disabled\}/)
  assert.match(ui, /generateAwg\(version, params.profile/)
  assert.match(ui, /onModeChange\('custom', versionParams\(normalizeGeneratedAwg\(generated\), version\)\)/)
  assert.match(ui, /'password'/)
})

test('generated header ranges autofill integer fields', () => {
  assert.deepEqual(normalizeGeneratedAwg({ h1: '100-200', h2: '300' }), { h1: 100, h2: 300 })
})
