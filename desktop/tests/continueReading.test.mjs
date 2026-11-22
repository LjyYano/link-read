import assert from 'node:assert/strict'
import test from 'node:test'
import { continueCandidates, findContinueReading, readingSummary } from '../src/continueReading.ts'

const article = (id, extras = {}) => ({ id, title: id, readingKey: `source:${id}`, isRead: false, ...extras })
const position = (tab = 'transcript') => ({ version: 1, updatedAt: new Date(2026, 10, 17, 15).getTime(), activeTab: tab,
  tabs: { [tab]: { signature: 'p1-0123456789abcdef-10', anchor: { kind: 'top' }, offset: 0, ratio: 0 } }, mindmap: null })

test('continue only offers live unread archives with a durable identity, in recent reading order', () => {
  const articles = [article('new'), article('old'), article('done', { isRead: true }), article('preview', { previewId: 'x' }), article('legacy', { readingKey: undefined })]
  assert.deepEqual(continueCandidates(articles, ['deleted', 'done', 'preview', 'legacy', 'old', 'old', 'new']).map(a => a.id), ['old', 'new'])
  assert.deepEqual(continueCandidates(articles, []), [])
})

test('continue requires a saved position; corrupt/missing entries fall through without preparing HTML', async () => {
  const calls = []
  const result = await findContinueReading([article('first'), article('second'), article('third')], ['first', 'second', 'third'], async key => {
    calls.push(key)
    if (key === 'source:first') throw Error('Unreadable state')
    return key === 'source:second' ? null : position('mindmap')
  })
  assert.equal(result.article.id, 'third')
  assert.equal(result.tab, 'mindmap')
  assert.deepEqual(calls, ['source:first', 'source:second', 'source:third'])
  for (const invalid of [null, {}, { ...position(), updatedAt: 0 }, { ...position(), activeTab: 'notes' }, { ...position(), tabs: {} }]) {
    assert.equal(readingSummary(invalid), null)
  }
})

test('a late lookup after a library or navigation change cannot continue scanning or return stale content', async () => {
  let current = true, release
  const calls = []
  const promise = findContinueReading([article('one'), article('two')], ['one', 'two'], key => {
    calls.push(key); return new Promise(resolve => { release = resolve })
  }, () => current)
  current = false
  release(position())
  assert.equal(await promise, undefined)
  assert.deepEqual(calls, ['source:one'])
})
