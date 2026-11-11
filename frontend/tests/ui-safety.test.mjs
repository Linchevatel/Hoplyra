import test from 'node:test'
import assert from 'node:assert/strict'
import { isValidHost } from '../src/lib/host-validation.ts'
import { hasLiveMetrics, reconcileMetrics, METRICS_TTL_MS } from '../src/lib/server-metrics.ts'

test('host accepts IPv4/IPv6/DNS, rejects schemes, paths, whitespace and invalid IPs', () => {
  for (const host of ['192.0.2.1', '2001:db8::1', '::1', 'vpn.example.org', 'node-1', 'example.org.']) assert.equal(isValidHost(host), true, host)
  for (const host of ['', 'https://example.org', 'example.org/a', 'bad host', ' example.org', '999.999.999.999', '1.2.3', '1234::abcd::1', '-bad.org', 'example.org:22', '[::1]', 'example.org?x']) assert.equal(isValidHost(host), false, host)
})

test('metrics TTL and explicit offline/error override cached numbers', () => {
  const server = { id: 's', name: 'server', host: '192.0.2.1', status: 'online' }
  const live = { serverId: 's', name: 'server', host: server.host, online: true, collectedAt: new Date().toISOString(), cpuPercent: 10 }
  assert.equal(hasLiveMetrics(live), true)
  assert.equal(hasLiveMetrics({ ...live, collectedAt: new Date(Date.now() - METRICS_TTL_MS - 1).toISOString() }), false)
  assert.equal(hasLiveMetrics({ ...live, collectedAt: 'bad' }), false)
  assert.equal(hasLiveMetrics({ ...live, online: false }), false)
  assert.equal(hasLiveMetrics({ ...live, error: 'failed' }), false)
  const offline = { ...live, online: false, error: 'offline' }
  assert.deepEqual(reconcileMetrics([offline], [live], [server]), [{ ...offline, latencyMs: undefined }])
  const cached = reconcileMetrics([], [live], [server])[0]
  assert.equal(cached.cached, true)
  assert.equal(cached.collectedAt, live.collectedAt)
  assert.equal(hasLiveMetrics(reconcileMetrics([], [{ ...live, collectedAt: '2000-01-01' }], [server])[0]), false)
})
