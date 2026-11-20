import assert from 'node:assert/strict'
import test from 'node:test'
import { previewKey, PreviewRevision } from '../src/previewState.ts'
import { defaultAsr, loadAsrOptions } from '../src/asrSettings.ts'

test('preview canonicalizes YouTube tracking links and Bilibili parts', () => {
  assert.equal(previewKey('https://youtu.be/1-Fo8McCD9w?si=x'), previewKey('https://www.youtube.com/watch?v=1-Fo8McCD9w'))
  assert.notEqual(previewKey('https://www.bilibili.com/video/BV1rxqmBhE91?p=1'), previewKey('https://www.bilibili.com/video/BV1rxqmBhE91?p=2'))
  assert.equal(previewKey('https://www.youtube.com/watch?v=partial'), undefined)
  assert.equal(previewKey('https://www.youtube.com.evil.example/watch?v=1-Fo8McCD9w'), undefined)
})
test('late preview request cannot replace newer input', () => {
  const revision = new PreviewRevision()
  const a=revision.next(),b=revision.next()
  assert.equal(revision.accepts(a),false)
  assert.equal(revision.accepts(b),true)
  revision.next();assert.equal(revision.accepts(b),false)
})
test('ASR is opt-in for old installations, never silently reuses a token', () => {
  assert.equal(defaultAsr.enabled,false)
  assert.equal(loadAsrOptions(undefined).reuseToken,false)
  assert.equal(loadAsrOptions({ enabled:true,maxMinutes:-3 }).maxMinutes,120)
})
