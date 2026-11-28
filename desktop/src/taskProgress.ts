import { tr } from "./language.ts"
import type { Progress, RequestActivity } from "./api"

export type Lane = "extract" | "audio" | "upload" | "asr" | "analysis" | "polish" | "publication" | "translation" | "render"
export type ActivityLine = { at: number; stage: string; message: string }
export type LaneState = {
  label: string
  message: string
  state: "running" | "complete"
  startedAt: number
  updatedAt: number
  completed?: number
  total?: number
}
export type TaskActivity = {
  startedAt: number
  lastEventAt: number
  lanes: Partial<Record<Lane, LaneState>>
  history: ActivityLine[]
  request?: RequestActivity
  lastResultAt?: number
}

export const laneOrder: Lane[] = ["extract", "audio", "upload", "asr", "analysis", "polish", "publication", "translation", "render"]
const labels: Record<Lane, string> = {
  extract: "提取字幕", audio: "下载音频", upload: "上传音频", asr: "语音识别", analysis: "提炼内容", polish: "校对正文",
  publication: "整理摘要与导图", translation: "正在翻译字幕", render: "生成阅读页",
}

export function laneFor(stage: string): Lane | undefined {
  if (stage.startsWith("audio_")) return "audio"
  if (stage.startsWith("asr_upload")) return "upload"
  if (stage.startsWith("asr_")) return "asr"
  if (stage === "article_ai_done" || stage.startsWith("publication_") || stage === "article_ai" || stage === "quote_warning") return "publication"
  if (stage.startsWith("polish_")) return "polish"
  if (stage.startsWith("translation_")) return "translation"
  if (stage.startsWith("ai_") || stage === "reuse") return "analysis"
  if (["render", "saved_article", "site", "saved_site", "replace_publish"].includes(stage)) return "render"
  if (["extract", "source", "redirect", "metadata", "metadata_done", "captions", "captions_done", "download", "reading", "parse", "parsed", "saved_transcript", "provider_switch", "replace"].includes(stage)) return "extract"
  return undefined
}

export function laneLabelFor(stage: string): string | undefined {
  const lane = laneFor(stage)
  if (!lane) return undefined
  if (lane === "audio" && stage.startsWith("audio_split")) return tr("分片音频")
  return tr(labels[lane])
}

const completedStages = new Set([
  "audio_download_done", "audio_split_done", "asr_upload_done", "asr_done", "asr_reuse", "saved_transcript", "ai_saved", "reuse", "polish_complete", "polish_reuse", "polish_skipped",
  "publication_done", "article_ai_done", "saved_article", "saved_site",
])

/** Every actual event is recorded, including out-of-order parallel stages.
 * Timer ticks never call this reducer: elapsed time is not model progress.
 */
export function recordProgress(previous: TaskActivity | undefined, event: Progress, now = Date.now()): TaskActivity {
  const activity = previous ?? { startedAt: now, lastEventAt: now, lanes: {}, history: [] }
  if (event.request && activity.request && event.request.sequence <= activity.request.sequence) return activity
  const lanes = { ...activity.lanes }
  const lane = laneFor(event.stage)
  // ASR replaces (rather than runs alongside) subtitle extraction.
  if (event.stage === "asr_start" && lanes.extract) lanes.extract = { ...lanes.extract, state: "complete" }
  if (event.stage === "audio_download" && lanes.asr) delete lanes.asr
  if (event.stage === "saved_transcript") {
    for (const key of ["audio", "upload", "asr"] as Lane[]) {
      const entry = lanes[key]
      if (entry) lanes[key] = { ...entry, state: "complete" }
    }
  }
  if (lane) {
    const old = lanes[lane]
    // Splitting is a new local operation after the download has completed.
    // Reopen only on this explicit transition, not a late download callback.
    const restartingAudio = event.stage === "audio_split" && old?.state === "complete"
    const counted = event.completed != null && event.total != null && event.total > 0
    const completed = counted ? Math.max(old?.completed ?? 0, event.completed!) : old?.completed
    const total = counted ? event.total! : old?.total
    const complete = (!restartingAudio && old?.state === "complete") || completedStages.has(event.stage)
      || (counted && ["ai_done", "polish_done", "translation_done"].includes(event.stage) && completed! >= total!)
    lanes[lane] = {
      label: lane === "audio" && event.stage.startsWith("audio_split") ? "分片音频" : labels[lane], message: event.message, state: complete ? "complete" : "running",
      startedAt: old?.startedAt ?? now, updatedAt: now, completed, total,
    }
  }
  const history = activity.history.slice()
  const recordInHistory = !event.request || event.request.phase === "failed"
  if (recordInHistory && history.at(-1)?.message !== event.message) {
    history.push({ at: now, stage: event.stage, message: event.message })
  }
  const result = event.request && ["complete", "cache", "received"].includes(event.request.phase)
  return { ...activity, lastEventAt: now, lanes, history: history.slice(-12),
    ...(event.request ? { request: event.request } : {}),
    lastResultAt: result ? now : activity.lastResultAt }
}

