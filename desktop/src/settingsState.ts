import type { Provider } from "./api"
import type { AsrOptions } from "./asrSettings"

export type Preferences = {
  provider: Provider
  paths: { codex: string; claude: string }
  api: { baseUrl: string; model: string }
  asr: AsrOptions
}

export type SettingsDraft = { preferences: Preferences; apiToken: string; asrToken: string }
export type SettingsField = "provider" | "codex-path" | "claude-path"
  | "api-url" | "api-model" | "api-token" | "asr-token" | "asr-url" | "asr-model"
  | "asr-max-minutes" | "asr-chunk-minutes" | "ffmpeg-path"

export class SettingsValidationError extends Error {
  readonly field: SettingsField
  constructor(field: SettingsField, message: string) {
    super(message)
    this.name = "SettingsValidationError"
    this.field = field
  }
}

export class SettingsSaveError extends Error {
  readonly rollbackFailed: boolean
  constructor(message: string, rollbackFailed = false) {
    super(message)
    this.name = "SettingsSaveError"
    this.rollbackFailed = rollbackFailed
  }
}

const controls = /[\u0000-\u001f\u007f-\u009f]/
const bytes = (value: string) => new TextEncoder().encode(value).byteLength

// Keep invalid control characters visible to validation and dirty-state checks.
// Ordinary surrounding spaces from a paste can still be normalized safely.
const clean = (value: string) => controls.test(value) ? value : value.trim()
const cleanUrl = (value: string) => clean(value).replace(/\/+$/, "")

function normalizedAsr(value: AsrOptions): AsrOptions {
  return {
    enabled: value.enabled,
    reuseToken: value.reuseToken,
    baseUrl: cleanUrl(value.baseUrl),
    model: clean(value.model),
    maxMinutes: value.maxMinutes,
    chunkMinutes: value.chunkMinutes,
    ffmpegPath: clean(value.ffmpegPath),
  }
}

export function normalizeSettingsDraft(value: SettingsDraft): SettingsDraft {
  return {
    preferences: {
      provider: value.preferences.provider,
      paths: { codex: clean(value.preferences.paths.codex), claude: clean(value.preferences.paths.claude) },
      api: { baseUrl: cleanUrl(value.preferences.api.baseUrl), model: clean(value.preferences.api.model) },
      asr: normalizedAsr(value.preferences.asr),
    },
    apiToken: clean(value.apiToken),
    asrToken: clean(value.asrToken),
  }
}

export function settingsDraftEqual(a: SettingsDraft, b: SettingsDraft): boolean {
  return JSON.stringify(normalizeSettingsDraft(a)) === JSON.stringify(normalizeSettingsDraft(b))
}

function validateToken(value: string, field: "api-token" | "asr-token", required: boolean) {
  const name = field === "api-token" ? "AI API 密钥" : "语音 API 密钥"
  if (required && !value) throw new SettingsValidationError(field, `请填写${name}`)
  if (bytes(value) > 8192 || controls.test(value)) {
    throw new SettingsValidationError(field, `${name}不能超过 8192 字节或包含控制字符`)
  }
}

function validatePath(value: string, field: "codex-path" | "claude-path" | "ffmpeg-path") {
  if (value && (!value.startsWith("/") || bytes(value) > 4096 || controls.test(value))) {
    const name = field === "ffmpeg-path" ? "FFmpeg" : field === "codex-path" ? "Codex" : "Claude Code"
    throw new SettingsValidationError(field, `请填写 ${name} 程序的完整路径，或留空自动查找`)
  }
}

function parsedUrl(value: string, field: "api-url" | "asr-url", message: string): URL {
  if (controls.test(value)) throw new SettingsValidationError(field, message)
  try { return new URL(value) } catch { throw new SettingsValidationError(field, message) }
}

