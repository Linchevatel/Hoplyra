import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { loadStoredServers, serializeServers, requireServerBackend } from '../src/lib/server-storage.ts'

function storage(initial) {
  let value = initial
  return {
    getItem: () => value,
    setItem: (_key, next) => { value = next },
    removeItem: () => { value = null },
  }
}

const server = { id: 'real-1', name: 'VPS', host: '192.0.2.1', port: 22, username: 'root', status: 'online', authSecret: 'test-secret' }

test('persistence strips credentials without mutating in-memory input', () => {
  assert.equal(JSON.parse(serializeServers([server]))[0].authSecret, undefined)
  assert.equal(server.authSecret, 'test-secret')
  assert.equal(serializeServers([]), '[]')
})

test('legacy cache is scrubbed synchronously and cannot claim online', () => {
  const cache = storage(JSON.stringify([{ ...server, activeProtocol: 'wg' }]))
  const loaded = loadStoredServers(cache, 'servers')
  assert.equal(loaded[0].status, 'offline')
  assert.equal(loaded[0].authSecret, undefined)
  assert.equal(loaded[0].activeProtocol, undefined)
  assert.ok(!cache.getItem().includes('test-secret'))
})

test('invalid legacy data is removed', () => {
  for (const raw of ['{broken', '{"authSecret":"test-secret"}']) {
    const cache = storage(raw)
    assert.deepEqual(loadStoredServers(cache, 'servers'), [])
    assert.equal(cache.getItem(), null)
  }
})

test('failed cache rewrite removes legacy data and returns no servers', () => {
  const cache = storage(JSON.stringify([server]))
  cache.setItem = () => { throw new Error('blocked') }
  assert.deepEqual(loadStoredServers(cache, 'servers'), [])
  assert.equal(cache.getItem(), null)
})

test('missing backend rejects instead of simulating a connection', () => {
  assert.throws(() => requireServerBackend(false, 'Backend unavailable'), /Backend unavailable/)
  assert.doesNotThrow(() => requireServerBackend(true, 'Backend unavailable'))
  // Guard the provider wiring as well as the pure policy.
  const source = readFileSync(new URL('../src/lib/dashboard.tsx', import.meta.url), 'utf8')
  const addServer = source.slice(source.indexOf('  const addServer ='), source.indexOf('  const pingServerById ='))
  assert.ok(addServer.includes('requireServerBackend(apiEnabled, message)'))
  assert.ok(!addServer.includes('setTimeout'))
  assert.ok(!addServer.includes('...data,'))
  const context = readFileSync(new URL('../src/lib/dashboard-context.ts', import.meta.url), 'utf8')
  assert.match(context, /Promise<VpnConfig \| undefined>/)
})
