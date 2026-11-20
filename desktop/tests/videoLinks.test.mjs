import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { normalizeVideoLink } from '../src/videoLinks.ts'
import { previewKey } from '../src/previewState.ts'
const cases = JSON.parse(readFileSync(new URL('../../tests/fixtures/video_links.json', import.meta.url), 'utf8'))
test('preview and generation accept shared text, escaped URLs and watchlater', () => {
  for (const row of cases) {
    if (row.output === null) { assert.throws(() => normalizeVideoLink(row.input), row.input); assert.equal(previewKey(row.input), undefined) }
    else { assert.equal(normalizeVideoLink(row.input), row.output); assert.equal(previewKey(row.input), previewKey(row.output)) }
  }
})
