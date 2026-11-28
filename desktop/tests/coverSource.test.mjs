import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { localCoverSource, thumbnailSource } from '../src/coverSource.ts'

test('localCoverSource accepts only validated linkread-reader cover URLs', () => {
  const valid = 'linkread-reader://localhost/cover/%E6%96%87%E7%AB%A0%20%5Byoutube-1-Fo8McCD9w%5D?v=123-456'
  assert.equal(localCoverSource(valid), valid)

  for (const bad of [
    null,
    undefined,
    '',
    'https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg',
    'linkread-reader://evil.example/cover/one',
    'linkread-reader://localhost/preview/abc',
    'linkread-reader://localhost/cover/../secret',
    'linkread-reader://localhost/cover/one#frag',
    'file:///tmp/cover.jpg',
    'javascript:alert(1)',
  ]) {
    assert.equal(localCoverSource(bad), undefined, String(bad))
  }
})

test('thumbnailSource keeps strict public CDN allowlist', () => {
  assert.equal(thumbnailSource('http://i0.hdslb.com/bfs/archive/a.jpg'), 'https://i0.hdslb.com/bfs/archive/a.jpg')
  assert.equal(thumbnailSource('https://evil.example/a.jpg'), undefined)
})

// The same cases drive Python's thumbnail_url and the native reader's image policy.
test('thumbnailSource matches the shared cover host cases', () => {
  const cases = JSON.parse(readFileSync(new URL('../../tests/fixtures/cover_hosts.json', import.meta.url), 'utf8'))
  for (const url of cases.accepted) assert.equal(thumbnailSource(url), url)
  for (const url of cases.rejected) assert.equal(thumbnailSource(url), undefined, url)
  for (const { input, expected } of cases.upgraded) assert.equal(thumbnailSource(input), expected)
})
