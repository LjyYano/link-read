import assert from "node:assert/strict"
import test from "node:test"
import { activeLanes, elapsedLabel, recordProgress, progressCaption, progressDetail, stageRows } from "../src/taskProgress.ts"
import { ConnectionRevision } from "../src/connectionState.ts"

const event = (stage, message = stage, completed = null, total = null) => ({ stage, message, completed, total })

test("preserve batch counters across starts, retries and parallel publication messages", () => {
  let state = recordProgress(undefined, event("polish_start", "开始校对", 0, 5), 1000)
  state = recordProgress(state, event("polish_done", "第一批完成", 1, 5), 2000)
  state = recordProgress(state, event("article_ai", "正在整理摘要和导图"), 2100)
  state = recordProgress(state, event("polish_batch", "第 2 批开始"), 2200)
  state = recordProgress(state, event("polish_retry", "保留原词重新补标点"), 2300)
  assert.equal(state.lanes.polish.completed, 1)
  assert.equal(state.lanes.polish.total, 5)
  assert.equal(activeLanes(state).length, 2)
  assert.equal(progressCaption(state, "等待"), "校对正文 · 已完成 1/5 批")
  assert.equal(state.history.at(-1).message, "保留原词重新补标点")
})

test("late events never reopen completed lanes; publication can finish before polish", () => {
  let state = recordProgress(undefined, event("polish_start", "校对", 0, 2), 1000)
  state = recordProgress(state, event("article_ai", "整理"), 1200)
  state = recordProgress(state, event("publication_done", "摘要完成"), 1300)
  assert.deepEqual(activeLanes(state).map(lane => lane.label), ["校对正文"])
  state = recordProgress(state, event("polish_done", "完成", 2, 2), 1400)
  state = recordProgress(state, event("polish_done", "晚到的消息", 1, 2), 1500)
  assert.equal(state.lanes.polish.completed, 2)
  assert.equal(state.lanes.polish.state, "complete")
  assert.equal(state.history.at(-1).message, "晚到的消息")
})

test("elapsed clocks do not invent activity and history is bounded", () => {
  let state = recordProgress(undefined, event("ai_start", "等待", 0, 4), 0)
  const unchanged = JSON.stringify(state)
  assert.equal(elapsedLabel(65000), "01:05")
  assert.equal(elapsedLabel(-1), "00:00")
  assert.equal(JSON.stringify(state), unchanged)
  for (let i = 0; i < 30; i++) state = recordProgress(state, event("ai_chunk", `真实事件 ${i}`), i + 1)
  assert.equal(state.history.length, 12)
  assert.equal(state.startedAt, 0)
  assert.equal(state.lanes.analysis.completed, 0)
})

test("dispatch order never appears as another completion count on the main card", () => {
  let state = recordProgress(undefined, event("ai_start", "开始提炼", 0, 3), 1000)
  state = recordProgress(state, event("ai_done", "完成一批", 1, 3), 2000)
  state = recordProgress(state, event("ai_chunk", "已调度提炼批次 3/3（20 条字幕）"), 2100)
  assert.equal(progressCaption(state, "等待"), "提炼内容 · 已完成 1/3 批")
  assert.equal(progressDetail(state, "等待"), "各批次独立处理，完成后按原文顺序合并")
  assert.equal(state.history.at(-1).message, "已调度提炼批次 3/3（20 条字幕）")
})

test("proofreading copy hides dispatch fractions but preserves useful warnings", () => {
  let state = recordProgress(undefined, event("polish_start", "开始校对", 0, 7), 1000)
  state = recordProgress(state, event("polish_batch", "AI 正在校对第 4/7 块"), 1100)
  assert.equal(progressDetail(state, "等待"), "逐批校对并校验原文字词，完成后按顺序合并")
  state = recordProgress(state, event("polish_retry", "校对结果校验失败，正在重试"), 1200)
  assert.equal(progressDetail(state, "等待"), "校对结果校验失败，正在重试")
  assert.equal(progressDetail(undefined, "真实后端消息"), "真实后端消息")
})

