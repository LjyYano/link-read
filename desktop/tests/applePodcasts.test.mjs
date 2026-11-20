import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { normalizeVideoLink } from '../src/videoLinks.ts'
import { previewKey } from '../src/previewState.ts'
import { thumbnailSource } from '../src/coverSource.ts'

test('podcast preview identifies an exact episode and storefront without tracking parameters', () => {
  const episode = 'https://podcasts.apple.com/cn/podcast/id1552904790?i=1000801308527'
  const shared = 'https://podcasts.apple.com/cn/podcast/商业就是这样/id1552904790?i=1000801308527&uo=4'
  assert.equal(normalizeVideoLink(shared), episode)
  assert.equal(previewKey(shared), previewKey(episode))
  assert.notEqual(previewKey(episode), previewKey(episode.replace('1000801308527', '1000801693752')))
  assert.notEqual(previewKey(episode), previewKey(episode.replace('1552904790', '1552904791')))
  assert.notEqual(previewKey(episode), previewKey(episode.replace('/cn/', '/us/')))
  assert.equal(previewKey('https://podcasts.apple.com/cn/podcast/id1552904790'), undefined)
})

test('podcast IDs retain precision and reject ambiguous episode selections', () => {
  const source = 'https://podcasts.apple.com/podcast/id12345678901234567890?i=98765432109876543210'
  assert.equal(normalizeVideoLink(source), source.replace('/podcast/', '/us/podcast/'))
  for (const suffix of ['?i=1&i=2', '?i=1&i=1', '?i=01', '?i=0', '?i=1e3', '?i=123456789012345678901']) {
    assert.throws(() => normalizeVideoLink('https://podcasts.apple.com/cn/podcast/id1552904790' + suffix))
  }
})

test('Apple artwork uses trusted HTTPS hosts allowed by the desktop CSP', () => {
  assert.equal(thumbnailSource('http://is1-ssl.mzstatic.com/image/cover.jpg'), 'https://is1-ssl.mzstatic.com/image/cover.jpg')
  for (const url of ['https://mzstatic.com.evil.test/cover.jpg', 'https://evil.test.mzstatic.com.evil.test/cover.jpg', 'https://user@is1-ssl.mzstatic.com/cover.jpg', 'https://is1-ssl.mzstatic.com:443/cover.jpg']) {
    assert.equal(thumbnailSource(url), undefined)
  }
  const config = JSON.parse(readFileSync(new URL('../src-tauri/tauri.conf.json', import.meta.url), 'utf8'))
  const images = config.app.security.csp.split(';').find(value => value.trim().startsWith('img-src'))
  assert.ok(images.includes('https://*.mzstatic.com'))
  assert.doesNotMatch(images, /(?:^|\s)https:\s|(?:^|\s)\*(?:\s|$)/)
})
