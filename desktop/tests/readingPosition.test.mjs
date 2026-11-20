import test from "node:test"
import assert from "node:assert/strict"
import { createRequire } from "node:module"

const require = createRequire(import.meta.url)
const { fingerprint, lexical, visibleCenter, sanitize, validMindmap, resolveAnchor } = require("../../src/linkread_desktop/renderers/reading-position.js")
const token = text => fingerprint(lexical(text))
const anchor = (cue, endCue, text, occurrence = 0) => ({ kind: "cue", cue, endCue, token: token(text), occurrence })
const position = (a, signature = token("完整原文")) => ({ signature, anchor: a, offset: 30, ratio: 0.25 })
const state = () => ({
  version: 1, updatedAt: 1730000000000, activeTab: "transcript",
  tabs: { transcript: position(anchor(8, 10, "正在阅读的内容")) },
  mindmap: null,
})

test("punctuation-only proofreading and regrouping keep a lexical source fingerprint", () => {
  assert.equal(token("原文：第一段。第二段，完整保留！"), token("原文第一段\n\n第二段完整保留"))
  assert.notEqual(token("原文完整保留"), token("原文有所改变"))
  assert.notEqual(token("金额 ¥100 😀"), token("金额 $100 😢"), "symbols are source content, not punctuation")
  assert.match(token("中文与 English 42"), /^p1-[0-9a-f]{16}-[0-9]+$/)
})

test("raw transcript resumes in a newly grouped paragraph by exact source cue", () => {
  const saved = position(anchor(8, 10, "旧版分段"))
  const regrouped = [anchor(0, 7, "开头"), anchor(8, 11, "重新组织但保留原文"), anchor(12, 16, "后续")]
  const resolved = resolveAnchor(saved, regrouped, saved.signature)
  assert.equal(resolved.anchor, regrouped[1])
  assert.equal(resolved.within, false, "old paragraph pixel offset must not apply to new grouping")
})

test("a cue at the middle of a newly grouped paragraph is preserved without guessing a neighbour", () => {
  const saved = position(anchor(10, 12, "旧版分段"))
  const regrouped = [anchor(0, 5, "开头"), anchor(8, 11, "包含原 cue"), anchor(12, 16, "后续")]
  assert.equal(resolveAnchor(saved, regrouped, saved.signature).anchor, regrouped[1])
  assert.equal(resolveAnchor(position(anchor(6, 7, "已删除")), regrouped, saved.signature), null)
})

test("matching paragraph text keeps its within-paragraph offset", () => {
  const saved = position(anchor(8, 10, "原文保持一致。"))
  const same = anchor(8, 10, "原文保持一致！")
  assert.deepEqual(resolveAnchor(saved, [same], saved.signature), { anchor: same, within: true })
})

test("actual source changes invalidate old cue numbers and only accept unique exact text", () => {
  const saved = position(anchor(8, 10, "阅读目标"))
  const changed = token("完全不同的原文")
  assert.equal(resolveAnchor(saved, [anchor(8, 10, "不同内容")], changed), null)
  const moved = anchor(20, 22, "阅读目标")
  assert.deepEqual(resolveAnchor(saved, [moved], changed), { anchor: moved, within: true })
  assert.equal(resolveAnchor(saved, [moved, anchor(30, 32, "阅读目标", 1)], changed), null)
})

test("repeated Shownotes blocks use occurrence only when the content is unchanged", () => {
  const first = { kind: "block", token: token("重复标题"), occurrence: 0 }
  const second = { ...first, occurrence: 1 }
  const saved = position(second)
  assert.equal(resolveAnchor(saved, [first, second], saved.signature).anchor, second)
  assert.equal(resolveAnchor(saved, [first, second], token("新增的简介")), null)
})

test("valid records are copied and reading completion flags cannot be smuggled into position state", () => {
  const original = state()
  assert.deepEqual(sanitize(original), original)
  assert.notEqual(sanitize(original).tabs, original.tabs)
  assert.equal(sanitize({ ...original, isRead: true }), null)
  assert.equal(sanitize({ ...original, activeTab: "notes" }), null)
  assert.equal(sanitize({ ...original, version: 2 }), null)
  assert.equal(sanitize({ ...original, tabs: { notes: original.tabs.transcript } }), null)
})

test("invalid and oversized anchors are rejected before DOM or native use", () => {
  for (const invalid of [
    { ...anchor(8, 10, "目标"), cue: -1 },
    { ...anchor(8, 10, "目标"), endCue: 7 },
    { ...anchor(8, 10, "目标"), token: "<script>" },
    { ...anchor(8, 10, "目标"), occurrence: Infinity },
    { kind: "top", selector: "body" },
  ]) {
    const value = state()
    value.tabs.transcript.anchor = invalid
    assert.equal(sanitize(value), null)
  }
  for (const invalid of [{ ratio: 2 }, { offset: NaN }, { offset: 100001 }, { signature: "" }]) {
    const value = state()
    Object.assign(value.tabs.transcript, invalid)
    assert.equal(sanitize(value), null)
  }
})

test("mindmap records store bounded paths and a view centre, never executable labels", () => {
  const map = {
    signature: token("主要脉络"), folded: ["0.1", "0.2.0"], mode: "custom",
    viewport: { engine: "markmap", cx: 245, cy: -80, scale: 0.9 },
  }
  assert.equal(validMindmap(map), true)
  assert.equal(validMindmap({ ...map, folded: ["0.__proto__"] }), false)
  assert.equal(validMindmap({ ...map, folded: Array(2049).fill("0.1") }), false)
  assert.equal(validMindmap({ ...map, mode: "automatic" }), false)
  assert.equal(validMindmap({ ...map, viewport: { ...map.viewport, scale: 0 } }), false)
  assert.equal(validMindmap({ ...map, viewport: { ...map.viewport, cx: Infinity } }), false)
  assert.equal(validMindmap({ ...map, viewport: null }), true)
  assert.equal(validMindmap({ ...map, content: "<img>" }), false)
})

test("a panned mindmap centre outside its bounds stays unchanged while the viewport still intersects it", () => {
  const saved = 62.67722446586764, bottom = 56.968750000000014
  assert.equal(visibleCenter(saved, -100, bottom, 640 / (2 * .853837597330367)), saved)
  assert.equal(visibleCenter(-30, 0, 300, 50), -30, "fallback graphs preserve the same visible pan")
  assert.equal(visibleCenter(350, 0, 300, 50), 350, "touching the boundary is still visible")
})

test("a completely offscreen map is brought back after a drastic resize or collapse", () => {
  assert.equal(visibleCenter(400, 0, 300, 50), 300)
  assert.equal(visibleCenter(-100, 0, 300, 50), 0)
})
