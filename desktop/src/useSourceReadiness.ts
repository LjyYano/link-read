import { useCallback, useEffect, useReducer } from "react"
import { checkSource, type GenerationFailure, type SourceCheck } from "./api"
import { SourceReadinessRequests, sourceCheckKey, sourceCheckTtl } from "./sourceReadinessState"
import type { YoutubeCookieOptions } from "./youtubeCookies"

const requests = new SourceReadinessRequests()

export type SourceReadiness = {
  source: string
  eligible: boolean
  phase: "idle" | "checking" | "checked"
  result?: SourceCheck
  blocked: boolean
  check: () => void
}

export function rememberSourceFailure(source: string, failure: GenerationFailure, checkedAt?: number) {
  requests.rememberFailure(source, failure, checkedAt)
}

export function forgetSourceReadiness(source: string) { requests.forget(source) }

export function useSourceReadiness(
  input: string,
  localFile: boolean,
  youtubeCookies?: YoutubeCookieOptions,
): SourceReadiness {
  const source = input.trim()
  const [, refresh] = useReducer(value => value + 1, 0)
  useEffect(() => requests.subscribe(refresh), [])
  const eligible = !localFile && !!sourceCheckKey(source)
  const result = eligible ? requests.peek(source) : undefined
  const checking = eligible && requests.checking(source)
  useEffect(() => {
    if (!result) return
    const timer = window.setTimeout(refresh, Math.max(0, result.checkedAt + sourceCheckTtl - Date.now()) + 1)
    return () => window.clearTimeout(timer)
  }, [result])
  const check = useCallback(() => {
    if (eligible) void requests.request(source, () => checkSource(source, youtubeCookies))
  }, [source, eligible, youtubeCookies])
  return { source, eligible, result, phase: checking ? "checking" : result ? "checked" : "idle",
    blocked: result?.status === "blocked", check }
}
