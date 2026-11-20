import assert from 'node:assert/strict'
import test from 'node:test'
import { continueCandidates, findContinueReading, readingSummary, continueLabel } from '../src/continueReading.ts'

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

test('continue labels describe the saved tab and date without inventing chapters or percentages', () => {
  const resume = { article: article('one'), tab: 'transcript', updatedAt: position().updatedAt }
  assert.equal(continueLabel(resume, new Date(2026, 10, 18, 12)), '昨天读到 Transcript')
  assert.equal(continueLabel(resume, new Date(2026, 10, 17, 18)), '今天读到 Transcript')
  assert.equal(continueLabel({ ...resume, tab: 'highlights' }, new Date(2026, 10, 20, 12)), '11 月 17 日读到 Highlights')
  assert.equal(continueLabel(resume, new Date(2027, 0, 1, 12)), '2026 年 11 月 17 日读到 Transcript')
})

test('continue labels use calendar boundaries at midnight and across the year', () => {
  const resume = { article: article('one'), tab: 'highlights', updatedAt: new Date(2026, 11, 31, 23, 59).getTime() }
  assert.equal(continueLabel(resume, new Date(2027, 0, 1, 0, 1)), '昨天读到 Highlights')
  assert.equal(continueLabel({ ...resume, updatedAt: new Date(2027, 0, 1, 0).getTime() }, new Date(2027, 0, 1, 0, 1)), '今天读到 Highlights')
  assert.equal(continueLabel({ ...resume, updatedAt: new Date(2026, 11, 30, 23, 59).getTime() }, new Date(2027, 0, 1, 0, 1)), '2026 年 12 月 30 日读到 Highlights')
})
