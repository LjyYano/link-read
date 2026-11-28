import type { GenerationFailure } from "./api"
import { defaultAsr, loadAsrOptions, type AsrOptions } from "./asrSettings.ts"
import { tr, type PageLanguage } from "./language.ts"
import { SettingsSaveError, type Preferences } from "./settingsState.ts"
import type { GenerationInput, TaskSettings } from "./taskJournal"
import { normalizeVideoLink } from "./videoLinks.ts"
import { defaultYoutubeCookies, type YoutubeCookieOptions } from "./youtubeCookies.ts"
import { effectiveAsrForSource, hasYoutubeCookiesConfigured } from "./youtubeGuidance.ts"

type SettingsSnapshot = Pick<GenerationInput, "provider" | "baseUrl" | "model" | "reasoningEffort" | "asr"> & {
  input?: string
  localFile?: boolean
  youtubeCookies?: YoutubeCookieOptions
}
type SettingsStorage = {
  readPreferences: () => Preferences
  hasPendingWrite: () => boolean
  setPendingWrite: (pending: boolean) => void
}

function isYoutubeSource(input?: string, localFile?: boolean): boolean {
  if (!input || localFile) return false
  try {
    const host = new URL(normalizeVideoLink(input)).hostname
    return host === "youtu.be" || host === "www.youtu.be" || host === "youtube.com" || host.endsWith(".youtube.com")
  } catch {
    return false
  }
}

function sameYoutubeCookies(a?: YoutubeCookieOptions, b?: YoutubeCookieOptions): boolean {
  const left = a ?? defaultYoutubeCookies
  const right = b ?? defaultYoutubeCookies
  return left.mode === right.mode && left.browser === right.browser && left.filePath === right.filePath
}

function sanitizeDormantAsr(asr: AsrOptions): AsrOptions {
  if (asr.enabled) return { ...asr }
  return {
    ...defaultAsr,
    enabled: false,
    reuseToken: asr.reuseToken,
    ffmpegPath: loadAsrOptions({ ffmpegPath: asr.ffmpegPath }).ffmpegPath,
  }
}

/** Build validated, active-only task settings so dormant invalid fields never block enqueueing. */
export function taskSettingsForGeneration(
  preferences: Preferences,
  options: { isYoutube?: boolean; pageLanguage?: PageLanguage } = {},
): TaskSettings {
  const provider = preferences.provider
  const hasCookies = hasYoutubeCookiesConfigured(preferences.youtubeCookies)
  const effectiveAsr = effectiveAsrForSource(preferences.asr, Boolean(options.isYoutube), hasCookies)
  const cli = provider === "api" ? undefined : preferences.cli?.[provider]
  return {
    provider,
    executable: provider === "api" ? "" : preferences.paths[provider],
    baseUrl: provider === "api" ? preferences.api.baseUrl : "",
    model: provider === "api" ? preferences.api.model : (cli?.model ?? ""),
    ...(cli?.reasoningEffort ? { reasoningEffort: cli.reasoningEffort } : {}),
    asr: sanitizeDormantAsr(effectiveAsr),
    ...(options.pageLanguage !== undefined ? { pageLanguage: options.pageLanguage } : {}),
  }
}

function matchesSavedSettings(input: SettingsSnapshot, saved: Preferences): boolean {
  if (input.provider !== saved.provider) return false
  if (saved.provider === "api") {
    if (input.baseUrl !== saved.api.baseUrl || input.model !== saved.api.model) return false
  } else {
    const cli = saved.cli?.[saved.provider]
    if (input.model !== (cli?.model ?? "")) return false
    if ((input.reasoningEffort ?? "") !== (cli?.reasoningEffort ?? "")) return false
  }
  const youtube = isYoutubeSource(input.input, input.localFile)
  if (youtube && input.youtubeCookies !== undefined && !sameYoutubeCookies(input.youtubeCookies, saved.youtubeCookies)) {
    return false
  }
  const expectedAsr = effectiveAsrForSource(saved.asr, youtube, hasYoutubeCookiesConfigured(saved.youtubeCookies))
  if (input.asr.enabled !== expectedAsr.enabled) return false
  if (expectedAsr.enabled && (
    input.asr.baseUrl !== expectedAsr.baseUrl
    || input.asr.model !== expectedAsr.model
    || input.asr.reuseToken !== expectedAsr.reuseToken
  )) {
    return false
  }
  return true
}

const pendingMessage = "上次未能完成设置保存。请核对服务地址和密钥，然后重新保存设置。"

/** Serialize native credential reads with the entire settings write and rollback. */
export class GenerationSettingsGate {
  private available = Promise.resolve()
  private readonly storage: SettingsStorage

  constructor(storage: SettingsStorage) { this.storage = storage }

  private async acquire(): Promise<() => void> {
    const previous = this.available
    let release!: () => void
    this.available = new Promise<void>(resolve => { release = resolve })
    await previous
    return release
  }

  async admit(input: SettingsSnapshot): Promise<() => void> {
    const release = await this.acquire()
    try {
      if (this.storage.hasPendingWrite()) {
        throw { message: tr(pendingMessage), code: "settings_pending", stage: null,
          retryable: true, action: "settings" } satisfies GenerationFailure
      }
      if (!matchesSavedSettings(input, this.storage.readPreferences())) {
        throw { message: tr("设置已变更，请重试以使用当前设置。"), code: "settings_changed", stage: null,
          retryable: true, action: "retry" } satisfies GenerationFailure
      }
      return release
    } catch (error) {
      release()
      throw error
    }
  }

  async withLock<T>(operation: () => Promise<T>): Promise<T> {
    const release = await this.acquire()
    try { return await operation() } finally { release() }
  }

  private markWrite(pending: boolean) {
    try { this.storage.setPendingWrite(pending) } catch {
      throw new SettingsSaveError(tr("无法确认设置保存状态，请检查设置后重试。"))
    }
  }

  save<T>(operation: () => Promise<T>): Promise<T> {
    return this.withLock(async () => {
      const wasPending = this.storage.hasPendingWrite()
      // Persist only a flag. An interrupted write must be reviewed on the next launch.
      this.markWrite(true)
      let result: T
      try {
        result = await operation()
      } catch (error) {
        if (!wasPending && !(error instanceof SettingsSaveError && error.rollbackFailed)) this.markWrite(false)
        throw error
      }
      this.markWrite(false)
      return result
    })
  }
}
