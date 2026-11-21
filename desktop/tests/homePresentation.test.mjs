import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import test from 'node:test'
import { selectRecentArticles, sourcePresentation, updatedDatePresentation } from '../src/homePresentation.ts'

const article = (id, extras = {}) => ({ id, title: id, ...extras })

test('recent cards include the resumed article once and keep completion order', () => {
  const library = Array.from({ length: 8 }, (_, i) => article(String(i)))
  const finished = [article('0'), article('new'), article('new')]
  assert.deepEqual(selectRecentArticles(finished, library, '0').map(item => item.id), ['0', 'new', '1', '2', '3', '4'])
  assert.deepEqual(selectRecentArticles(finished, library, '7').map(item => item.id), ['0', 'new', '1', '2', '3', '7'])
  assert.deepEqual(finished.map(item => item.id), ['0', 'new', 'new'])
  assert.deepEqual(library.map(item => item.id), ['0', '1', '2', '3', '4', '5', '6', '7'])
})

test('a finished job never hides newer library edits or reading state for the same article', () => {
  const old = article('saved', { title: 'Generated title', isRead: false, isStarred: false })
  const current = article('saved', { title: 'Edited title', isRead: true, isStarred: true })
  assert.deepEqual(selectRecentArticles([old], [article('other'), current]).map(item => item.id), ['saved', 'other'])
  assert.equal(selectRecentArticles([old], [current])[0], current)
  assert.equal(selectRecentArticles([old])[0], old)
})

test('a library containing only the resumed article shows its recent card', () => {
  assert.deepEqual(selectRecentArticles([article('only')], [article('only')], 'only'), [article('only')])
  assert.deepEqual(selectRecentArticles([], []), [])
  assert.deepEqual(selectRecentArticles([], [article('one')], undefined, 0), [])
})

test('source identity prefers the saved author and preserves the platform separately', () => {
  assert.deepEqual(sourcePresentation({ platform: 'apple_podcasts', author: ' 迷因电波 ', durationMs: 42 * 60_000 }), {
    name: '迷因电波', platformLabel: 'Apple 播客', duration: '音频约 42 分钟',
  })
  assert.deepEqual(sourcePresentation({ platform: 'youtube', author: 'Yano', durationMs: 9 * 60_000 }), {
    name: 'Yano', platformLabel: 'YouTube', duration: '视频约 9 分钟',
  })
})

test('missing authors fall back to platform names without guessing from titles', () => {
  for (const author of [undefined, null, '', ' \n ']) {
    assert.deepEqual(sourcePresentation({ platform: 'bilibili', author, durationMs: 29 * 60_000 }), {
      name: 'B 站', platformLabel: 'B 站', duration: '视频约 29 分钟',
    })
  }
  assert.deepEqual(sourcePresentation({ platform: 'local' }), { name: '本地字幕', platformLabel: '本地字幕', duration: '' })
  assert.deepEqual(sourcePresentation({ durationMs: 10_000 }), { name: '', platformLabel: '', duration: '原时长约 10 秒' })
})

test('unknown durations stay hidden; duration labels describe the original media with stable minute boundaries', () => {
  for (const durationMs of [undefined, null, 0, -1, NaN, Infinity, -Infinity]) {
    assert.equal(sourcePresentation({ platform: 'apple_podcasts', durationMs }).duration, '')
  }
  for (const [durationMs, label] of [[1, '视频约 1 秒'], [59_999, '视频约 59 秒'], [60_000, '视频约 1 分钟'], [90_000, '视频约 2 分钟']]) {
    assert.equal(sourcePresentation({ platform: 'youtube', durationMs }).duration, label)
  }
  assert.equal(sourcePresentation({ platform: 'local', durationMs: 120_000 }).duration, '原时长约 2 分钟')
})

test('updated dates use local calendar days and provide an exact timestamp with update semantics', () => {
  const now = new Date(2026, 10, 20, 12)
  const saved = new Date(2026, 10, 20, 9, 4, 5)
  assert.deepEqual(updatedDatePresentation(saved.getTime(), now), {
    label: '今天更新', title: '更新于 2026 年 11 月 20 日 09:04:05', dateTime: saved.toISOString(),
  })
  assert.equal(updatedDatePresentation(new Date(2026, 10, 19, 23, 59).getTime(), now).label, '昨天更新')
  assert.equal(updatedDatePresentation(new Date(2026, 10, 17).getTime(), now).label, '11 月 17 日更新')
})

test('yesterday crosses month and year boundaries, while older years remain explicit', () => {
  assert.equal(updatedDatePresentation(new Date(2026, 9, 31, 23).getTime(), new Date(2026, 10, 1, 1)).label, '昨天更新')
  const now = new Date(2027, 0, 1, 12)
  assert.equal(updatedDatePresentation(new Date(2026, 11, 31, 23).getTime(), now).label, '昨天更新')
  assert.equal(updatedDatePresentation(new Date(2026, 11, 30).getTime(), now).label, '2026 年 12 月 30 日更新')
})

test('invalid or missing archive timestamps do not turn into epoch dates or invalid machine dates', () => {
  for (const updatedAt of [undefined, null, 0, -1, NaN, Infinity, -Infinity, 8_640_000_000_000_001]) {
    assert.equal(updatedDatePresentation(updatedAt), null)
  }
})

test('calendar labels remain correct across the short and long daylight-saving days', () => {
  const moduleUrl = new URL('../src/homePresentation.ts', import.meta.url).href
  const script = `import { updatedDatePresentation as label } from ${JSON.stringify(moduleUrl)};
    process.stdout.write(JSON.stringify([
      label(new Date(2026, 2, 7, 23, 30).getTime(), new Date(2026, 2, 9, 0, 30)).label,
      label(new Date(2026, 2, 8, 0, 10).getTime(), new Date(2026, 2, 9, 0, 30)).label,
      label(new Date(2026, 10, 1, 0, 0).getTime(), new Date(2026, 10, 2, 0, 30)).label
    ]));`
  const labels = execFileSync(process.execPath, ['--experimental-strip-types', '--input-type=module', '-e', script], {
    encoding: 'utf8', env: { ...process.env, TZ: 'America/New_York' },
  })
  assert.deepEqual(JSON.parse(labels), ['3 月 7 日更新', '昨天更新', '昨天更新'])
})
