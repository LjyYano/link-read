import assert from 'node:assert/strict'
import test from 'node:test'
import { SourceReadinessRequests, sourceCheckKey, sourceCheckTtl } from '../src/sourceReadinessState.ts'

const source = 'https://podcasts.apple.com/cn/podcast/id6788661772?i=1000775971124'
const other = 'https://podcasts.apple.com/cn/podcast/id6788661772?i=1000775971125'
const captions = value => ({ source: value, status: 'captions', code: 'public_captions', message: 'Verified', checkedAt: 1000 })
const failure = { code: 'protected_media', message: 'Protected', retryable: false, action: 'source' }
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }

test('reading readiness never starts a check and has a source-specific identity', () => {
  const checks = new SourceReadinessRequests(() => 1000)
  assert.equal(checks.peek(source), undefined)
  assert.equal(checks.checking(source), false)
  assert.equal(sourceCheckKey('not a link'), undefined)
  assert.notEqual(sourceCheckKey(source), sourceCheckKey(other))
  assert.notEqual(sourceCheckKey(source), sourceCheckKey(source.replace('/cn/', '/us/')))
})

test('same-source explicit checks are deduplicated; old results cannot show for another episode', async () => {
  const checks = new SourceReadinessRequests(() => 1000)
  const pending = deferred()
  let calls = 0
  const a = checks.request(source, () => { calls++; return pending.promise })
  const b = checks.request(source, () => { calls++; return pending.promise })
  assert.equal(a, b)
  await Promise.resolve()
  assert.equal(calls, 1)
  assert.equal(checks.peek(other), undefined)
  pending.resolve(captions(source)); await a
  assert.equal(checks.peek(source).status, 'captions')
  assert.equal(checks.peek(other), undefined)
})

test('check again requests fresh data instead of accepting a cached result', async () => {
  const checks = new SourceReadinessRequests(() => 1000)
  checks.rememberFailure(source, failure)
  const pending = deferred()
  const request = checks.request(source, () => pending.promise)
  assert.equal(checks.checking(source), true)
  assert.equal(checks.peek(source).status, 'blocked')
  pending.resolve(captions(source)); await request
  assert.equal(checks.peek(source).status, 'captions')
  assert.equal(checks.peek(source).origin, 'check')
})

test('new task evidence wins over an earlier in-flight source check', async () => {
  const checks = new SourceReadinessRequests(() => 1000)
  const pending = deferred()
  const request = checks.request(source, () => pending.promise)
  checks.rememberFailure(source, failure)
  pending.resolve(captions(source)); await request
  assert.equal(checks.peek(source).status, 'blocked')
  assert.equal(checks.peek(source).origin, 'generation')
})

test('success invalidates obsolete restrictions, including late check responses', async () => {
  const checks = new SourceReadinessRequests(() => 1000)
  const pending = deferred()
  const request = checks.request(source, () => pending.promise)
  checks.forget(source)
  pending.resolve(captions(source)); await request
  assert.equal(checks.peek(source), undefined)
})

test('unknown or mismatched results never become a source restriction', async () => {
  const checks = new SourceReadinessRequests(() => 1000)
  checks.rememberFailure(source, { ...failure, code: 'source_timeout' })
  assert.equal(checks.peek(source), undefined)
  await checks.request(source, async () => captions(other))
  assert.equal(checks.peek(source).status, 'unknown')
  await checks.request(source, async () => ({ ...captions(source), status: 'blocked', code: 'network_error' }))
  assert.equal(checks.peek(source).status, 'unknown')
  await checks.request(source, async () => { throw Error('remote sensitive output') })
  assert.equal(checks.peek(source).status, 'unknown')
  assert.ok(!checks.peek(source).message.includes('remote sensitive'))
})

test('old results expire and restored timestamps are not replaced by now', () => {
  let now = 1000
  const checks = new SourceReadinessRequests(() => now)
  checks.rememberFailure(source, failure, 900)
  assert.equal(checks.peek(source).checkedAt, 900)
  now = 900 + sourceCheckTtl
  assert.equal(checks.peek(source), undefined)
})

test('Bilibili subtitle sign-in can use configured ASR and does not mask protected audio', async () => {
  const video = 'https://www.bilibili.com/video/BV1rxqmBhE91?p=2'
  const checks = new SourceReadinessRequests(() => 1000)
  checks.rememberFailure(video, { ...failure, code: 'subtitle_login_required' })
  assert.equal(checks.peek(video).status, 'needs_asr')
  await checks.request(video, async () => ({
    source: video, status: 'needs_asr', code: 'subtitle_login_required', message: 'Speech fallback', checkedAt: 1000,
  }))
  assert.equal(checks.peek(video).origin, 'check')
  checks.rememberFailure(video, failure)
  assert.equal(checks.peek(video).status, 'blocked')
})

test('cache remains bounded and listeners observe explicit updates only', () => {
  const checks = new SourceReadinessRequests(() => 1000, 1)
  let changed = 0
  const stop = checks.subscribe(() => changed++)
  checks.rememberFailure(source, failure)
  checks.rememberFailure(other, failure)
  assert.equal(checks.peek(source), undefined)
  assert.equal(checks.peek(other).status, 'blocked')
  assert.equal(changed, 2)
  stop(); checks.forget(other)
  assert.equal(changed, 2)
})