export function validateApiConfiguration(api: Preferences["api"], token: string): Preferences["api"] {
  const normalized = { baseUrl: cleanUrl(api.baseUrl), model: clean(api.model) }
  const message = "请输入 HTTPS API 根地址（本机 localhost 可用 HTTP），不要包含账号、查询参数或 /chat/completions"
  const url = parsedUrl(normalized.baseUrl, "api-url", message)
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
  if ((url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) || !url.hostname
    || url.username || url.password || /[?#]/.test(normalized.baseUrl)
    || url.pathname.replace(/\/+$/, "").endsWith("/chat/completions")) {
    throw new SettingsValidationError("api-url", message)
  }
  if (!normalized.model || bytes(normalized.model) > 256 || controls.test(normalized.model)) {
    throw new SettingsValidationError("api-model", "请填写有效的模型名称（最多 256 字节）")
  }
  validateToken(clean(token), "api-token", true)
  return normalized
}

export function validateAsrConfiguration(value: AsrOptions, token: string): AsrOptions {
  const asr = normalizedAsr(value)
  validatePath(asr.ffmpegPath, "ffmpeg-path")
  const message = "请填写百炼原生 /api/v1 接口地址"
  const url = parsedUrl(asr.baseUrl, "asr-url", message)
  if (url.protocol !== "https:" || url.username || url.password || url.port
    || /[?#]/.test(asr.baseUrl) || url.pathname.replace(/\/+$/, "") !== "/api/v1"
    || !(url.hostname === "dashscope.aliyuncs.com" || url.hostname === "dashscope-intl.aliyuncs.com"
      || url.hostname.endsWith(".cn-beijing.maas.aliyuncs.com"))) {
    throw new SettingsValidationError("asr-url", message)
  }
  if (asr.model !== "paraformer-v2") {
    throw new SettingsValidationError("asr-model", "当前语音服务使用 paraformer-v2 模型")
  }
  if (!Number.isInteger(asr.maxMinutes) || asr.maxMinutes < 1 || asr.maxMinutes > 720) {
    throw new SettingsValidationError("asr-max-minutes", "转写上限需要在 1–720 分钟之间")
  }
  if (![0, 5, 10, 15].includes(asr.chunkMinutes)) {
    throw new SettingsValidationError("asr-chunk-minutes", "请选择整段转写，或每 5、10、15 分钟分片")
  }
  validateToken(clean(token), "asr-token", true)
  return asr
}

/** Validate the complete draft before any credential or preference is written. */
export function validateSettingsDraft(value: SettingsDraft): SettingsDraft {
  const next = normalizeSettingsDraft(value)
  const { preferences, apiToken, asrToken } = next
  if (!["api", "codex", "claude"].includes(preferences.provider)) {
    throw new SettingsValidationError("provider", "请选择 AI 工具")
  }
  if (preferences.provider !== "api") {
    validatePath(preferences.paths[preferences.provider], `${preferences.provider}-path`)
  }
  validatePath(preferences.asr.ffmpegPath, "ffmpeg-path")
  // Even dormant credentials are written when explicitly changed, so validate
  // them before the first write, not only when their provider is selected.
  validateToken(apiToken, "api-token", preferences.provider === "api")
  validateToken(asrToken, "asr-token", false)
  if (preferences.provider === "api") validateApiConfiguration(preferences.api, apiToken)
  if (preferences.asr.enabled) {
    if (preferences.asr.reuseToken) validateToken(apiToken, "api-token", true)
    validateAsrConfiguration(preferences.asr, preferences.asr.reuseToken ? apiToken : asrToken)
  }
  return next
}

export type SettingsSavePorts = {
  saveApiToken: (token: string) => Promise<void>
  deleteApiToken: () => Promise<void>
  saveAsrToken: (token: string) => Promise<void>
  deleteAsrToken: () => Promise<void>
  // This callback must write preferences before publishing application state;
  // if persistence fails it must throw without applying the new preferences.
  savePreferences: (preferences: Preferences) => void | Promise<void>
}

/**
 * Compensate completed credential writes if a later write fails. Secrets never
 * enter preferences, exception messages, or logs. This is failure recovery for
 * the existing private files and local preferences, not a crash-safe database.
 */
export async function saveSettingsDraft(
  value: SettingsDraft, previous: SettingsDraft, ports: SettingsSavePorts,
): Promise<SettingsDraft> {
  const next = validateSettingsDraft(value)
  const before = normalizeSettingsDraft(previous)
  const originalApiToken = previous.apiToken
  const originalAsrToken = previous.asrToken
  if (settingsDraftEqual(next, before)) return next
  const undo: Array<() => Promise<void>> = []
  let stage = "设置"
  try {
    if (next.apiToken !== before.apiToken) {
      stage = "AI API 密钥"
      if (next.apiToken) await ports.saveApiToken(next.apiToken)
      else await ports.deleteApiToken()
      undo.push(() => originalApiToken ? ports.saveApiToken(originalApiToken) : ports.deleteApiToken())
    }
    if (next.asrToken !== before.asrToken) {
      stage = "语音 API 密钥"
      if (next.asrToken) await ports.saveAsrToken(next.asrToken)
      else await ports.deleteAsrToken()
      undo.push(() => originalAsrToken ? ports.saveAsrToken(originalAsrToken) : ports.deleteAsrToken())
    }
    stage = "设置"
    await ports.savePreferences(next.preferences)
    return next
  } catch {
    let rollbackFailed = false
    for (const restore of undo.reverse()) {
      try { await restore() } catch { rollbackFailed = true }
    }
    if (rollbackFailed) {
      throw new SettingsSaveError(`保存${stage}失败，且部分密钥未能恢复。请重新读取配置后再试。`, true)
    }
    throw new SettingsSaveError(`保存${stage}失败，${undo.length ? "已恢复原有密钥" : "原有设置未更改"}。请重试。`)
  }
}
