import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { en } from '../src/i18n/locales/en.ts'
import { ru } from '../src/i18n/locales/ru.ts'

const source = name => readFileSync(new URL(`../src/components/dashboard/${name}.tsx`, import.meta.url), 'utf8')

test('AWG container upgrade control is absent from VPN cards', () => {
  assert.doesNotMatch(source('VpnTab'), /upgradeAwgConfig|dashboard\.vpn\.upgradeAwg/)
})

test('live badge overrides only active configs and is used on list and chain cards', () => {
  const card = source('ClientConfigCard')
  assert.match(card, /status === 'active' && liveState === 'down'/)
  assert.match(card, /status === 'active' && liveState === 'degraded'/)
  for (const component of ['VpnTab', 'ClientConfigCard', 'ChainsTab']) {
    assert.match(source(component), /<ConfigStatusBadge status=\{config.status\} liveState=\{config.liveState\}/)
  }
  assert.equal(ru.status.unavailable, 'Недоступен')
  assert.equal(en.status.unavailable, 'Unavailable')
  assert.ok(ru.status.degraded)
  assert.ok(en.status.degraded)
})
