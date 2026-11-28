import { tr, locale, localizedDetail, translateDetail, type PageLanguage, type Locale } from "./language.ts"
import { Channel, invoke as invokeNative, type InvokeArgs } from "@tauri-apps/api/core"
import type { AsrOptions } from "./asrSettings"
import type { CliModelCatalog, CliProvider, CliReasoningEffort } from "./cliModels"
import type { TaskJournal } from "./taskJournal"
import type { YoutubeCookieOptions } from "./youtubeCookies"

function invoke<T>(command: string, args?: InvokeArgs): Promise<T> {
  return invokeNative<T>(command, args).catch(error => {
    if (typeof error === "string" && locale() === "en") {
      throw localizedDetail(error, "The operation could not be completed. Try again.")
    }
    throw error
  })
}

export const setNativeLanguage = (language: Locale) => invoke<void>("set_ui_language", { language })

export type AppUpdateCheck =
  | { status: "unconfigured" }
  | { status: "current"; currentVersion: string }
  | { status: "available"; currentVersion: string; version: string }

export const checkAppUpdate = () => invokeNative<AppUpdateCheck>("check_app_update")
export const installAppUpdate = (version: string) => invokeNative<void>("install_app_update", { version })

export type Provider = "codex" | "claude" | "api"
export type ExportFormat = "html" | "markdown"
export type SubtitleExportFormat = "srt" | "vtt"
export type MarkdownExportOptions = { content: "readable" | "full"; includeTimestamped: boolean }
export type ArticleDetails = {
  title: string; category: string; originalTitle: string; originalCategory: string
  titleOverridden: boolean; categoryOverridden: boolean
  linkedSourceUrl?: string
  linkedThumbnailUrl?: string
  subtitleTitle?: string
}
export type ArticleState = { isRead: boolean; isStarred: boolean }
export type ArticleStatePatch = Partial<ArticleState>

export type Article = {
  id: string
  title: string
  category: string
  hasAnalysis?: boolean
  platform: "bilibili" | "youtube" | "apple_podcasts" | "local"
  sourceUrl: string | null
  updatedAt: number
  author: string | null
  publishedAt: string | null
  durationMs: number | null
  thumbnailUrl: string | null
  localCover?: string | null
  isRead?: boolean
  isStarred?: boolean
  previewId?: string
  readingKey?: string
}

export type RequestActivity = {
  sequence: number; id: string; stage: "analysis" | "polish" | "publication" | "translation" | null
  phase: "queued" | "running" | "received" | "validating" | "complete" | "cache" | "failed"
  active: number; queued: number; validating: number; completed: number; cacheHits: number
}

export type GenerationFailure = {
  message: string; code: string; stage?: string | null; retryable: boolean
  action: "retry" | "settings" | "setup" | "source" | "details"
}

export function normalizeGenerationFailure(error: unknown): GenerationFailure {
  if (error && typeof error === "object" && "message" in error) {
    const value = error as Partial<GenerationFailure>
    const action = ["retry", "settings", "setup", "source", "details"].includes(value.action ?? "")
      ? value.action! : "details"
    return { message: translateDetail(String(value.message)), code: typeof value.code === "string" ? value.code : "unknown",
      stage: typeof value.stage === "string" ? value.stage : null, retryable: value.retryable === true, action }
  }
  const message = translateDetail(String(error))
  const cancelled = message === "已取消生成" || message === "Generation cancelled" || message === tr("已取消生成")
  return { message, code: cancelled ? "cancelled" : "unknown", stage: null,
    retryable: true, action: "retry" }
}

export type Progress = {
  stage: string
  message: string
  completed: number | null
  total: number | null
  metadata?: ArticleMetadata | null
  previewId?: string | null
  request?: RequestActivity | null
}

export type ArticleMetadata = Pick<Article, "title" | "platform" | "sourceUrl" | "author" | "publishedAt" | "durationMs" | "thumbnailUrl">

export type Library = { articles: Article[]; unavailable: number }
export type Duplicate = { key: string | null; article: Article | null }
export type ToolStatus = { executable: string | null; keyAvailable: boolean }

