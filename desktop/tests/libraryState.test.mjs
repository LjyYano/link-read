import assert from 'node:assert/strict'
import test from 'node:test'
import { articleState, filterArticles, searchArticles, shelfCounts, withArticleState, ArticleStateChanges, ArticleStateWrites } from '../src/libraryState.ts'

const article = (id, extra = {}) => ({
  id, title: id, category: '学习', platform: 'apple_podcasts', author: '迷因电波', updatedAt: 123,
  sourceUrl: null, publishedAt: null, durationMs: 2542000, thumbnailUrl: null, ...extra,
})
const collection = [
  article('new'), article('read', { isRead: true }),
  article('star', { isStarred: true }), article('both', { isRead: true, isStarred: true }),
]
const ids = items => items.map(item => item.id)

test('reading completion is opt-in, independent of generation and stars', () => {
  assert.deepEqual(articleState(article('old', { hasAnalysis: true })), { isRead: false, isStarred: false })
  assert.deepEqual(articleState(article('raw', { hasAnalysis: false, isStarred: true })), { isRead: false, isStarred: true })
  assert.deepEqual(articleState(collection[3]), { isRead: true, isStarred: true })
})

test('reading and star filters combine without changing article order', () => {
  assert.deepEqual(ids(filterArticles(collection, { read: 'all', starred: false })), ['new', 'read', 'star', 'both'])
  assert.deepEqual(ids(filterArticles(collection, { read: 'unread', starred: false })), ['new', 'star'])
  assert.deepEqual(ids(filterArticles(collection, { read: 'completed', starred: false })), ['read', 'both'])
  assert.deepEqual(ids(filterArticles(collection, { read: 'all', starred: true })), ['star', 'both'])
  assert.deepEqual(ids(filterArticles(collection, { read: 'unread', starred: true })), ['star'])
  assert.deepEqual(ids(filterArticles(collection, { read: 'completed', starred: true })), ['both'])
})

test('search works with titles, authors, categories and bilingual platform terms', () => {
  const rows = [article('podcast'), article('video', { platform: 'bilibili', author: '飞鸟', title: '关系与沟通', category: '生活' })]
  for (const query of ['迷因', '学习', '  APPLE PODCASTS  ', '播客']) assert.deepEqual(ids(searchArticles(rows, query)), ['podcast'])
  for (const query of ['关系', '飞鸟', 'bilibili', 'B 站']) assert.deepEqual(ids(searchArticles(rows, query)), ['video'])
  assert.equal(searchArticles(rows, '').length, 2)
  assert.deepEqual(searchArticles(rows, 'does not exist'), [])
})

test('filter counts agree with the active star subset and the search results', () => {
  assert.deepEqual(shelfCounts(collection, false), { all: 4, unread: 2, completed: 2, starred: 2 })
  assert.deepEqual(shelfCounts(collection, true), { all: 2, unread: 1, completed: 1, starred: 2 })
  assert.deepEqual(shelfCounts(searchArticles(collection, 'both'), true), { all: 1, unread: 0, completed: 1, starred: 1 })
  assert.deepEqual(shelfCounts([], true), { all: 0, unread: 0, completed: 0, starred: 0 })
})

test('saved state updates do not alter metadata, generation state, sort dates or another article', () => {
  const original = article('one', { isRead: false, isStarred: true, hasAnalysis: true })
  const saved = withArticleState(original, 'one', { isRead: true, isStarred: true })
  assert.deepEqual(saved, { ...original, isRead: true })
  assert.equal(original.isRead, false)
  assert.equal(withArticleState(original, 'another', { isRead: true, isStarred: false }), original)
})

test('late library responses cannot erase a saved toggle while future reloads stay authoritative', () => {
  const updates = new ArticleStateChanges()
  const startedBeforeSave = updates.version
  updates.record('one', { isRead: true, isStarred: false })
  const intermediate = updates.version
  updates.record('one', { isRead: true, isStarred: true })
  updates.record('two', { isRead: false, isStarred: true })
  const stale = [article('one', { title: 'New metadata', updatedAt: 456 }), article('two')]
  const reconciled = updates.reconcile(stale, startedBeforeSave)
  assert.deepEqual(articleState(reconciled[0]), { isRead: true, isStarred: true })
  assert.deepEqual(articleState(reconciled[1]), { isRead: false, isStarred: true })
  assert.equal(reconciled[0].title, 'New metadata')
  assert.equal(reconciled[0].updatedAt, 456)
  assert.deepEqual(articleState(updates.reconcile(stale, intermediate)[0]), { isRead: true, isStarred: true })
  assert.equal(updates.reconcile(stale, updates.version)[0], stale[0])
  assert.deepEqual(updates.reconcile([], startedBeforeSave), [])
})

test('writes from different views are serialized per article, and a failed write does not block retry', async () => {
  const writes = new ArticleStateWrites()
  let release, reject
  const firstResponse = new Promise(resolve => { release = resolve })
  const failureResponse = new Promise((_, fail) => { reject = fail })
  const events = []
  const one = writes.run('one', async () => { events.push('one:start'); await firstResponse; events.push('one:saved') })
  const two = writes.run('one', async () => { events.push('two:start'); events.push('two:saved') })
  const other = writes.run('other', async () => { events.push('other:saved') })
  await other
  assert.deepEqual(events, ['one:start', 'other:saved'])
  release()
  await Promise.all([one,two])
  assert.deepEqual(events, ['one:start','other:saved','one:saved','two:start','two:saved'])
  const failing = writes.run('one', async () => { await failureResponse })
  const assertion = assert.rejects(failing, /write failed/)
  const retry = writes.run('one', async () => { events.push('retry:saved') })
  reject(new Error('write failed'))
  await assertion
  await retry
  assert.equal(events.at(-1), 'retry:saved')
})
