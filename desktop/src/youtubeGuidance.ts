/** YouTube subtitle-first flow: require cookies before suggesting paid ASR. */

import type { GenerationFailure, SourceCheck } from "./api"
import type { YoutubeCookieOptions } from "./youtubeCookies"

export type YoutubeSubtitlePrompt = {
  kind: "configure_cookies" | "fix_cookies" | "offer_asr"
  source: string
  detail?: string
}

export type YoutubeCookiePermissionKind = "full_disk_access" | "safari_full_disk_access" | "keychain"

const youtubeCookieErrorCodes = new Set([
  "youtube_verification_required",
  "youtube_cookies_unavailable",
  "youtube_cookies_safari_permission",
  "youtube_cookies_permission_denied",
  "youtube_cookies_keychain_denied",
  "youtube_cookies_not_found",
  "youtube_cookies_format_invalid",
  "youtube_cookies_empty",
  "youtube_cookies_expired",
])

export function isYoutubeCookieErrorCode(code?: string | null): boolean {
  return Boolean(code && youtubeCookieErrorCodes.has(code))
}

export function resolveYoutubeCookiePermissionKind(code?: string | null): YoutubeCookiePermissionKind | null {
  if (code === "youtube_cookies_safari_permission") return "safari_full_disk_access"
  if (code === "youtube_cookies_permission_denied") return "full_disk_access"
  if (code === "youtube_cookies_keychain_denied") return "keychain"
  return null
}

const youtubeSubtitleFailureCodes = new Set([
  "subtitles_unavailable",
  "youtube_subtitles_unconfirmed",
  "youtube_subtitle_invalid",
  "extraction_failed",
  "source_permission_denied",
])

export function hasYoutubeCookiesConfigured(options?: YoutubeCookieOptions | null): boolean {
  if (!options) return false
  if (options.mode === "browser") return Boolean(options.browser.trim())
  if (options.mode === "file") return Boolean(options.filePath.trim())
  return false
}

export function effectiveAsrForSource<T extends { enabled: boolean }>(
  asr: T,
  isYoutube: boolean,
  cookiesConfigured: boolean,
): T {
  // Never silently spend paid ASR on YouTube before cookies are configured.
  if (isYoutube && !cookiesConfigured && asr.enabled) {
    return { ...asr, enabled: false }
  }
  return { ...asr }
}

export function resolveYoutubeSubtitlePrompt({
  source,
  isYoutube,
  cookiesConfigured,
  asrEnabled,
  code,
  status,
  message,
}: {
  source: string
  isYoutube: boolean
  cookiesConfigured: boolean
  asrEnabled: boolean
  code?: string | null
  status?: SourceCheck["status"]
  message?: string | null
}): YoutubeSubtitlePrompt | undefined {
  if (!isYoutube) return undefined
  const detail = message?.trim() || undefined
  if (code && youtubeCookieErrorCodes.has(code)) {
    return {
      kind: cookiesConfigured ? "fix_cookies" : "configure_cookies",
      source,
      detail,
    }
  }
  const missingSubtitles = status === "needs_asr" || code === "subtitles_unavailable"
  const subtitleProblem = missingSubtitles || (code ? youtubeSubtitleFailureCodes.has(code) : false)
  if (!cookiesConfigured && subtitleProblem) {
    return { kind: "configure_cookies", source, detail }
  }
  if (cookiesConfigured && missingSubtitles && !asrEnabled) {
    return { kind: "offer_asr", source, detail }
  }
  return undefined
}

export function failureToYoutubePrompt(
  source: string,
  isYoutube: boolean,
  cookiesConfigured: boolean,
  asrEnabled: boolean,
  failure: GenerationFailure,
): YoutubeSubtitlePrompt | undefined {
  return resolveYoutubeSubtitlePrompt({
    source,
    isYoutube,
    cookiesConfigured,
    asrEnabled,
    code: failure.code,
    message: failure.message,
  })
}
