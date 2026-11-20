import type { Progress } from "./api"

/**
 * A monotonic *workflow completion estimate*, not an elapsed-time forecast.
 * Individual model calls can take unpredictable amounts of time.
 * Keep 100% reserved for the backend's confirmed "completed" message.
 */
export function progressFromEvent(previous: number, event: Progress): number {
  const stages: Record<string, number> = {
    extract: 1, source: 2, redirect: 2, metadata: 3, metadata_done: 5,
    captions: 6, captions_done: 7, download: 8, reading: 8,
    parse: 9, parsed: 9, saved_transcript: 10,
    ai_start: 10, ai_chunk: 10, ai_saved: 45, reuse: 45,
    polish_start: 45, polish_batch: 45, polish_retry: 45,
    polish_warning: 85, polish_complete: 85, polish_reuse: 85, polish_skipped: 85,
    article_ai_done: 94, render: 97, saved_article: 99,
    site: 99, saved_site: 99,
  }
  let next = stages[event.stage] ?? previous
  if ((event.stage === "ai_done" || event.stage === "polish_done")
      && event.completed != null && event.total != null && event.total > 0) {
    const ratio = Math.min(1, Math.max(0, event.completed / event.total))
    next = event.stage === "ai_done" ? 10 + Math.round(35 * ratio) : 45 + Math.round(40 * ratio)
  }
  return Math.min(99, Math.max(0, previous, next))
}
