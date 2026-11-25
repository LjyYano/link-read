import assert from 'node:assert/strict'
import test from 'node:test'
import { activeTaskCount, groupTasks, loadReviewedTasks, reviewTasks, completedArticles, loadCompletedArticleIds, rememberCompletedArticle,
  reviewedTerminalTaskIds, saveReviewedTasks, unreviewedTaskIds } from '../src/taskCenterState.ts'

const tasks = [
  { id: 'running', job: { status: 'running', message: 'Working', percent: 5, step: 0 } },
  { id: 'stopping', job: { status: 'cancelling', message: 'Stopping', percent: 12, step: 0 } },
  { id: 'queued-1', job: { status: 'queued', message: 'Waiting' } },
  { id: 'queued-2', job: { status: 'queued', message: 'Waiting' } },
  { id: 'error', job: { status: 'error', message: 'Unavailable' } },
  { id: 'cancelled', job: { status: 'cancelled', message: 'Cancelled' } },
  { id: 'interrupted', job: { status: 'interrupted', message: 'Resume explicitly' } },
  { id: 'done', job: { status: 'done', article: { id: 'article' } } },
]

test('task groups preserve queue order and show the newest attention records first', () => {
  const snapshot = JSON.stringify(tasks)
  const groups = groupTasks(tasks)
  assert.deepEqual(groups.active.map(task => task.id), ['running', 'stopping'])
  assert.deepEqual(groups.queued.map(task => task.id), ['queued-1', 'queued-2'])
  assert.deepEqual(groups.attention.map(task => task.id), ['interrupted', 'cancelled', 'error'])
  assert.equal(activeTaskCount(tasks), 4)
  assert.equal(JSON.stringify(tasks), snapshot)
  assert.deepEqual(groupTasks([]), { active: [], queued: [], attention: [] })
})

test('failure and interrupted records remain unreviewed until explicitly acknowledged', () => {
  assert.deepEqual(unreviewedTaskIds(tasks, []), ['error', 'interrupted'])
  assert.deepEqual(unreviewedTaskIds(tasks, ['error']), ['interrupted'])
  const reviewed = reviewTasks([], ['error'], tasks)
  assert.deepEqual(unreviewedTaskIds(tasks, reviewed), ['interrupted'])
  const withNewFailure = [...tasks, { id: 'new-error', job: { status: 'error' } }]
  assert.deepEqual(unreviewedTaskIds(withNewFailure, reviewed), ['interrupted', 'new-error'])
})

test('clear selection can never remove running, queued, finished or unseen records', () => {
  assert.deepEqual(reviewedTerminalTaskIds(tasks, []), [])
  assert.deepEqual(reviewedTerminalTaskIds(tasks, ['error', 'queued-1', 'running', 'done', 'gone']), ['error'])
  assert.deepEqual(reviewedTerminalTaskIds(tasks, ['cancelled', 'interrupted']), ['cancelled', 'interrupted'])
  assert.deepEqual(reviewTasks(['removed', 'error'], ['queued-1', 'interrupted', 'done'], tasks), ['interrupted', 'error'])
})

test('acknowledgement stays bounded and malformed local history cannot supply arbitrary IDs', () => {
  let stored = JSON.stringify(['one', 'one', '../outside', '/absolute', 'a\\b', '\u0000', {}, 'two'])
  globalThis.localStorage = { getItem: () => stored, setItem: (_, value) => { stored = value } }
  assert.deepEqual(loadReviewedTasks(), ['one', 'two'])
  const saved = saveReviewedTasks(['error', 'error', ...Array.from({ length: 150 }, (_, n) => `old-${n}`)])
  assert.equal(saved.length, 100)
  assert.deepEqual(JSON.parse(stored), saved)
  assert.equal(reviewTasks(saved, ['latest'])[0], 'latest')
  assert.equal(reviewTasks(saved, ['latest']).length, 100)
  stored = '{broken'
  assert.deepEqual(loadReviewedTasks(), [])
  globalThis.localStorage = { getItem: () => { throw Error('blocked') }, setItem: () => { throw Error('blocked') } }
  assert.deepEqual(loadReviewedTasks(), [])
  assert.deepEqual(saveReviewedTasks(['error']), ['error'])
})

test('recent completions survive reload and show only live archives in completion order', () => {
  let stored = JSON.stringify(['older', 'old', '../outside', 'older'])
  globalThis.localStorage = { getItem: () => stored, setItem: (_, value) => { stored = value } }
  const ids = rememberCompletedArticle(loadCompletedArticleIds(), 'old')
  assert.deepEqual(loadCompletedArticleIds(), ['old', 'older'])
  assert.deepEqual(completedArticles([{ id: 'other' }, { id: 'older' }], ids), [{ id: 'older' }])
  assert.equal(rememberCompletedArticle(Array.from({ length: 20 }, (_, number) => `item-${number}`), 'new').length, 8)
})