test("out-of-order completion and verification show one actual completion count", () => {
  let state = recordProgress(undefined, event("polish_start", "开始校对", 0, 5), 1000)
  state = recordProgress(state, event("polish_done", "第 3/5 批完成", 1, 5), 1100)
  assert.equal(progressCaption(state, "等待"), "校对正文 · 已完成 1/5 批")
  assert.equal(progressDetail(state, "等待"), "一批内容已处理完成")
  assert.equal(state.history.at(-1).message, "第 3/5 批完成")
  state = recordProgress(state, event("polish_verify", "第 5/5 批已收到结果，正在核对原文文字"), 1200)
  assert.equal(progressCaption(state, "等待"), "校对正文 · 已完成 1/5 批")
  assert.equal(progressDetail(state, "等待"), "已收到结果，正在核对原文文字")
  state = recordProgress(state, event("polish_retry", "第 5/5 批中有 2 段被 AI 改动了文字，正在重新补标点"), 1300)
  assert.equal(progressDetail(state, "等待"), "有 2 段被 AI 改动了文字，正在重新补标点")
  assert.equal(state.history.at(-1).message, "第 5/5 批中有 2 段被 AI 改动了文字，正在重新补标点")
  assert.equal(progressDetail(undefined, "第 3/5 块提炼完成"), "一批内容已处理完成")
})

test("connection test responses are invalidated when settings change", () => {
  const revision = new ConnectionRevision()
  const old = revision.snapshot()
  assert.ok(revision.accepts(old))
  revision.invalidate()
  assert.equal(revision.accepts(old), false)
  const current = revision.snapshot()
  assert.ok(revision.accepts(current))
  revision.invalidate() // unmount or another edit
  assert.equal(revision.accepts(current), false)
})

test("an extraction failure replaces the last running stage with an explicit failed state", () => {
  const activity = recordProgress(undefined, event("captions", "正在检查该单集发布的公开字幕"), 1000)
  const original = JSON.stringify(activity)
  assert.deepEqual(stageRows(activity, "running").map(row => [row.state, row.statusLabel]), [["running", "处理中"]])
  assert.deepEqual(stageRows(activity, "error").map(row => [row.state, row.statusLabel]), [["failed", "失败"]])
  assert.deepEqual(stageRows(activity, "cancelled").map(row => [row.state, row.statusLabel]), [["stopped", "已停止"]])
  assert.deepEqual(stageRows(activity, "cancelling").map(row => [row.state, row.statusLabel]), [["stopping", "正在停止"]])
  assert.equal(JSON.stringify(activity), original, "terminal presentation preserves the actual event history")
})

test("terminal tasks stop every unfinished parallel stage and preserve completed stages and counts", () => {
  let activity = recordProgress(undefined, event("saved_transcript", "已保存字幕"), 1000)
  activity = recordProgress(activity, event("ai_start", "提炼", 0, 5), 1100)
  activity = recordProgress(activity, event("ai_done", "一批完成", 1, 5), 1200)
  activity = recordProgress(activity, event("polish_start", "校对", 0, 8), 1300)
  for (const status of ["error", "cancelled", "done", "queued"]) {
    const rows = stageRows(activity, status)
    assert.equal(rows[0].state, "complete")
    assert.equal(rows[0].statusLabel, "已完成")
    assert.deepEqual(rows.slice(1).map(row => row.state), ["stopped", "stopped"])
    assert.equal(rows[1].statusLabel, "已停止 · 已完成 1/5 批")
    assert.ok(rows.every(row => !row.statusLabel.includes("处理中")))
  }
  assert.deepEqual(stageRows(undefined, "error"), [])
  assert.deepEqual(stageRows(activity, "running").slice(1).map(row => row.state), ["running", "running"])
})
