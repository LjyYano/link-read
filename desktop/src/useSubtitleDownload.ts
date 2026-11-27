import { useCallback, useEffect, useReducer, useRef } from "react"
import { downloadSubtitles, normalizeGenerationFailure, type DownloadedSubtitles, type GenerationFailure } from "./api"
import { sourceCheckKey } from "./sourceReadinessState"
import type { YoutubeCookieOptions } from "./youtubeCookies"

const ttlMs = 30 * 60_000
const maxEntries = 24

type Entry = {
  data?: DownloadedSubtitles
  error?: string
  failure?: GenerationFailure
  updatedAt: number
}

class SubtitleDownloadStore {
  private entries = new Map<string, Entry>()
  private pending = new Map<string, Promise<void>>()
  private revisions = new Map<string, number>()
  private listeners = new Set<() => void>()

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }

  private notify() { this.listeners.forEach(listener => listener()) }

  peek(source: string): Entry | undefined {
    const key = sourceCheckKey(source)
    if (!key) return
    const entry = this.entries.get(key)
    if (!entry) return
    if (Date.now() - entry.updatedAt >= ttlMs) {
      this.entries.delete(key)
      return
    }
    return entry
  }

  downloading(source: string): boolean {
    const key = sourceCheckKey(source)
    return !!key && this.pending.has(key)
  }

  private save(key: string, entry: Entry) {
    this.entries.delete(key)
    this.entries.set(key, entry)
    while (this.entries.size > maxEntries) {
      const oldest = this.entries.keys().next().value!
      this.entries.delete(oldest)
      if (!this.pending.has(oldest)) this.revisions.delete(oldest)
    }
  }

  forget(source: string) {
    const key = sourceCheckKey(source)
    if (!key) return
    this.revisions.set(key, (this.revisions.get(key) ?? 0) + 1)
    this.entries.delete(key)
    if (!this.pending.has(key)) this.revisions.delete(key)
    this.notify()
  }

  clearErrors() {
    let changed = false
    for (const [key, entry] of this.entries) {
      if (entry.error && !entry.data) {
        this.entries.delete(key)
        changed = true
      }
    }
    if (changed) this.notify()
  }

  request(
    source: string,
    youtubeCookies?: YoutubeCookieOptions,
    onFailure?: (source: string, failure: GenerationFailure) => void,
  ): Promise<void> {
    const key = sourceCheckKey(source)
    if (!key) return Promise.resolve()
    const existing = this.pending.get(key)
    if (existing) return existing
    const revision = (this.revisions.get(key) ?? 0) + 1
    this.revisions.set(key, revision)
    const run = downloadSubtitles(source, youtubeCookies).then(result => {
      if (this.revisions.get(key) !== revision) return
      if (sourceCheckKey(result.source) !== key) {
        const failure: GenerationFailure = {
          message: "下载的字幕与当前链接不匹配，请重试。",
          code: "source_mismatch",
          stage: "extract",
          retryable: true,
          action: "retry",
        }
        this.save(key, { error: failure.message, failure, updatedAt: Date.now() })
        onFailure?.(source, failure)
        return
      }
      this.save(key, { data: result, updatedAt: Date.now() })
    }, error => {
      if (this.revisions.get(key) !== revision) return
      const failure = normalizeGenerationFailure(error)
      this.save(key, { error: failure.message, failure, updatedAt: Date.now() })
      onFailure?.(source, failure)
    }).finally(() => {
      if (this.pending.get(key) === run) this.pending.delete(key)
      this.notify()
    })
    this.pending.set(key, run)
    this.notify()
    return run
  }
}

const store = new SubtitleDownloadStore()

export type SubtitleDownloadState = {
  source: string
  eligible: boolean
  downloading: boolean
  downloaded?: DownloadedSubtitles
  error?: string
  failure?: GenerationFailure
  download: () => void
  forget: () => void
}

export function forgetSubtitleDownload(source: string) { store.forget(source) }
export function clearSubtitleDownloadErrors() { store.clearErrors() }

export function useSubtitleDownload(
  input: string,
  localFile: boolean,
  youtubeCookies?: YoutubeCookieOptions,
  onFailure?: (source: string, failure: GenerationFailure) => void,
): SubtitleDownloadState {
  const source = input.trim()
  const [, refresh] = useReducer(value => value + 1, 0)
  const failureCallback = useRef(onFailure)
  failureCallback.current = onFailure
  useEffect(() => store.subscribe(refresh), [])
  const eligible = !localFile && !!sourceCheckKey(source)
  const entry = eligible ? store.peek(source) : undefined
  const downloading = eligible && store.downloading(source)
  const download = useCallback(() => {
    if (eligible) void store.request(source, youtubeCookies, (src, failure) => failureCallback.current?.(src, failure))
  }, [source, eligible, youtubeCookies])
  const forget = useCallback(() => {
    if (eligible) store.forget(source)
  }, [source, eligible])
  return {
    source,
    eligible,
    downloading,
    downloaded: entry?.data,
    error: entry?.error,
    failure: entry?.failure,
    download,
    forget,
  }
}
