import assert from 'node:assert/strict'
import test from 'node:test'
import { locale, localizedDetail, setLocale, translateDetail } from '../src/language.ts'
import { normalizeGenerationFailure } from '../src/api.ts'

test('translateDetail matches whole diagnostics and Rust head（detail）compositions', () => {
  setLocale('en')
  assert.equal(locale(), 'en')
  assert.equal(translateDetail('LinkRead 核心提前退出，请重试'), 'The LinkRead core exited early. Try again.')
  assert.equal(
    translateDetail('无法启动 LinkRead 核心（No such file or directory (os error 2)）'),
    'Could not start the LinkRead core（No such file or directory (os error 2)）',
  )
  // Nothing is invented for diagnostics without a dictionary entry.
  assert.equal(translateDetail('资料库目录不可用'), '资料库目录不可用')
  assert.equal(translateDetail('already english'), 'already english')
  setLocale('zh-CN')
  assert.equal(translateDetail('LinkRead 核心提前退出，请重试'), 'LinkRead 核心提前退出，请重试')
})

test('localizedDetail uses the fallback only when an entry is missing', () => {
  setLocale('en')
  assert.equal(localizedDetail('读取资料库超时，请重试', 'fallback'), 'Reading the library timed out. Try again.')
  assert.equal(localizedDetail('无法读取资料库（disk full）', 'fallback'), 'Could not read the library（disk full）')
  assert.equal(localizedDetail('完全未收录的提示', 'fallback'), 'fallback')
  setLocale('zh-CN')
  assert.equal(localizedDetail('完全未收录的提示', 'fallback'), '完全未收录的提示')
})

test('normalizeGenerationFailure translates Rust-origin messages in English', () => {
  setLocale('en')
  const failure = normalizeGenerationFailure('LinkRead 核心异常退出，请重试')
  assert.equal(failure.message, 'The LinkRead core exited unexpectedly. Try again.')
  assert.equal(failure.code, 'unknown')
  assert.equal(normalizeGenerationFailure('已取消生成').code, 'cancelled')
  const structured = normalizeGenerationFailure({ message: '无法取消生成，请重试', code: 'core', retryable: true })
  assert.equal(structured.message, 'Could not cancel generation. Try again.')
  assert.equal(structured.code, 'core')
  // Sidecar failures already arrive localized; the dictionary must not alter them.
  const localized = normalizeGenerationFailure({ message: 'No usable subtitles are available for this source.', code: 'subtitles_unavailable' })
  assert.equal(localized.message, 'No usable subtitles are available for this source.')
  setLocale('zh-CN')
  assert.equal(normalizeGenerationFailure('已取消生成').code, 'cancelled')
})