export const providerNames = { codex: "Codex", claude: "Claude Code", get api() { return tr("自定义 API") } }
export const platformNames = { youtube: "YouTube", bilibili: "Bilibili", get apple_podcasts() { return tr("Apple 播客") }, get local() { return tr("本地字幕") } }

export function generateArticle(
  source: string,
  provider: Provider,
  executable: string,
  onProgress: (event: Progress) => void,
  onCredentialsRead: () => void,
  requestId: string,
  localFile: boolean,
  replace = false,
  baseUrl = "",
  model = "",
  asr?: AsrOptions,
  resumePreview?: string,
  pageLanguage: PageLanguage = "original",
  youtubeCookies?: YoutubeCookieOptions,
  reasoningEffort?: CliReasoningEffort,
) {
  const progress = new Channel<Progress>(onProgress)
  const credentialsRead = new Channel<null>(onCredentialsRead)
  return invoke<Article>("generate", { source, provider, executable, progress, requestId, localFile, replace, baseUrl, model, asr,
    resumePreview, pageLanguage, uiLanguage: locale(), credentialsRead,
    ...(reasoningEffort ? { reasoningEffort } : {}),
    ...(youtubeCookies && youtubeCookies.mode !== "none" ? { youtubeCookies } : {}) })
}

export const cancelGeneration = (requestId: string) => invoke("cancel_generation", { requestId })
export const listArticles = () => invoke<Library>("list_articles")
export const checkTool = (provider: Provider, executable: string) =>
  invoke<ToolStatus>("check_tool", { provider, executable })
export const listCliModels = (provider: CliProvider) =>
  invoke<CliModelCatalog>("list_cli_models", { provider })
export const exportArticle = (id: string, format: ExportFormat, options?: MarkdownExportOptions) =>
  invoke<boolean>("export_article", { id, format, uiLanguage: locale(), ...(options ? { options } : {}) })
export const exportSubtitles = (id: string, format: SubtitleExportFormat, preview = false) =>
  invoke<boolean>("export_subtitles", { id, format, preview, uiLanguage: locale() })
export const pickCookiesFile = () => invoke<string | null>("pick_cookies_file", { uiLanguage: locale() })
export const testYoutubeCookies = (options: YoutubeCookieOptions) =>
  invoke<{ ok: boolean; message: string }>("test_youtube_cookies", { options })
export const getArticleDetails = (id: string) => invoke<ArticleDetails>("get_article_details", { id })
export const updateArticleDetails = (id: string, title: string | null, category: string | null, sourceUrl?: string) =>
  invoke<Article>("update_article_details", { id, title, category, ...(sourceUrl === undefined ? {} : { sourceUrl }) })
export const openVideo = (source: string) => invoke("open_video", { source })
export const openExternal = (url: string) => invoke("open_external", { url })
export const pickSubtitle = () => invoke<string | null>("pick_subtitle", { uiLanguage: locale() })
export const readSubtitleClipboard = () => invoke<{ paths: string[]; text: string }>("read_subtitle_clipboard", { uiLanguage: locale() })
export const resolveSubtitlePath = (path: string) => invoke<string>("resolve_subtitle_path", { path })
export const savePastedSubtitle = (name: string, text: string) => invoke<string>("save_pasted_subtitle", { name, text })
export const openArticle = (id: string) => invoke("open_article", { id })
export const revealArticle = (id: string) => invoke("reveal_article", { id })

export const checkDuplicate = (source: string, localFile: boolean, resumePreview?: string) =>
  invoke<Duplicate>("check_duplicate", { source, localFile, resumePreview })

export const deleteArticle = (id: string, readingKey?: string | null) =>
  invoke<void>("delete_article", { id, ...(readingKey ? { readingKey } : {}) })
