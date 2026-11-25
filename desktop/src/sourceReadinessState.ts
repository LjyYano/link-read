import { tr } from "./language.ts"
import type { GenerationFailure, SourceCheck } from "./api"
import { previewKey } from "./previewState.ts"
import { normalizeVideoLink } from "./videoLinks.ts"

const blockedCodes = new Set(["protected_media", "unsupported_audio_stream"])
export const sourceCheckTtl = 10 * 60_000

export function sourceCheckKey(source: string) {
  return previewKey(source)
}

/** An explicit check is scoped to a source, never a title or the latest input. */
export class SourceReadinessRequests {
  private cache = new Map<string, SourceCheck>()
  private pending = new Map<string, Promise<void>>()
  private revisions = new Map<string, number>()
  private listeners = new Set<() => void>()
  private now: () => number
  private capacity: number
  constructor(now = Date.now, capacity = 32) { this.now = now; this.capacity = capacity }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }

  private notify() { this.listeners.forEach(listener => listener()) }

  peek(source: string): SourceCheck | undefined {
    const key = sourceCheckKey(source)
    if (!key) return
    const result = this.cache.get(key)
    if (!result) return
    if (this.now() - result.checkedAt >= sourceCheckTtl) {
      this.cache.delete(key)
      return
    }
    return result
  }

  checking(source: string) {
    const key = sourceCheckKey(source)
    return !!key && this.pending.has(key)
  }

  private save(key: string, result: SourceCheck) {
    this.cache.delete(key)
    this.cache.set(key, result)
    while (this.cache.size > this.capacity) {
      const oldest = this.cache.keys().next().value!
      this.cache.delete(oldest)
      if (!this.pending.has(oldest)) this.revisions.delete(oldest)
    }
  }

  forget(source: string) {
    const key = sourceCheckKey(source)
    if (!key) return
    this.revisions.set(key, (this.revisions.get(key) ?? 0) + 1)
    this.cache.delete(key)
    if (!this.pending.has(key)) this.revisions.delete(key)
    this.notify()
  }

  rememberFailure(source: string, failure: GenerationFailure, checkedAt = this.now()) {
    const key = sourceCheckKey(source)
    const subtitleLogin = failure.code === "subtitle_login_required"
    if (!key || (!subtitleLogin && !blockedCodes.has(failure.code))) return
    if (!Number.isFinite(checkedAt) || checkedAt <= 0 || checkedAt > this.now() + 60_000
        || this.now() - checkedAt >= sourceCheckTtl) return
    this.revisions.set(key, (this.revisions.get(key) ?? 0) + 1)
    this.save(key, { source: normalizeVideoLink(source), status: subtitleLogin ? "needs_asr" : "blocked", code: failure.code,
      message: subtitleLogin ? tr("B 站要求登录后查看字幕。生成时可使用语音识别转写公开音轨。") : failure.message,
      checkedAt, origin: "generation" })
    this.notify()
  }

  request(source: string, load: () => Promise<SourceCheck>): Promise<void> {
    const key = sourceCheckKey(source)
    if (!key) return Promise.resolve()
    const existing = this.pending.get(key)
    if (existing) return existing
    const revision = (this.revisions.get(key) ?? 0) + 1
    this.revisions.set(key, revision)
    // Keep a known restriction visible until a fresh check actually supersedes
    // it. Starting a recheck must not briefly re-enable a known futile job.
    let deadline: ReturnType<typeof setTimeout> | undefined
    const unknown = (code: string, message: string): SourceCheck => ({ source: normalizeVideoLink(source),
      status: "unknown", code, message, checkedAt: this.now() })
    const timeout = new Promise<SourceCheck>(resolve => {
      deadline = setTimeout(() => resolve(unknown("source_check_timeout",
        tr("来源检查暂时超时，尚未确认内容是否可用；请重试检查或查看来源。"))), 38_000)
    })
    const request = Promise.race([Promise.resolve().then(load), timeout]).then(result => {
      if (this.revisions.get(key) !== revision) return
      if (sourceCheckKey(result.source) !== key
          || !["captions", "needs_asr", "blocked", "unknown"].includes(result.status)
          || (result.status === "blocked" && !blockedCodes.has(result.code))) {
        this.save(key, unknown("source_check_mismatch", tr("检查结果与当前来源不一致，请重新检查。")))
        return
      }
      this.save(key, { ...result, checkedAt: this.now(), origin: "check" })
    }, () => {
      if (this.revisions.get(key) === revision) this.save(key,
        unknown("source_check_failed", tr("暂时无法完成来源检查，请重试检查或查看来源。")))
    }).finally(() => {
      clearTimeout(deadline)
      if (this.pending.get(key) === request) this.pending.delete(key)
      if (!this.cache.has(key)) this.revisions.delete(key)
      this.notify()
    })
    this.pending.set(key, request)
    this.notify()
    return request
  }
}
