import test from 'node:test'
import assert from 'node:assert/strict'
import { laneLabelFor, recordProgress, requestCaption, stageRows } from '../src/taskProgress.ts'

const event = (sequence, phase, overrides = {}) => ({ stage: 'polish_request', message: 'Model request', completed: null, total: null,
  request: { sequence, id: 'r1', stage: 'polish', phase, active: 2, queued: 1, validating: 0, completed: 0, cacheHits: 0, ...overrides } })
test('late request snapshots do not regress active counts or last-result time', () => {
  let state = recordProgress(undefined, event(1, 'running'), 1000)
  state = recordProgress(state, event(3, 'complete', { active: 0, queued: 0, completed: 1 }), 2000)
  assert.equal(recordProgress(state, event(2, 'running'), 3000), state)
  assert.equal(state.lastResultAt, 2000)
  assert.equal(requestCaption(state, 25_000), undefined)
})
test('running requests, queue slots and validation are distinguished without fake progress', () => {
  let state = recordProgress(undefined, event(1, 'received'), 1000)
  state = recordProgress(state, event(2, 'validating', { validating: 1 }), 2000)
  assert.equal(requestCaption(state, 40_000), '2 项等待模型响应 · 1 项等待并发空位 · 1 项正在校验 · 上次结果 39 秒前')
})
test('structured failure identifies only its own parallel stage and interruption stops all loaders', () => {
  let state = recordProgress(undefined, { stage: 'ai_start', message: 'analysis', completed: 0, total: 8 }, 1000)
  state = recordProgress(state, { stage: 'polish_start', message: 'polish', completed: 3, total: 8 }, 2000)
  assert.deepEqual(stageRows(state, 'error', 'polish').map(x => [x.key, x.state]), [['analysis','stopped'],['polish','failed']])
  assert.ok(stageRows(state, 'interrupted').every(x => x.state === 'stopped'))
})
test('routine request telemetry does not flood history while failed requests and stage labels remain clear', () => {
  let state = recordProgress(undefined, { stage: 'ai_start', message: '开始提炼', completed: 0, total: 3 }, 1000)
  state = recordProgress(state, event(1, 'queued', { active: 0, queued: 1 }), 1100)
  state = recordProgress(state, event(2, 'running', { active: 1, queued: 0 }), 1200)
  state = recordProgress(state, event(3, 'cache', { active: 0, queued: 0, cacheHits: 1 }), 1300)
  assert.deepEqual(state.history.map(item => item.message), ['开始提炼'])
  state = recordProgress(state, {
    ...event(4, 'failed', { active: 0, queued: 0 }),
    stage: 'ai_request',
    message: '模型结果未通过原文与结构校验',
  }, 1400)
  assert.deepEqual(state.history.map(item => item.message), ['开始提炼', '模型结果未通过原文与结构校验'])
  assert.equal(laneLabelFor('polish_done'), '校对正文')
  assert.equal(laneLabelFor('ai_retry'), '提炼内容')
})