export const deleteTaskData = (options: {
  source?: string | null
  localFile?: boolean
  sourceKey?: string | null
  previewId?: string | null
}) => invoke<void>("delete_task_data", {
  ...(options.source ? { source: options.source } : {}),
  ...(options.localFile !== undefined ? { localFile: options.localFile } : {}),
  ...(options.sourceKey ? { sourceKey: options.sourceKey } : {}),
  ...(options.previewId ? { previewIdValue: options.previewId } : {}),
})
export const updateArticleState = (id: string, patch: ArticleStatePatch) =>
  invoke<ArticleState>("update_article_state", { id, ...patch })
export type PreparedArticle = { revision: string; readingKey: string }
export const prepareArticle = (id: string) => invoke<PreparedArticle>("prepare_article", { id })
type ShownotesResult = { status: "empty" } | ({ status: "updated" } & PreparedArticle)
export const refreshShownotes = (id: string, youtubeCookies?: YoutubeCookieOptions) =>
  invoke<ShownotesResult>("refresh_shownotes", {
    id,
    ...(youtubeCookies && youtubeCookies.mode !== "none" ? { youtubeCookies } : {}),
  })
export const preparePreview = (id: string) => invoke<PreparedArticle & { article: Article }>("prepare_preview", { id })
export const loadTaskJournal = () => invoke<unknown>("load_task_journal")
export const saveTaskJournal = (journal: TaskJournal) => invoke<void>("save_task_journal", { journal })
export const loadPreferencesNative = () => invoke<unknown>("load_preferences")
export const savePreferencesNative = (preferences: unknown) => invoke<void>("save_preferences", { preferences })
export const pickExecutable = (title: string) => invoke<string | null>("pick_executable", { title, uiLanguage: locale() })
export const loadReadingPosition = (key: string) => invoke<unknown>("load_reading_position", { key })
export const saveReadingPosition = (key: string, position: unknown) => invoke<void>("save_reading_position", { key, position })

// Token lives in a private app-data file (mode 0600), not Keychain or localStorage.
// It is only loaded into the Settings form on explicit navigation to that screen.
export const loadApiToken = () => invoke<string | null>("load_api_token")
export const saveApiToken = (token: string) => invoke<void>("save_api_token", { token })
export const deleteApiToken = () => invoke<void>("delete_api_token")
export const testApiConnection = (baseUrl: string, model: string, token: string) =>
  invoke<{ ok: boolean; message: string }>("test_api_connection", { baseUrl, model, token })

export const previewVideo = (source: string) => invoke<ArticleMetadata>("preview_video", { source })
export type SourceCheck = {
  source: string
  status: "captions" | "needs_asr" | "blocked" | "unknown"
  code: string
  message: string
  checkedAt: number
  origin?: "check" | "generation"
}
export type DownloadedSubtitles = {
  previewId: string
  source: string
  sourceKey: string
  readingKey: string
  language: string | null
  segmentCount: number
  metadata: ArticleMetadata
}
export const checkSource = (source: string, youtubeCookies?: YoutubeCookieOptions) =>
  invoke<SourceCheck>("check_source", {
    source,
    ...(youtubeCookies && youtubeCookies.mode !== "none" ? { youtubeCookies } : {}),
  })
export const downloadSubtitles = (source: string, youtubeCookies?: YoutubeCookieOptions) =>
  invoke<DownloadedSubtitles>("download_subtitles", {
    source,
    ...(youtubeCookies && youtubeCookies.mode !== "none" ? { youtubeCookies } : {}),
  })
export const loadAsrToken = () => invoke<string | null>("load_asr_token")
export const saveAsrToken = (token: string) => invoke<void>("save_asr_token", { token })
export const deleteAsrToken = () => invoke<void>("delete_asr_token")
export const testAsrConnection = (options: AsrOptions, token: string) =>
  invoke<{ ok: boolean; message: string }>("test_asr_connection", { options, token })

export type FfmpegStatus = {
  state: "ready" | "missing" | "invalid"
  path: string | null
  version: string | null
  message: string
}
export const checkFfmpeg = (path = "") => invoke<FfmpegStatus>("check_ffmpeg", { path })
export const locateFfmpeg = () => invoke<FfmpegStatus | null>("locate_ffmpeg", { uiLanguage: locale() })

export const articleTranscriptPath = (id: string) => invoke<string>("article_transcript_path", { id })
