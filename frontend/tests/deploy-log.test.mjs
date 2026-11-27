import test from 'node:test'
import assert from 'node:assert/strict'
import { emptyDeployLog, mergeDeployLog, deployLogOutcome } from '../src/lib/deploy-log-state.ts'
import { en } from '../src/i18n/locales/en.ts'
import { ru } from '../src/i18n/locales/ru.ts'
const event = (seq, stage = null) => ({ seq, ts: 1, level: 'info', stage, message: 'progress' })
const status = (seq, finished = false, ok = null) => ({ active: !finished, finished, ok, seq })

test('deploy log merges, sorts, and deduplicates reconnect/poll events', () => {
  const first = mergeDeployLog(emptyDeployLog, { events: [event(2), event(1)], status: status(2) })
  const next = mergeDeployLog(first, { events: [event(2), event(3)], status: status(3) })
  assert.deepEqual(next.events.map((entry) => entry.seq), [1, 2, 3])
  assert.equal(deployLogOutcome(next), 'deploying')
  assert.deepEqual(emptyDeployLog.events, [])
})
test('deploy log auto status success, error, and cancellation; end keeps events', () => {
  const running = mergeDeployLog(emptyDeployLog, { events: [event(1)], status: status(1) })
  assert.equal(deployLogOutcome(mergeDeployLog(running, { status: status(1, true, true) })), 'ok')
  assert.equal(deployLogOutcome(mergeDeployLog(running, { status: status(1, true, false) })), 'error')
  const cancelled = mergeDeployLog(running, { events: [event(2, 'cancelled')], status: status(2, true, false) })
  assert.equal(deployLogOutcome(cancelled), 'cancelled')
  assert.equal(cancelled.events.length, 2)
})
test('deploy log caps browser buffer and has matching locale keys', () => {
  const state = mergeDeployLog(emptyDeployLog, { events: Array.from({ length: 2005 }, (_, i) => event(i + 1)), status: status(2005) })
  assert.equal(state.events.length, 2000)
  assert.equal(state.events[0].seq, 6)
  assert.deepEqual(Object.keys(en.deployLog), Object.keys(ru.deployLog))
  assert.deepEqual(Object.keys(en.deployLog.status), Object.keys(ru.deployLog.status))
})
test('emptyDeployLog base isolation on configId mismatch or 404', () => {
  const previousState = mergeDeployLog(emptyDeployLog, { events: [event(1)], status: status(1) })
  const previous = { id: 'cfg-1', state: previousState }
  const newConfigId = 'cfg-2'
  const base = previous.id === newConfigId ? previous.state : emptyDeployLog
  const finalState = { ...base, status: { ...base.status, finished: true, active: false } }
  assert.equal(finalState.events.length, 0)
  assert.equal(finalState.status.finished, true)
  assert.equal(finalState.status.active, false)
})

