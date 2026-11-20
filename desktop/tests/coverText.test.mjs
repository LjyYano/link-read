import assert from 'node:assert/strict'
import test from 'node:test'
import { coverText } from '../src/coverText.ts'

test('local subtitles use a short title excerpt, not a giant file icon', () => {
  const result = coverText('查理·芒格：为什么 ETF 可能是散户最大的陷阱？.srt')
  assert.equal(result.label, '查理·芒格')
  assert.equal(result.compact, '查理·芒')
})

test('cover wording is bounded for long Chinese and English titles', () => {
  assert.ok(Array.from(coverText('这是一个很长很长很长的标题').label).length <= 8)
  assert.ok(Array.from(coverText('How to Connect Codex to an AI Database').label).length <= 24)
  assert.equal(coverText('How to Connect Codex to an AI Database').label, 'How to Connect')
})

test('cover palette is deterministic and handles empty or unusual titles', () => {
  const choices = ['sage', 'sand', 'slate', 'lilac']
  for (const title of [undefined, '', '!!!', '《活着》', '🌱\u0000 微小的生活', '<script>alert(1)</script>']) {
    const result = coverText(title)
    assert.ok(result.label.length > 0)
    assert.ok(choices.includes(result.tone))
    assert.deepEqual(result, coverText(title))
  }
  assert.equal(coverText('').label, '阅读笔记')
})
