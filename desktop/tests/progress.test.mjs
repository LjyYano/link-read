import assert from "node:assert/strict"
import test from "node:test"
import { progressFromEvent } from "../src/progress.ts"

const progress = (stage, completed = null, total = null) => ({ stage, message: stage, completed, total })

test("progress reflects milestones and known batch completion", () => {
  assert.equal(progressFromEvent(0, progress("metadata_done")), 5)
  assert.equal(progressFromEvent(5, progress("parsed")), 9)
  assert.equal(progressFromEvent(10, progress("ai_done", 1, 5)), 17)
  assert.equal(progressFromEvent(17, progress("ai_done", 5, 5)), 45)
  assert.equal(progressFromEvent(45, progress("polish_done", 1, 4)), 55)
  assert.equal(progressFromEvent(55, progress("polish_done", 4, 4)), 85)
  assert.equal(progressFromEvent(85, progress("article_ai_done")), 94)
  assert.equal(progressFromEvent(94, progress("render")), 97)
  assert.equal(progressFromEvent(97, progress("saved_article")), 99)
})

test("progress never regresses, never displays 100 before success", () => {
  assert.equal(progressFromEvent(75, progress("article_ai")), 75)
  assert.equal(progressFromEvent(75, progress("polish_done", 1, 5)), 75)
  assert.equal(progressFromEvent(99, progress("saved_site")), 99)
  assert.equal(progressFromEvent(99, progress("article_ai_done")), 99)
  assert.equal(progressFromEvent(0, progress("ai_done", null, null)), 0)
  assert.equal(progressFromEvent(0, progress("ai_done", 1, 0)), 0)
})

test("reuse and skipped polish advance the appropriate phases", () => {
  assert.equal(progressFromEvent(10, progress("reuse")), 45)
  assert.equal(progressFromEvent(45, progress("polish_reuse")), 85)
  assert.equal(progressFromEvent(45, progress("polish_skipped")), 85)
})
