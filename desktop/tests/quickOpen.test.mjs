import assert from 'node:assert/strict'
import test from 'node:test'
import { highlightQueryParts, loadRecentArticles, rememberArticle, quickOpenArticles } from '../src/quickOpenState.ts'

const articles = [
  { id: 'new', title: 'New article', platform: 'youtube', category: 'Technology', author: 'Yano' },
  { id: 'podcast', title: 'A thoughtful conversation', platform: 'apple_podcasts', category: '', author: 'Meme Radio' },
  { id: 'old', title: 'Old article', platform: 'local', category: '', author: null },
]

test('quick open reuses library search for title, creator and source', () => {
  assert.deepEqual(quickOpenArticles(articles, 'MEME', []), [articles[1]])
  assert.deepEqual(quickOpenArticles(articles, '播客', []), [articles[1]])
  assert.deepEqual(quickOpenArticles(articles, ' article ', []).map(a => a.id), ['new', 'old'])
})

test('recent ordering uses live article IDs and never resurrects deleted entries', () => {
  assert.deepEqual(quickOpenArticles(articles, '', ['deleted', 'old', 'podcast']).map(a => a.id), ['old', 'podcast', 'new'])
  assert.deepEqual(articles.map(a => a.id), ['new', 'podcast', 'old'])
  assert.equal(quickOpenArticles(articles, 'not-found', ['old']).length, 0)
})

test('optional history is bounded, deduplicated and safe against malformed storage', () => {
  let stored = JSON.stringify(['one', 'one', '../outside', '/absolute', {}, 'two'])
  globalThis.localStorage = { getItem: () => stored, setItem: (_, value) => { stored = value } }
  assert.deepEqual(loadRecentArticles(), ['one', 'two'])
  assert.deepEqual(rememberArticle(['one', 'two'], 'two'), ['two', 'one'])
  assert.equal(rememberArticle(Array.from({ length: 40 }, (_, n) => String(n)), 'latest').length, 30)
  stored = '{broken'
  assert.deepEqual(loadRecentArticles(), [])
  globalThis.localStorage = { getItem: () => { throw Error('blocked') }, setItem: () => { throw Error('blocked') } }
  assert.deepEqual(loadRecentArticles(), [])
  assert.deepEqual(rememberArticle(['old'], 'new'), ['new', 'old'])
})

test('highlightQueryParts splits case-insensitive overlapping matches safely without HTML', () => {
  assert.deepEqual(highlightQueryParts('LinkRead Desktop', ''), [{ text: 'LinkRead Desktop', match: false }])
  assert.deepEqual(highlightQueryParts('LinkRead Desktop', 'read desk'), [
    { text: 'Link', match: false },
    { text: 'Read', match: true },
    { text: ' ', match: false },
    { text: 'Desk', match: true },
    { text: 'top', match: false },
  ])
  assert.deepEqual(highlightQueryParts('蛋白质的世界', '蛋白 白质'), [
    { text: '蛋白质', match: true },
    { text: '的世界', match: false },
  ])
})