export function activeLanes(activity?: TaskActivity): LaneState[] {
  return laneOrder.flatMap(lane => {
    const entry = activity?.lanes[lane]
    return entry?.state === "running" ? [{ ...entry, label: tr(entry.label) }] : []
  })
}

type TaskStatus = "queued" | "running" | "cancelling" | "error" | "cancelled" | "interrupted" | "done"
type StageState = "running" | "complete" | "failed" | "stopped" | "stopping"
type StageRow = Omit<LaneState, "state"> & { key: Lane; state: StageState; statusLabel: string }

/** The task's terminal state takes precedence over its last progress snapshot.
 * Completed stages stay complete. With parallel unfinished stages, the backend
 * error does not identify which one failed, so do not invent that attribution.
 */
export function stageRows(activity: TaskActivity | undefined, status: TaskStatus, failedStage?: string | null): StageRow[] {
  const running = activeLanes(activity).length
  const failedLane = laneOrder.includes(failedStage as Lane) ? failedStage as Lane : failedStage ? laneFor(failedStage) : undefined
  return laneOrder.flatMap(key => {
    const lane = activity?.lanes[key]
    if (!lane) return []
    const state: StageState = lane.state === "complete" ? "complete"
      : status === "running" ? "running"
      : status === "cancelling" ? "stopping"
      : status === "error" && (failedLane ? key === failedLane : running === 1) ? "failed" : "stopped"
    const labels = { running: tr("处理中"), complete: tr("已完成"), failed: tr("失败"), stopped: tr("已停止"), stopping: tr("正在停止") }
    const count = state !== "complete" && lane.total
      ? tr(" · 已完成 {0}/{1} 批", Math.min(lane.completed ?? 0, lane.total), lane.total) : ""
    return [{ ...lane, label: tr(lane.label), key, state, statusLabel: labels[state] + count }]
  })
}

/** Native/model events own these counts; a timer only describes time since a result. */
export function requestCaption(activity: TaskActivity | undefined, now: number): string | undefined {
  const state = activity?.request
  if (!state) return undefined
  const parts: string[] = []
  if (state.active) parts.push(tr("{0} 项等待模型响应", state.active))
  if (state.queued) parts.push(tr("{0} 项等待并发空位", state.queued))
  if (state.validating) parts.push(tr("{0} 项正在校验", state.validating))
  if (!parts.length) return undefined
  if (activity?.lastResultAt) {
    const seconds = Math.max(0, Math.floor((now - activity.lastResultAt) / 1000))
    if (seconds >= 20) parts.push(tr("上次结果 {0} 秒前", seconds))
  }
  return parts.join(" · ")
}

export function progressCaption(activity: TaskActivity | undefined, fallback: string): string {
  const active = activeLanes(activity)
  if (!active.length) return fallback
  const current = active[0]
  const count = current.total ? tr(" · 已完成 {0}/{1} 批", Math.min(current.completed ?? 0, current.total), current.total) : ""
  return current.label + count
}

/** Batch positions are not completion counts (parallel requests finish out of
 * order). Keep the exact event in details; the main card shows counts only in
 * its caption, including during verification, completion and retries.
 */
export function progressDetail(activity: TaskActivity | undefined, fallback: string): string {
  const current = activeLanes(activity)[0]
  const message = current?.message || fallback
  if (/^(AI 正在(?:校对|提炼)|已调度(?:校对|提炼)批次)/.test(message)) {
    if (current?.label === tr("校对正文")) return tr("逐批校对并校验原文字词，完成后按顺序合并")
    if (current?.label === tr("提炼内容")) return tr("各批次独立处理，完成后按原文顺序合并")
  }
  const detail = message.replace(/^第\s*\d+\s*\/\s*\d+\s*(?:批|块)(?:中)?\s*[，,:：]?\s*/, "")
  if (detail !== message) {
    if (/^(?:提炼|校对)?完成[。！!]?$/u.test(detail)) return tr("一批内容已处理完成")
    return detail
  }
  return message
}

export function elapsedLabel(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000))
  return `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${(seconds % 60).toString().padStart(2, "0")}`
}
