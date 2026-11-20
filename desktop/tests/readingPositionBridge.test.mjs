import test from 'node:test'
import assert from 'node:assert/strict'
import { ReadingPositionBridge, flushReadingPositions } from '../src/readingPositionBridge.ts'

const tick = () => new Promise(resolve => setTimeout(resolve, 0))
const ready = (key, session) => ({ linkReadPositionReady: { version: 1, key, session } })
const changed = (key, session, state, requestId) => ({ linkReadPositionChanged: { version: 1, key, session, state, requestId } })

test('native restore cannot cross an iframe session or article identity', async () => {
  const loads = [], sent = [], saved = []
  const bridge = new ReadingPositionBridge({ key: 'source-a', load: () => new Promise(resolve => loads.push(resolve)),
    save: async (_key, state) => { saved.push(state) }, send: value => sent.push(value), onError: assert.fail })
  bridge.receive(ready('source-b', 'session-00'))
  assert.equal(loads.length, 0)
  bridge.receive(ready('source-a', 'session-01'))
  await tick()
  bridge.receive(ready('source-a', 'session-02'))
  await tick()
  loads[0]({ stale: true }); loads[1]({ current: true })
  await tick()
  assert.equal(sent.length, 1)
  assert.equal(sent[0].linkReadPositionRestore.session, 'session-02')
  bridge.receive(changed('source-a', 'session-01', { stale: true }))
  bridge.receive(changed('source-b', 'session-02', { foreign: true }))
  await tick()
  assert.deepEqual(saved, [])
  bridge.close()
})

test('leaving waits for the current capture to be durably saved in order', async () => {
  const saved = [], resolvers = [], sent = []
  const bridge = new ReadingPositionBridge({ key: 'flush-source', load: async () => null,
    save: (_key, state) => new Promise(resolve => { saved.push(state); resolvers.push(resolve) }),
    send: value => sent.push(value), onError: assert.fail })
  bridge.receive(ready('flush-source', 'session-01')); await tick()
  bridge.receive(changed('flush-source', 'session-01', { sequence: 1 })); await tick()
  let finished = false
  const flush = bridge.flush().then(() => { finished = true })
  await tick()
  const request = sent.at(-1).linkReadPositionFlush
  bridge.receive(changed('flush-source', 'session-01', { sequence: 2 }, request.requestId))
  await tick()
  assert.equal(saved.length, 1)
  assert.equal(finished, false)
  resolvers[0](); await tick()
  assert.deepEqual(saved, [{ sequence: 1 }, { sequence: 2 }])
  assert.equal(finished, false)
  resolvers[1](); await flush
  assert.equal(finished, true)
  bridge.close()
})

test('immediate app navigation restores the native record before capturing and saving', async () => {
  let restore
  const sent = [], saved = []
  const bridge = new ReadingPositionBridge({ key: 'immediate-leave', load: () => new Promise(resolve => { restore = resolve }),
    save: async (_key, state) => { saved.push(state) }, send: message => sent.push(message), onError: assert.fail })
  bridge.receive(ready('immediate-leave', 'session-01')); await tick()
  let done = false
  const leaving = flushReadingPositions().then(() => { done = true })
  await tick()
  assert.equal(sent.length, 0)
  assert.equal(done, false)
  restore({ persistedAnchor: 42 }); await tick()
  assert.equal(sent[0].linkReadPositionRestore.state.persistedAnchor, 42)
  const capture = sent[1].linkReadPositionFlush
  bridge.receive(changed('immediate-leave', 'session-01', { persistedAnchor: 42 }, capture.requestId))
  await leaving
  assert.deepEqual(saved, [{ persistedAnchor: 42 }])
  assert.equal(done, true)
  bridge.close()
})

test('a failed position save is reported but does not poison later saves', async () => {
  let fail = true; const errors = [], saved = []
  const bridge = new ReadingPositionBridge({ key: 'retry-source', load: async () => null,
    save: async (_key, state) => { if (fail) throw new Error('disk full'); saved.push(state) },
    send: () => {}, onError: error => errors.push(String(error)) })
  bridge.receive(ready('retry-source', 'session-01')); await tick()
  bridge.receive(changed('retry-source', 'session-01', { sequence: 1 })); await tick()
  fail = false
  bridge.receive(changed('retry-source', 'session-01', { sequence: 2 })); await tick()
  assert.equal(errors.length, 1)
  assert.deepEqual(saved, [{ sequence: 2 }])
  bridge.close()
})
