import { useEffect, useEffectEvent, useLayoutEffect, useRef, useState } from "react"
import { createRoot } from "react-dom/client"
import { isTauri } from "@tauri-apps/api/core"
import { getCurrentWebview } from "@tauri-apps/api/webview"
import { getCurrentWindow } from "@tauri-apps/api/window"
import {
  articleTranscriptPath, cancelGeneration, checkDuplicate, checkTool, deleteArticle,
  exportSubtitles, generateArticle, listArticles, loadPreferencesNative, loadTaskJournal,
  normalizeGenerationFailure, openVideo, pickSubtitle, providerNames, readSubtitleClipboard,
  resolveSubtitlePath, savePastedSubtitle, savePreferencesNative, saveTaskJournal, setNativeLanguage,
  updateArticleState,
  type Article, type ArticleStatePatch, type DownloadedSubtitles, type Library, type Progress,
  type SubtitleExportFormat, type ToolStatus,
} from "./api"
import { AppUpdateDialog } from "./AppUpdateDialog"
import { ArticleDetailsDialog } from "./ArticleDetailsDialog"
import { defaultAsr, loadAsrOptions } from "./asrSettings"
import { defaultCliPreferences, loadCliPreferences } from "./cliModels"
import { Bookshelf } from "./BookshelfSection"
import { ConfirmDialog } from "./ConfirmDialog"
import { mergeArticleMetadata } from "./coverSource"
import { GenerationSettingsGate, taskSettingsForGeneration } from "./generationSettings"
import { Home } from "./Home"
import type { Job, TaskJob } from "./JobCard"
import { languageKey, loadLanguageSettings, resolveLocale, setLocale, tr, type LanguageSettings } from "./language.ts"
import { ArticleStateChanges, ArticleStateWrites, defaultShelfFilters, withArticleState, type ShelfFilters } from "./libraryState"
import { MarkdownExportDialog } from "./MarkdownExportDialog"
import { progressFromEvent } from "./progress"
import { QuickOpen } from "./QuickOpen"
import { loadRecentArticles, rememberArticle } from "./quickOpenState"
import { Reader } from "./Reader"
import { flushReadingPositions } from "./readingPositionBridge"
import { Settings, type SettingsLeaveGuard, type SettingsTarget } from "./Settings"
import { isLoopbackBaseUrl, type Preferences } from "./settingsState"
import { SourceReadinessPanel } from "./SourceReadinessPanel"
import { maxSubtitleBytes, pastedSubtitleFormat, pastedSubtitlePath, subtitleExtension, type ComposerPaste } from "./subtitlePaste"
import { TaskCenter } from "./TaskCenter"
import {
  activeTaskCount, completedArticles, groupTasks, loadCompletedArticleIds, loadReviewedTasks,
  rememberCompletedArticle, reviewedTerminalTaskIds, reviewTasks, saveReviewedTasks, unreviewedTaskIds,
} from "./taskCenterState"
import {
  parseTaskJournal, restoreTaskJournal, resumeNeedsAsrConfirmation, TaskJournalWriter, taskSnapshot,
  type GenerationInput, type TaskJournal,
} from "./taskJournal"
import { recordProgress } from "./taskProgress"
import { SerialTaskQueue } from "./taskQueue"
import { loadThemeMode, resolveTheme, saveThemeMode, type ThemeMode } from "./themeState"
import { TitleBar } from "./TitleBar"
import { ToastBanner } from "./ToastBanner"
import { useAppUpdates } from "./useAppUpdates"
import { useContinueReading } from "./useContinueReading"
import { useLinkPreview } from "./useLinkPreview"
import { useSourceDuplicate } from "./useSourceDuplicate"
import { forgetSourceReadiness, rememberSourceFailure, useSourceReadiness } from "./useSourceReadiness"
import { clearSubtitleDownloadErrors, forgetSubtitleDownload, useSubtitleDownload } from "./useSubtitleDownload"
import { normalizeVideoLink } from "./videoLinks"
import { defaultYoutubeCookies, loadYoutubeCookies, type YoutubeCookieOptions } from "./youtubeCookies"
import { failureToYoutubePrompt, hasYoutubeCookiesConfigured, resolveYoutubeSubtitlePrompt, type YoutubeSubtitlePrompt } from "./youtubeGuidance"
import { YoutubeSubtitleDialog } from "./YoutubeSubtitleDialog"
import "./styles.css"
import "./source-preview.css"
import "./experience.css"

type Screen = "create" | "library" | "settings" | "reader"
type ReadingOrigin = { screen: Exclude<Screen, "reader">; scroll: number; tasks: boolean; completedArticleId?: string; taskId?: string }
type ActiveGeneration = GenerationInput & { replacementArticleId?: string; youtubeCookies?: YoutubeCookieOptions }
type PendingConfirm = {
  title: string
  message: string
  confirmLabel?: string
  danger?: boolean
  resolve: (accepted: boolean) => void
}

const storageKey = "linkread-preferences"
const settingsWriteKey = "linkread-settings-write-pending"

function hasPendingSettingsWrite(): boolean {
  try { return localStorage.getItem(settingsWriteKey) !== null } catch { return true }
}

function defaultPreferences(): Preferences {
  return {
    provider: "codex",
    paths: { codex: "", claude: "" },
    cli: defaultCliPreferences(),
    api: { baseUrl: "", model: "" },
    asr: { ...defaultAsr },
    youtubeCookies: { ...defaultYoutubeCookies },
  }
}

function normalizePreferencesObject(raw: unknown): Preferences {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return defaultPreferences()
  const saved = raw as Record<string, unknown>
  const paths = saved.paths && typeof saved.paths === "object" ? saved.paths as Record<string, unknown> : undefined
  const api = saved.api && typeof saved.api === "object" ? saved.api as Record<string, unknown> : undefined
  return {
    asr: loadAsrOptions(saved.asr as Partial<Preferences["asr"]> | undefined),
    youtubeCookies: loadYoutubeCookies(saved.youtubeCookies),
    provider: saved.provider === "claude" || saved.provider === "api" ? saved.provider : "codex",
    paths: {
      codex: typeof paths?.codex === "string" ? paths.codex : "",
      claude: typeof paths?.claude === "string" ? paths.claude : "",
    },
    cli: loadCliPreferences(saved.cli),
    api: {
      baseUrl: typeof api?.baseUrl === "string" ? api.baseUrl : "",
      model: typeof api?.model === "string" ? api.model : "",
    },
  }
}

function readLegacyLocalPreferences(): { value: Preferences; existed: boolean } {
  try {
    const raw = localStorage.getItem(storageKey)
    if (!raw) return { value: defaultPreferences(), existed: false }
    return { value: normalizePreferencesObject(JSON.parse(raw)), existed: true }
  } catch {
    return { value: defaultPreferences(), existed: false }
  }
}

function normalizeLanguageEnvelope(raw: unknown): LanguageSettings | undefined {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined
  const obj = raw as Record<string, unknown>
  if (!["system", "zh-CN", "en"].includes(String(obj.interfaceLanguage))
    || !["original", "zh-CN", "en"].includes(String(obj.pageLanguage))) return undefined
  return {
    interfaceLanguage: obj.interfaceLanguage as LanguageSettings["interfaceLanguage"],
    pageLanguage: obj.pageLanguage as LanguageSettings["pageLanguage"],
  }
}

function normalizeThemeEnvelope(raw: unknown): ThemeMode | undefined {
  return raw === "system" || raw === "light" || raw === "dark" ? raw : undefined
}

async function persistNativeEnvelope(next: {
  preferences: Preferences
  language: LanguageSettings
  theme: ThemeMode
}) {
  await savePreferencesNative({
    version: 1,
    preferences: next.preferences,
    language: next.language,
    theme: next.theme,
  })
}

function stepFor(stage: string) {
  if (stage === "render" || stage === "saved_article") return 3
  if (stage.startsWith("polish_") || stage === "article_ai" || stage === "article_ai_done"
    || stage === "parallel_start" || stage === "quote_warning") return 2
  if (stage.startsWith("ai_") || stage === "reuse") return 1
  return 0
}

function updateJob(current: Job | null, event: Progress): Job | null {
  if (current?.status !== "running") return current
  const step = stepFor(event.stage)
  const metadata = event.metadata
  const count: [number, number] | undefined = event.completed !== null && event.total !== null && event.total > 0
    ? [event.completed, event.total]
    : undefined
  return {
    ...mergeArticleMetadata(current, metadata), step: Math.max(step, current.step), message: event.message,
    previewId: event.previewId ?? current.previewId,
    activity: recordProgress(current.activity, event),
    percent: progressFromEvent(current.percent, event),
    count: event.stage === "ai_start" || event.stage === "ai_done"
      || event.stage === "polish_start" || event.stage === "polish_done" ? count : undefined,
  }
}

function sourcePlatform(source: string): Article["platform"] | undefined {
  try {
    const host = new URL(normalizeVideoLink(source)).hostname
    if (host === "youtu.be" || host === "www.youtu.be" || host === "youtube.com" || host.endsWith(".youtube.com")) return "youtube"
    if (host === "b23.tv" || host === "www.b23.tv" || host === "bilibili.com" || host.endsWith(".bilibili.com")) return "bilibili"
    if (host === "podcasts.apple.com") return "apple_podcasts"
  } catch { /* The processing command reports unsupported links. */ }
}

function App() {
  const [language, setLanguage] = useState(loadLanguageSettings)
  const [languageError, setLanguageError] = useState("")
  const [systemLanguage, setSystemLanguage] = useState(() => resolveLocale("system"))
  const appLocale = language.interfaceLanguage === "system" ? systemLanguage : language.interfaceLanguage
  setLocale(appLocale)
  useEffect(() => {
    const updateSystem = () => setSystemLanguage(resolveLocale("system"))
    window.addEventListener("languagechange", updateSystem)
    return () => window.removeEventListener("languagechange", updateSystem)
  }, [])
  useEffect(() => { void setNativeLanguage(appLocale).catch(() => {}) }, [appLocale])

  const [themeMode, setThemeMode] = useState<ThemeMode>(loadThemeMode)
  const [systemDark, setSystemDark] = useState(() => window.matchMedia("(prefers-color-scheme: dark)").matches)
  const [appNotice, setAppNotice] = useState("")
  const [screen, setScreen] = useState<Screen>("create")
  const [previousScreen, setPreviousScreen] = useState<Exclude<Screen, "settings">>("create")
  const [settingsTarget, setSettingsTarget] = useState<SettingsTarget>()
  const [source, setSource] = useState("")
  const [localPath, setLocalPath] = useState<string | null>(null)
  const [pasting, setPasting] = useState(false)
  const nativePaste = isTauri() && navigator.platform.startsWith("Mac")
  const [dragging, setDragging] = useState(false)
  const [inputError, setInputError] = useState("")
  const [preferences, setPreferences] = useState<Preferences>(() => readLegacyLocalPreferences().value)
  const [preferencesReady, setPreferencesReady] = useState(!isTauri())
  const preferencesRef = useRef(preferences)
  preferencesRef.current = preferences
  const languageRef = useRef(language)
  languageRef.current = language
  const themeModeRef = useRef(themeMode)
  themeModeRef.current = themeMode
  const [readerTheme, setReaderTheme] = useState<"light" | "dark">()

  async function changeLanguage(next: LanguageSettings) {
    if (isTauri()) {
      try {
        await persistNativeEnvelope({
          preferences: preferencesRef.current,
          language: next,
          theme: themeModeRef.current,
        })
      } catch {
        const message = tr("无法保存语言设置，请重试。")
        setLanguageError(message)
        throw new Error(message)
      }
    }
    try {
      localStorage.setItem(languageKey, JSON.stringify(next))
    } catch {
      if (!isTauri()) {
        const message = tr("无法保存语言设置，请重试。")
        setLanguageError(message)
        throw new Error(message)
      }
    }
    languageRef.current = next
    setLanguage(next)
    setLanguageError("")
  }

  async function changeTheme(next: ThemeMode) {
    if (isTauri()) {
      try {
        await persistNativeEnvelope({
          preferences: preferencesRef.current,
          language: languageRef.current,
          theme: next,
        })
      } catch {
        const message = tr("无法保存外观设置，请重试。")
        setAppNotice(message)
        throw new Error(message)
      }
    }
    try {
      saveThemeMode(next)
    } catch {
      if (!isTauri()) {
        const message = tr("无法保存外观设置，请重试。")
        setAppNotice(message)
        throw new Error(message)
      }
    }
    themeModeRef.current = next
    setReaderTheme(undefined)
    setThemeMode(next)
  }
  const [youtubePrompt, setYoutubePrompt] = useState<YoutubeSubtitlePrompt>()

  const linkPreview = useLinkPreview(source, localPath !== null)
  const sourceReadiness = useSourceReadiness(source, localPath !== null, preferences.youtubeCookies, result => {
    const prompt = resolveYoutubeSubtitlePrompt({
      source: result.source,
      isYoutube: sourcePlatform(result.source) === "youtube",
      cookiesConfigured: hasYoutubeCookiesConfigured(preferencesRef.current.youtubeCookies),
      asrEnabled: preferencesRef.current.asr.enabled,
      code: result.code,
      status: result.status,
      message: result.message,
    })
    if (prompt) setYoutubePrompt(prompt)
  })
  const subtitleDownload = useSubtitleDownload(source, localPath !== null, preferences.youtubeCookies, (src, failure) => {
    const prompt = failureToYoutubePrompt(
      src,
      sourcePlatform(src) === "youtube",
      hasYoutubeCookiesConfigured(preferencesRef.current.youtubeCookies),
      preferencesRef.current.asr.enabled,
      failure,
    )
    if (prompt) setYoutubePrompt(prompt)
  })
  const [settingsRecovery, setSettingsRecovery] = useState(hasPendingSettingsWrite)
  const settingsGate = useRef(new GenerationSettingsGate({
    readPreferences: () => preferencesRef.current,
    hasPendingWrite: hasPendingSettingsWrite,
    setPendingWrite: pending => {
      if (pending) localStorage.setItem(settingsWriteKey, "1")
      else localStorage.removeItem(settingsWriteKey)
      setSettingsRecovery(pending)
    },
  }))
  const [toolStatus, setToolStatus] = useState<ToolStatus>()
  const [apiTokenVersion, setApiTokenVersion] = useState(0)
  const [toolError, setToolError] = useState("")
  const [library, setLibrary] = useState<Library>()
  const [libraryError, setLibraryError] = useState("")
  const [shelfSearch, setShelfSearch] = useState("")
  const [quickOpen, setQuickOpen] = useState(false)
  const [taskCenterOpen, setTaskCenterOpen] = useState(false)
  const [taskCenterFocusId, setTaskCenterFocusId] = useState<string>()
  const [taskCenterCompletedId, setTaskCenterCompletedId] = useState<string>()
  const [reviewedTasks, setReviewedTasks] = useState(loadReviewedTasks)
  const [completedArticleIds, setCompletedArticleIds] = useState(loadCompletedArticleIds)
  const reviewedTasksRef = useRef(reviewedTasks)
  reviewedTasksRef.current = reviewedTasks
  const [recentArticles, setRecentArticles] = useState(loadRecentArticles)
  const resume = useContinueReading(library?.articles, recentArticles, screen === "create")
  const [shelfFilters, setShelfFilters] = useState<ShelfFilters>(defaultShelfFilters)
  const [article, setArticle] = useState<Article>()
  const [openedCompletion, setOpenedCompletion] = useState<string>()
  const [readerSession, setReaderSession] = useState(0)
  const readingOrigin = useRef<ReadingOrigin>({ screen: "library", scroll: 0, tasks: false })
  const returnScroll = useRef<number | undefined>(undefined)
  const [detailsArticle, setDetailsArticle] = useState<Article>()
  const [exportArticle, setExportArticle] = useState<Article>()
  const [detailsRevisions, setDetailsRevisions] = useState<Record<string, number>>({})
  const [previewTaskId, setPreviewTaskId] = useState<string>()
  const [standaloneDownload, setStandaloneDownload] = useState<DownloadedSubtitles>()
  const [completionNotice, setCompletionNotice] = useState<Article[]>([])
  const [jobs, setJobs] = useState<TaskJob[]>([])
  const jobsRef = useRef<TaskJob[]>([])
  const taskQueue = useRef(new SerialTaskQueue<Article>())
  const taskInputs = useRef(new Map<string, ActiveGeneration>())
  const taskTimes = useRef(new Map<string, { createdAt: number; updatedAt: number }>())
  const journalWriter = useRef(new TaskJournalWriter(saveTaskJournal))
  const journalReady = useRef(false)
  const journalLoadVersion = useRef(0)
  const journalSaveVersion = useRef(0)
  const [journalLoading, setJournalLoading] = useState(true)
  const [journalError, setJournalError] = useState("")
  const submitting = useRef(false)
  const startSubmission = useRef<symbol | undefined>(undefined)
  const draft = useRef("")
  const inputVersion = useRef(0)
  const pasteRequest = useRef<symbol | undefined>(undefined)
  const workspace = useRef<HTMLElement>(null)
  const shelfScroll = useRef(0)
  const loadingVersion = useRef(0)
  const articleStateChanges = useRef(new ArticleStateChanges())
  const articleStateWrites = useRef(new ArticleStateWrites())
  const requestId = useRef<string | null>(null)
  const navigationVersion = useRef(0)
  const settingsLeaveGuard = useRef<SettingsLeaveGuard | null>(null)
  const picking = useRef(false)
  const [pendingConfirm, setPendingConfirm] = useState<PendingConfirm>()
  const [closeConfirmOpen, setCloseConfirmOpen] = useState(false)
  const allowCloseRef = useRef(false)

  const busy = jobs.some(task => task.job.status === "running" || task.job.status === "cancelling" || task.job.status === "queued")
  const duplicate = useSourceDuplicate(localPath ?? source.trim(), localPath !== null, library)
  const existingArticle = duplicate?.article
    ? library?.articles.find(item => item.id === duplicate.article?.id) ?? duplicate.article : undefined
  const existingTask = matchingTask(duplicate?.key)
  const canInstallUpdate = !busy && !journalLoading

  function askConfirm(options: { title: string; message: string; confirmLabel?: string; danger?: boolean }): Promise<boolean> {
    return new Promise(resolve => {
      setPendingConfirm(current => {
        current?.resolve(false)
        return { ...options, resolve }
      })
    })
  }

  function runWithLeaveGuard(action: () => void) {
    if (screen !== "settings" || !settingsLeaveGuard.current) {
      action()
      return
    }
    if (settingsLeaveGuard.current.confirm(action)) action()
  }

  const update = useAppUpdates(
    () => canInstallUpdate && !submitting.current && !(screen === "settings" && settingsLeaveGuard.current?.isDirty()),
    async () => {
      if (journalReady.current) await persistJournal()
      await flushReadingPositions()
    },
  )

  const handleMenu = useEffectEvent((action: string) => {
    if (action === "check-updates") update.check()
    if (action === "open-settings" && !quickOpen && !taskCenterOpen
      && !document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) void showSettings()
  })

  useEffect(() => {
    if (!isTauri()) return
    let active = true
    void (async () => {
      try {
        const nativeRaw = await loadPreferencesNative()
        if (!active) return
        if (nativeRaw && typeof nativeRaw === "object" && !Array.isArray(nativeRaw)
          && (nativeRaw as Record<string, unknown>).version === 1) {
          const envelope = nativeRaw as Record<string, unknown>
          if (envelope.preferences && typeof envelope.preferences === "object") {
            const normalized = normalizePreferencesObject(envelope.preferences)
            preferencesRef.current = normalized
            setPreferences(normalized)
          }
          const nextLang = normalizeLanguageEnvelope(envelope.language)
          if (nextLang) {
            languageRef.current = nextLang
            setLanguage(nextLang)
            try { localStorage.setItem(languageKey, JSON.stringify(nextLang)) } catch {}
          }
          const nextTheme = normalizeThemeEnvelope(envelope.theme)
          if (nextTheme) {
            themeModeRef.current = nextTheme
            setThemeMode(nextTheme)
            try { saveThemeMode(nextTheme) } catch {}
          }
          try { localStorage.removeItem(storageKey) } catch {}
        } else {
          const legacy = readLegacyLocalPreferences()
          if (legacy.existed) {
            await persistNativeEnvelope({
              preferences: legacy.value,
              language: languageRef.current,
              theme: themeModeRef.current,
            })
            if (!active) return
            preferencesRef.current = legacy.value
            setPreferences(legacy.value)
            try { localStorage.removeItem(storageKey) } catch {}
          }
        }
      } catch {
        if (active) setAppNotice(tr("无法读取应用设置，已使用默认设置。"))
      } finally {
        if (active) setPreferencesReady(true)
      }
    })()
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!isTauri()) return
    let disposed = false
    let unlisten: (() => void) | undefined
    void getCurrentWebview().listen<string>("linkread-menu", event => handleMenu(event.payload)).then(cleanup => {
      if (disposed) cleanup()
      else unlisten = cleanup
    }).catch(() => {})
    return () => { disposed = true; unlisten?.() }
  }, [])

  async function finalizeWindowClose() {
    allowCloseRef.current = true
    try {
      if (screen === "reader") await flushReadingPositions()
      if (journalReady.current) await persistJournal()
    } catch {
      // Best-effort state flush on confirmed window close.
    }
    try {
      await getCurrentWindow().destroy()
    } catch {
      allowCloseRef.current = false
      setAppNotice(tr("无法关闭窗口，请重试。"))
    }
  }

  const handleWindowClose = useEffectEvent(() => {
    const activeRunning = jobsRef.current.some(task => task.job.status === "running" || task.job.status === "cancelling")
    const continueClose = () => {
      if (activeRunning) setCloseConfirmOpen(true)
      else void finalizeWindowClose()
    }
    runWithLeaveGuard(continueClose)
  })

  useEffect(() => {
    if (!isTauri()) return
    let disposed = false
    let unlisten: (() => void) | undefined
    void getCurrentWindow().onCloseRequested(event => {
      if (allowCloseRef.current) return
      event.preventDefault()
      handleWindowClose()
    }).then(cleanup => {
      if (disposed) cleanup()
      else unlisten = cleanup
    }).catch(() => {})
    return () => { disposed = true; unlisten?.() }
  }, [])

  function matchingTask(key: string | null | undefined) {
    if (!key) return undefined
    return jobsRef.current.find(task => ["running", "cancelling", "queued"].includes(task.job.status)
      && taskInputs.current.get(task.id)?.sourceKey === key)
  }

  function beginNavigation() {
    cancelPendingStart()
    cancelPendingPaste()
    const version = ++navigationVersion.current
    return () => navigationVersion.current === version
  }

  function cancelPendingPaste() {
    if (!pasteRequest.current) return
    pasteRequest.current = undefined
    setPasting(false)
  }

  function cancelPendingStart() {
    if (!startSubmission.current) return
    startSubmission.current = undefined
    submitting.current = false
  }

  function navigateTo(next: Screen) {
    runWithLeaveGuard(() => {
      beginNavigation()
      setScreen(next)
    })
  }

  function showQuickOpen() {
    beginNavigation()
    setTaskCenterOpen(false)
    setQuickOpen(true)
    void refreshLibrary()
  }

  function showTasks() {
    openTasks()
  }

  function openTasks(taskId?: string) {
    beginNavigation()
    setQuickOpen(false)
    setTaskCenterFocusId(taskId)
    setTaskCenterCompletedId(undefined)
    setTaskCenterOpen(true)
  }

  function markReviewed(ids: string[]) {
    setReviewedTasks(current => {
      const next = reviewTasks(current, ids, jobsRef.current)
      return next.length === current.length && next.every((id, index) => id === current[index])
        ? current : saveReviewedTasks(next)
    })
  }

  function markUnreviewed(id: string) {
    setReviewedTasks(current => current.includes(id) ? saveReviewedTasks(current.filter(value => value !== id)) : current)
  }

  function journalSnapshot(current = jobsRef.current, inputs = taskInputs.current, times = taskTimes.current): TaskJournal {
    const tasks = current.flatMap(task => {
      if (task.job.status === "done") return []
      const input = inputs.get(task.id), time = times.get(task.id)
      if (!input || !time) throw new Error(tr("任务输入记录缺失，无法保存队列"))
      return [taskSnapshot(task.id, input, task.job, time.createdAt, time.updatedAt)]
    })
    return parseTaskJournal({ version: 1, tasks })
  }

  async function persistJournal() {
    if (!journalReady.current) throw new Error(tr("请先完成任务记录恢复"))
    const version = ++journalSaveVersion.current
    try {
      await journalWriter.current.write(journalSnapshot())
      if (version === journalSaveVersion.current) setJournalError("")
    } catch (failure) {
      if (version === journalSaveVersion.current) setJournalError(tr("任务记录未能保存。") + String(failure))
      throw failure
    }
  }

  function changeJobs(update: TaskJob[] | ((current: TaskJob[]) => TaskJob[]), persist = true) {
    const next = typeof update === "function" ? update(jobsRef.current) : update
    jobsRef.current = next
    setJobs(next)
    if (persist && journalReady.current) void persistJournal().catch(() => {})
  }

  async function restoreJournal() {
    const version = ++journalLoadVersion.current
    setJournalLoading(true)
    setJournalError("")
    try {
      const restored = restoreTaskJournal(await loadTaskJournal())
      if (version !== journalLoadVersion.current) return
      taskInputs.current = new Map(restored.map(task => [task.id, task.input]))
      taskTimes.current = new Map(restored.map(task => [task.id, { createdAt: task.createdAt, updatedAt: task.updatedAt }]))
      for (const task of restored) {
        if (!task.input.localFile && task.job.failure) {
          rememberSourceFailure(task.input.input, task.job.failure, task.updatedAt)
        }
      }
      journalReady.current = true
      // Restored tasks are display-only until the user explicitly chooses Continue.
      changeJobs(restored.map(({ id, job }) => ({ id, job })))
    } catch (failure) {
      if (version === journalLoadVersion.current) setJournalError(tr("未能读取保存的任务。") + String(failure))
    } finally {
      if (version === journalLoadVersion.current) setJournalLoading(false)
    }
  }

  function retryJournal() {
    if (journalLoading) return
    if (journalReady.current) void persistJournal().catch(() => {})
    else void restoreJournal()
  }

  async function refreshLibrary() {
    const version = ++loadingVersion.current
    const stateVersion = articleStateChanges.current.version
    setLibraryError("")
    try {
      const result = await listArticles()
      if (version === loadingVersion.current) setLibrary({
        ...result, articles: articleStateChanges.current.reconcile(result.articles, stateVersion),
      })
    } catch (error) {
      if (version === loadingVersion.current) setLibraryError(String(error))
    }
  }

  function chooseLocal(paths: string[]) {
    cancelPendingPaste()
    if (paths.length !== 1) {
      setInputError(tr("一次只能导入一个字幕文件"))
      return
    }
    if (!subtitleExtension.test(paths[0])) {
      setInputError(tr("请选择 SRT、VTT 或 JSON 字幕文件"))
      return
    }
    cancelPendingStart()
    inputVersion.current++
    draft.current = paths[0]
    setLocalPath(paths[0])
    setSource("")
    setInputError("")
    setScreen("create")
  }

  useEffect(() => { void refreshLibrary() }, [])
  useEffect(() => {
    void restoreJournal()
    return () => { journalLoadVersion.current++ }
  }, [])
  useEffect(() => {
    if (!completionNotice.length) return
    const timeout = window.setTimeout(() => setCompletionNotice([]), 12_000)
    return () => window.clearTimeout(timeout)
  }, [completionNotice])
  useEffect(() => {
    if (!appNotice) return
    const timeout = window.setTimeout(() => setAppNotice(""), 10_000)
    return () => window.clearTimeout(timeout)
  }, [appNotice])
  useLayoutEffect(() => {
    if (workspace.current) workspace.current.scrollTop = returnScroll.current ?? (screen === "library" ? shelfScroll.current : 0)
    returnScroll.current = undefined
  }, [screen])

  const resolvedAppTheme = resolveTheme(themeMode, systemDark)
  const resolvedActiveTheme = (screen === "reader" && readerTheme) || resolvedAppTheme
  useLayoutEffect(() => {
    const system = window.matchMedia("(prefers-color-scheme: dark)")
    const handleSystemChange = () => setSystemDark(system.matches)
    handleSystemChange()
    system.addEventListener("change", handleSystemChange)
    return () => system.removeEventListener("change", handleSystemChange)
  }, [])
  useLayoutEffect(() => {
    document.documentElement.dataset.theme = resolvedActiveTheme
  }, [resolvedActiveTheme])

  useEffect(() => {
    if (!preferencesReady) return
    let active = true
    setToolStatus(undefined)
    setToolError("")
    checkTool(preferences.provider,
      preferences.provider === "api" ? "" : preferences.paths[preferences.provider]).then(
      status => { if (active) setToolStatus(status) },
      error => { if (active) setToolError(String(error)) },
    )
    return () => { active = false }
  }, [preferences, preferencesReady, apiTokenVersion])

  useEffect(() => {
    if (!isTauri() || (screen !== "create" && screen !== "library")) return
    let active = true
    let unlisten: (() => void) | undefined
    getCurrentWebview().onDragDropEvent(({ payload }) => {
      if (!active) return
      if (payload.type === "enter") setDragging(payload.paths.length > 0)
      if (payload.type === "drop") {
        setDragging(false)
        chooseLocal(payload.paths)
      }
      if (payload.type === "leave") setDragging(false)
    }).then(cleanup => {
      if (active) unlisten = cleanup
      else cleanup()
    }).catch(error => {
      if (active) console.error(tr("LinkRead file drop:"), error)
    })
    return () => {
      active = false
      unlisten?.()
    }
  }, [screen])

  async function showSettings(target?: SettingsTarget) {
    if (screen === "settings") return
    const isCurrent = beginNavigation()
    if (screen === "reader") await flushReadingPositions()
    if (!isCurrent()) return
    if (screen === "library") shelfScroll.current = workspace.current?.scrollTop ?? 0
    setPreviousScreen(screen)
    setSettingsTarget(target)
    setDragging(false)
    setScreen("settings")
  }

  async function configure(next: Preferences) {
    if (isTauri()) {
      await persistNativeEnvelope({
        preferences: next,
        language: languageRef.current,
        theme: themeModeRef.current,
      })
    } else {
      localStorage.setItem(storageKey, JSON.stringify(next))
    }
    if (JSON.stringify(next.youtubeCookies) !== JSON.stringify(preferencesRef.current.youtubeCookies)) {
      clearSubtitleDownloadErrors()
      setYoutubePrompt(undefined)
      if (source.trim()) forgetSourceReadiness(source.trim())
    }
    preferencesRef.current = next
    setPreferences(next)
  }

  function goToLibrary() {
    runWithLeaveGuard(() => {
      beginNavigation()
      if (screen === "create") {
        shelfScroll.current = 0
        setShelfSearch("")
      }
      setDragging(false)
      setScreen("library")
      void refreshLibrary()
    })
  }

  function setLink(value: string) {
    cancelPendingStart()
    cancelPendingPaste()
    inputVersion.current++
    draft.current = value
    setSource(value)
    setLocalPath(null)
    setInputError("")
  }

  async function pick() {
    if (picking.current) return
    cancelPendingPaste()
    const input = inputVersion.current, navigation = navigationVersion.current
    const current = () => inputVersion.current === input && navigationVersion.current === navigation
    setInputError("")
    picking.current = true
    try {
      const selected = await pickSubtitle()
      if (selected && current()) chooseLocal([selected])
    } catch (error) {
      if (current()) setInputError(String(error))
    } finally {
      picking.current = false
    }
  }

  async function paste({ clipboard, start, end }: ComposerPaste) {
    cancelPendingStart()
    inputVersion.current++
    const request = Symbol("subtitle paste")
    pasteRequest.current = request
    const current = () => pasteRequest.current === request
    setPasting(true)
    setInputError("")
    try {
      const files = clipboard ? Array.from(clipboard.files).slice(0, 2) : []
      let text = clipboard?.getData("text/plain") || ""
      let paths: string[] = []
      // Finder file copies may appear in WebKit as a plain filename or empty
      // paste data. In either case read the native file URLs on explicit paste.
      if (!clipboard || (nativePaste && !files.length && (!text
          || (!pastedSubtitlePath(text) && subtitleExtension.test(text.trim()))))) {
        const native = await readSubtitleClipboard()
        if (!current()) return
        paths = native.paths
        text = native.text || text
      }
      if (paths.length > 1 || files.length > 1) throw new Error(tr("一次只能导入一个字幕文件"))
      if (paths.length || (!files.length && pastedSubtitlePath(text))) {
        const path = await resolveSubtitlePath(paths[0] ?? text.trim())
        if (current()) chooseLocal([path])
        return
      }
      if (files.length) {
        const file = files[0]
        if (!subtitleExtension.test(file.name)) throw new Error(tr("请选择 SRT、VTT 或 JSON 字幕文件"))
        if (file.size > maxSubtitleBytes) throw new Error(tr("粘贴的字幕超过 16 MB"))
        const contents = await file.arrayBuffer()
        if (!current()) return
        try { text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(contents) }
        catch { throw new Error(tr("无法读取字幕文件，请使用 UTF-8 编码。")) }
        const path = await savePastedSubtitle(file.name, text)
        if (current()) chooseLocal([path])
        return
      }
      if (text.length > maxSubtitleBytes) throw new Error(tr("粘贴的字幕超过 16 MB"))
      const format = pastedSubtitleFormat(text)
      if (format) {
        const path = await savePastedSubtitle(tr("粘贴的字幕") + "." + format, text)
        if (current()) chooseLocal([path])
        return
      }
      if (!text) return
      try { setLink(normalizeVideoLink(text)) }
      catch {
        if (/[\r\n]/.test(text.trim())) {
          setInputError(tr("请粘贴受支持的链接，或带时间戳的字幕。"))
        } else if (!localPath) {
          setLink(source.slice(0, start) + text.trim() + source.slice(end))
        }
      }
    } catch (error) {
      if (current()) setInputError(error instanceof Error ? error.message : String(error))
    } finally {
      if (current()) cancelPendingPaste()
    }
  }

  function updateTask(id: string, update: (job: Job) => Job) {
    if (!jobsRef.current.some(task => task.id === id)) return
    const time = taskTimes.current.get(id)
    if (time) taskTimes.current.set(id, { ...time, updatedAt: Date.now() })
    changeJobs(current => current.map(task =>
      task.id === id ? { ...task, job: update(task.job) } : task,
    ))
  }

  /** Capture source and settings on submission; run jobs one at a time. */
  function enqueueTask(spec: ActiveGeneration, replaceId?: string) {
    if (!journalReady.current) throw new Error(tr("请先恢复任务记录，再开始生成"))
    const id = crypto.getRandomValues(new Uint32Array(4)).join("-")
    const inputs = new Map(taskInputs.current), times = new Map(taskTimes.current)
    if (replaceId) { inputs.delete(replaceId); times.delete(replaceId) }
    inputs.set(id, spec)
    times.set(id, { createdAt: Date.now(), updatedAt: Date.now() })
    const next: TaskJob[] = [
      ...jobsRef.current.filter(task => task.id !== replaceId),
      { id, job: { ...spec.identity, previewId: spec.previewId, status: "queued", message: tr("等待前面任务完成") } },
    ]
    // Refuse an oversized queue before mutating state; never silently discard older work.
    journalSnapshot(next, inputs, times)
    taskInputs.current = inputs
    taskTimes.current = times
    changeJobs(next)
    if (replaceId) setPreviewTaskId(current => current === replaceId ? id : current)
    let releaseSettings: (() => void) | undefined
    taskQueue.current.enqueue({
      id,
      beforeStart: async () => {
        try { await persistJournal() } catch (failure) {
          throw { message: tr("任务尚未开始：无法保存恢复记录。") + String(failure),
            code: "task_journal", stage: null, retryable: true, action: "retry" }
        }
        releaseSettings = await settingsGate.current.admit(spec)
      },
      onStart: () => {
        requestId.current = id
        updateTask(id, () => ({
          ...spec.identity, previewId: spec.previewId, status: "running", step: 0,
          message: spec.previewId ? tr("正在检查已保存的字幕与处理结果…") : tr("正在读取字幕…"), percent: 0,
          activity: recordProgress(undefined, { stage: "source", message: tr("正在检查已保存的处理结果"), completed: null, total: null }),
        }))
      },
      run: () => generateArticle(spec.input, spec.provider, spec.executable,
        event => {
          spec.identity = mergeArticleMetadata(spec.identity, event.metadata)
          if (event.previewId) spec.previewId = event.previewId
          updateTask(id, job => updateJob(job, event) ?? job)
        }, () => releaseSettings?.(), id, spec.localFile, spec.replace, spec.baseUrl, spec.model, spec.asr, spec.previewId, spec.pageLanguage, spec.youtubeCookies, spec.reasoningEffort),
      onSuccess: generated => {
        if (!spec.localFile) forgetSourceReadiness(spec.input)
        updateTask(id, () => ({ status: "done", article: generated }))
        taskInputs.current.delete(id)
        taskTimes.current.delete(id)
        setCompletedArticleIds(current => rememberCompletedArticle(current, generated.id))
        setCompletionNotice(current => [generated, ...current.filter(item => item.id !== generated.id)].slice(0, 5))
        void refreshLibrary()
      },
      onFailure: error => {
        const failure = normalizeGenerationFailure(error)
        if (!spec.localFile) {
          rememberSourceFailure(spec.input, failure)
          const prompt = failureToYoutubePrompt(
            spec.input,
            sourcePlatform(spec.input) === "youtube",
            hasYoutubeCookiesConfigured(spec.youtubeCookies),
            preferencesRef.current.asr.enabled,
            failure,
          )
          if (prompt) setYoutubePrompt(prompt)
        }
        if (failure.code !== "cancelled") markUnreviewed(id)
        updateTask(id, current => ({
          ...spec.identity,
          ...(current.status === "done" ? {} : current),
          status: failure.code === "cancelled" ? "cancelled" : "error",
          previewId: spec.previewId, message: failure.message, failure,
        }))
      },
      onFinish: () => {
        releaseSettings?.()
        if (requestId.current === id) requestId.current = null
      },
    })
  }

  async function start(overrideDownloaded?: DownloadedSubtitles) {
    if (submitting.current || pasteRequest.current) return
    const localFile = overrideDownloaded ? false : localPath !== null
    const input = overrideDownloaded ? overrideDownloaded.source : (localPath ?? source.trim())
    if (!input) return
    const downloaded = overrideDownloaded
      ?? (!localFile && subtitleDownload.downloaded && subtitleDownload.source === input ? subtitleDownload.downloaded : undefined)
    const version = inputVersion.current, navigation = navigationVersion.current
    const submission = Symbol("source submission")
    startSubmission.current = submission
    const current = () => startSubmission.current === submission
      && inputVersion.current === version && navigationVersion.current === navigation
    submitting.current = true
    try {
      // An active task may publish its archive while the native lookup is in
      // flight. Recheck after a task transition before deciding to create work.
      const taskState = () => jobsRef.current.map(task => `${task.id}:${task.job.status}`).join("|")
      let checked: Awaited<ReturnType<typeof checkDuplicate>> | undefined
      for (let attempt = 0; attempt < 3; attempt++) {
        const before = taskState()
        const result = await checkDuplicate(input, localFile, downloaded?.previewId)
        if (!current()) return
        if (before === taskState()) { checked = result; break }
      }
      if (!checked) { setInputError(tr("任务状态正在更新，请稍后再试。")); return }
      // Finding saved work comes before model, ASR and source-readiness checks.
      // A protected/unavailable source can still have a perfectly readable archive.
      if (checked.article) { setInputError(""); await read(checked.article); return }
      const inQueue = matchingTask(checked.key)
      if (inQueue) { setInputError(""); openTasks(inQueue.id); return }
      if (!journalReady.current) { setInputError(journalLoading ? tr("正在恢复任务记录，请稍候") : tr("请先重试读取保存的任务")); return }
      if (!localFile && !downloaded && sourceReadiness.blocked) {
        setInputError(tr("此来源已确认暂不支持处理，请导入字幕、查看来源或重新检查。")); return
      }
      const isYoutube = !localFile && sourcePlatform(input) === "youtube"
      const hasCookies = hasYoutubeCookiesConfigured(preferences.youtubeCookies)
      if (!localFile && !downloaded && isYoutube && !hasCookies) {
        const pendingPrompt = resolveYoutubeSubtitlePrompt({
          source: input,
          isYoutube: true,
          cookiesConfigured: false,
          asrEnabled: preferences.asr.enabled,
          code: subtitleDownload.failure?.code ?? sourceReadiness.result?.code,
          status: subtitleDownload.failure ? undefined : sourceReadiness.result?.status,
          message: subtitleDownload.failure?.message ?? sourceReadiness.result?.message,
        })
        if (pendingPrompt) { setYoutubePrompt(pendingPrompt); return }
      }
      if (!localFile && !downloaded && sourceReadiness.result?.status === "needs_asr" && !preferences.asr.enabled) { void showSettings("asr"); return }
      if (toolWarning) { void showSettings("ai"); return }
      enqueueTask({
        ...taskSettingsForGeneration(preferences, { isYoutube, pageLanguage: language.pageLanguage }),
        input,
        localFile,
        sourceKey: checked.key,
        replace: false,
        previewId: downloaded?.previewId,
        youtubeCookies: preferences.youtubeCookies ? { ...preferences.youtubeCookies } : undefined,
        identity: {
          ...(linkPreview.status === "ready" && linkPreview.source === input ? linkPreview.metadata : {}),
          ...(downloaded ? downloaded.metadata : {}),
          title: localFile ? input.split("/").pop()?.replace(subtitleExtension, "")
            : downloaded?.metadata.title
            || (linkPreview.status === "ready" && linkPreview.source === input ? linkPreview.metadata.title : undefined),
          platform: localFile ? "local" : downloaded?.metadata.platform ?? sourcePlatform(input),
        },
      })
      if (downloaded) forgetSubtitleDownload(input)
      // Do not discard a new draft if the user typed while duplicate lookup was running.
      if (draft.current === input || (overrideDownloaded && draft.current.trim() === input)) {
        inputVersion.current++
        draft.current = ""
        setSource("")
        setLocalPath(null)
      }
      if (overrideDownloaded) {
        setStandaloneDownload(undefined)
        setScreen("create")
      }
      setInputError("")
    } catch (failure) {
      if (current()) setInputError(String(failure))
    } finally {
      if (startSubmission.current === submission) {
        startSubmission.current = undefined
        submitting.current = false
      }
    }
  }

  async function cancel(id: string) {
    if (taskQueue.current.cancelWaiting(id)) {
      updateTask(id, current => ({
        ...taskInputs.current.get(id)?.identity,
        ...(current.status === "done" ? {} : current),
        status: "cancelled",
        message: tr("已取消排队"),
      }))
      return
    }
    if (requestId.current !== id) return
    updateTask(id, job => job.status === "running"
      ? { ...job, status: "cancelling", message: tr("正在取消…") } : job)
    try {
      await cancelGeneration(id)
    } catch (error) {
      if (requestId.current === id) {
        updateTask(id, job => job.status === "cancelling"
          ? { ...job, status: "running", message: String(error) } : job)
      }
    }
  }

  async function removeJob(id: string) {
    if (requestId.current === id || taskQueue.current.activeId === id) return
    if (!journalReady.current) throw new Error(tr("请先恢复任务记录"))
    // Removing a failed/cancelled/interrupted task record preserves reusable
    // stage caches, previews and paid ASR results. Persist before mutating UI.
    try {
      await journalWriter.current.write(journalSnapshot(jobsRef.current.filter(task => task.id !== id)))
    } catch (failure) {
      setJournalError(tr("任务记录未能保存。") + String(failure))
      throw new Error(tr("移除未完成，任务记录已保留。") + String(failure))
    }
    if (requestId.current === id || taskQueue.current.activeId === id) return
    taskQueue.current.cancelWaiting(id)
    taskInputs.current.delete(id)
    taskTimes.current.delete(id)
    changeJobs(current => current.filter(task => task.id !== id))
    setPreviewTaskId(current => current === id ? undefined : current)
    setReviewedTasks(current => current.includes(id) ? saveReviewedTasks(current.filter(value => value !== id)) : current)
  }

  async function clearReviewed(ids: string[]) {
    if (!journalReady.current) throw new Error(tr("请先恢复任务记录"))
    const eligible = new Set(reviewedTerminalTaskIds(jobsRef.current, reviewedTasksRef.current))
    const selected = new Set(ids.filter(id => eligible.has(id)))
    if (!selected.size) return
    try {
      await journalWriter.current.write(journalSnapshot(jobsRef.current.filter(task => !selected.has(task.id))))
    } catch (failure) {
      setJournalError(tr("任务记录未能保存。") + String(failure))
      throw new Error(tr("清理未完成，任务记录已保留。") + String(failure))
    }
    const stillEligible = new Set(reviewedTerminalTaskIds(jobsRef.current, reviewedTasksRef.current))
    const removed = new Set([...selected].filter(id => stillEligible.has(id)))
    for (const id of removed) { taskInputs.current.delete(id); taskTimes.current.delete(id) }
    changeJobs(current => current.filter(task => !removed.has(task.id)))
    setPreviewTaskId(current => current && removed.has(current) ? undefined : current)
    setReviewedTasks(current => saveReviewedTasks(current.filter(id => !removed.has(id))))
  }

  async function removeArticle(id: string) {
    const existing = library?.articles.find(item => item.id === id)
      ?? (existingArticle?.id === id ? existingArticle : undefined)
    await deleteArticle(id, existing?.readingKey)
    if (existing?.sourceUrl) forgetSubtitleDownload(existing.sourceUrl)
    setCompletionNotice(current => current.filter(item => item.id !== id))
    changeJobs(current => current.filter(task =>
      task.job.status !== "done" || task.job.article.id !== id))
    setLibrary(current => current
      ? { ...current, articles: current.articles.filter(item => item.id !== id) } : current)
    await refreshLibrary()
  }

  function changeArticleState(id: string, patch: ArticleStatePatch) {
    return articleStateWrites.current.run(id, async () => {
      const state = await updateArticleState(id, patch)
      articleStateChanges.current.record(id, state)
      setLibrary(current => current ? {
        ...current, articles: current.articles.map(item => withArticleState(item, id, state)),
      } : current)
      setArticle(current => current ? withArticleState(current, id, state) : current)
      changeJobs(current => current.map(task => task.job.status === "done"
        ? { ...task, job: { ...task.job, article: withArticleState(task.job.article, id, state) } } : task))
    })
  }

  async function showArticleDialog(selected: Article, kind: "details" | "export") {
    const isCurrent = beginNavigation()
    if (screen === "reader") await flushReadingPositions()
    if (!isCurrent()) return
    if (kind === "details") setDetailsArticle(selected)
    else setExportArticle(selected)
  }

  function articleDetailsSaved(updated: Article) {
    loadingVersion.current++
    const merged = articleStateChanges.current.reconcile([updated], 0)[0]
    setLibrary(current => current ? { ...current, articles: current.articles.map(item => item.id === merged.id ? merged : item) } : current)
    setArticle(current => current?.id === merged.id ? merged : current)
    setCompletionNotice(current => current.map(item => item.id === merged.id ? merged : item))
    changeJobs(current => current.map(task => task.job.status === "done" && task.job.article.id === merged.id
      ? { ...task, job: { ...task.job, article: merged } } : task))
    setDetailsRevisions(current => ({ ...current, [merged.id]: (current[merged.id] ?? 0) + 1 }))
  }

  async function regenerate(selected: Article) {
    if (submitting.current) return
    if (!journalReady.current) throw new Error(tr("请先在新建文章页面恢复保存的任务记录"))
    if (toolWarning) throw new Error(toolWarning + tr("，请先打开设置"))
    submitting.current = true
    try {
      const title = selected.hasAnalysis === false ? tr("AI 整理") : tr("重新整理")
      const confirmed = await askConfirm({
        title,
        message: tr("{0}《{1}》？\n使用 {2} 处理已保存的字幕，可能产生模型费用。新版本成功后才替换旧文章。",
          title, selected.title, providerNames[preferences.provider]),
        confirmLabel: title,
      })
      if (!confirmed) return
      const input = await articleTranscriptPath(selected.id)
      const duplicate = await checkDuplicate(input, true)
      if (matchingTask(duplicate.key)) throw new Error(tr("该文章已在处理或排队，可在任务中心查看。"))
      enqueueTask({
        ...taskSettingsForGeneration(preferences, { isYoutube: false, pageLanguage: language.pageLanguage }),
        input, localFile: true,
        sourceKey: duplicate.key, replace: true,
        identity: selected, replacementArticleId: selected.id,
      })
    } finally {
      submitting.current = false
    }
  }

  async function retry(id: string) {
    if (submitting.current || !journalReady.current) return
    const original = taskInputs.current.get(id)
    const previous = jobsRef.current.find(task => task.id === id)?.job
    if (!original || !previous || !["error", "cancelled", "interrupted"].includes(previous.status)) return
    submitting.current = true
    try {
      const provider = preferences.provider
      const executable = provider === "api" ? "" : preferences.paths[provider]
      const currentTool = await checkTool(provider, executable)
      const unavailable = provider === "api"
        ? !preferences.api.baseUrl || !preferences.api.model
          || (!currentTool.keyAvailable && !isLoopbackBaseUrl(preferences.api.baseUrl))
        : !currentTool.executable || (provider === "claude" && !currentTool.keyAvailable)
      if (unavailable) throw { message: tr("请检查当前 AI 服务设置，再继续此任务。"), code: "settings", stage: null, retryable: true, action: "settings" }
      const previewId = previous.status === "done" ? original.previewId : previous.previewId ?? original.previewId
      const duplicate = await checkDuplicate(original.input, original.localFile, previewId)
      const inQueue = duplicate.key !== null && [...taskInputs.current.entries()].some(([key, input]) =>
        key !== id && input.sourceKey === duplicate.key
        && (taskQueue.current.activeId === key || taskQueue.current.waitingIds.includes(key)))
      const replace = duplicate.article !== null || inQueue
      if (replace) {
        const confirmedReplace = await askConfirm({
          title: tr("重新生成并替换"),
          message: duplicate.article
            ? tr("《{0}》已在书架中。是否重新生成并替换？\n旧文章会在新版本成功后被替换。", duplicate.article.title)
            : tr("该内容已经在生成或排队。是否继续添加重新生成任务？\n新任务会排在后面，并替换前一个版本。"),
          confirmLabel: tr("继续生成"),
        })
        if (!confirmedReplace) return
      }
      const isYoutube = !original.localFile && sourcePlatform(original.input) === "youtube"
      const nextSettings = taskSettingsForGeneration(preferences, { isYoutube, pageLanguage: original.pageLanguage })
      const subtitleLogin = previous.status === "error" && previous.failure?.code === "subtitle_login_required"
        && sourcePlatform(original.input) === "bilibili"
      if (!subtitleLogin && !original.localFile && !previewId && resumeNeedsAsrConfirmation(original.asr, nextSettings.asr)) {
        const confirmedAsr = await askConfirm({
          title: tr("确认语音识别费用"),
          message: tr("当前语音识别设置比此任务上次使用的范围更大，或服务/分片配置已改变。\n继续时只会在没有可读取字幕的情况下使用已启用的语音识别，可能产生新费用。是否继续？"),
          confirmLabel: tr("继续处理"),
        })
        if (!confirmedAsr) return
      }
      enqueueTask({ ...original, ...nextSettings,
        youtubeCookies: preferences.youtubeCookies ? { ...preferences.youtubeCookies } : undefined,
        sourceKey: duplicate.key, replace, previewId,
      }, id)
    } catch (error) {
      const failure = normalizeGenerationFailure(error)
      markUnreviewed(id)
      updateTask(id, current => current.status === "done" ? current
        : { ...current, status: "error", message: failure.message, failure })
    } finally { submitting.current = false }
  }

  function rememberReadingOrigin(completedArticleId?: string, taskId?: string) {
    if (screen === "reader") return
    const scroll = workspace.current?.scrollTop ?? 0
    readingOrigin.current = { screen, scroll, tasks: taskCenterOpen, completedArticleId, taskId }
    if (screen === "library") shelfScroll.current = scroll
  }

  function returnFromReader() {
    const origin = readingOrigin.current
    returnScroll.current = origin.scroll
    setReaderTheme(undefined)
    if (origin.screen === "settings") setSettingsTarget(undefined)
    setScreen(origin.screen)
    if (origin.tasks) {
      setTaskCenterFocusId(origin.taskId)
      setTaskCenterCompletedId(origin.completedArticleId)
      setTaskCenterOpen(true)
    }
    if (origin.screen === "library") void refreshLibrary()
  }

  function latestCompletion(id: string) {
    const current = jobsRef.current
    for (let index = current.length - 1; index >= 0; index--) {
      const task = current[index]
      if (task.job.status === "done" && task.job.article.id === id) return { id: task.id, article: task.job.article }
    }
  }

  async function read(selected: Article, positionFlushed = false) {
    runWithLeaveGuard(() => {
      void (async () => {
        const isCurrent = beginNavigation()
        if (screen === "reader" && !positionFlushed) await flushReadingPositions()
        if (!isCurrent()) return
        rememberReadingOrigin(taskCenterOpen ? selected.id : undefined)
        setTaskCenterOpen(false)
        setOpenedCompletion(latestCompletion(selected.id)?.id)
        if (screen === "reader" && article?.id === selected.id) setReaderSession(current => current + 1)
        setPreviewTaskId(undefined)
        setStandaloneDownload(undefined)
        setCompletionNotice(current => current.filter(item => item.id !== selected.id))
        setArticle(articleStateChanges.current.reconcile([selected], 0)[0])
        setRecentArticles(current => rememberArticle(current, selected.id))
        setDragging(false)
        setScreen("reader")
      })()
    })
  }

  async function readPreview(id: string) {
    const completed = jobsRef.current.find(task => task.id === id)
    if (completed?.job.status === "done") { void read(completed.job.article); return }
    runWithLeaveGuard(() => {
      void (async () => {
        const isCurrent = beginNavigation()
        if (screen === "reader") await flushReadingPositions()
        if (!isCurrent()) return
        const task = jobsRef.current.find(t => t.id === id)
        if (!task) return
        if (task.job.status === "done") { void read(task.job.article, true); return }
        const previewId = task.job.previewId
        if (!previewId) return
        rememberReadingOrigin(undefined, taskCenterOpen ? id : undefined)
        setTaskCenterOpen(false)
        const identity = { ...taskInputs.current.get(id)?.identity, ...task.job }
        setPreviewTaskId(id)
        setStandaloneDownload(undefined)
        setArticle({ id: "preview-" + previewId, previewId, title: identity.title || tr("Transcript preview"),
          hasAnalysis: false, category: "", platform: identity.platform || "local", updatedAt: Date.now(),
          sourceUrl: identity.sourceUrl ?? null, author: identity.author ?? null, publishedAt: identity.publishedAt ?? null,
          durationMs: identity.durationMs ?? null, thumbnailUrl: identity.thumbnailUrl ?? null,
        })
        setDragging(false)
        setScreen("reader")
      })()
    })
  }

  async function openDownloadedPreview(downloaded: DownloadedSubtitles) {
    runWithLeaveGuard(() => {
      void (async () => {
        const isCurrent = beginNavigation()
        if (screen === "reader") await flushReadingPositions()
        if (!isCurrent()) return
        rememberReadingOrigin()
        setTaskCenterOpen(false)
        setPreviewTaskId(undefined)
        setStandaloneDownload(downloaded)
        setArticle({
          id: "preview-" + downloaded.previewId,
          previewId: downloaded.previewId,
          title: downloaded.metadata.title || tr("Transcript preview"),
          hasAnalysis: false,
          category: "",
          platform: downloaded.metadata.platform,
          updatedAt: Date.now(),
          sourceUrl: downloaded.metadata.sourceUrl ?? downloaded.source,
          author: downloaded.metadata.author ?? null,
          publishedAt: downloaded.metadata.publishedAt ?? null,
          durationMs: downloaded.metadata.durationMs ?? null,
          thumbnailUrl: downloaded.metadata.thumbnailUrl ?? null,
          readingKey: downloaded.readingKey,
        })
        setDragging(false)
        setScreen("reader")
      })()
    })
  }

  useEffect(() => {
    async function handleShortcut(event: KeyboardEvent) {
      if (event.defaultPrevented || event.isComposing || event.keyCode === 229 || event.altKey
          || quickOpen || taskCenterOpen || document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) return
      if (event.key === "Escape") {
        // Settings and Reader own Escape.
      } else if (event.metaKey || event.ctrlKey) {
        if (event.key.toLowerCase() === "k") {
          event.preventDefault()
          showQuickOpen()
        } else if (event.key === ",") {
          if (nativePaste && event.metaKey) return
          event.preventDefault()
          showSettings()
        } else if (event.key.toLowerCase() === "o" && screen !== "settings") {
          event.preventDefault()
          const isCurrent = beginNavigation()
          if (screen === "reader") await flushReadingPositions()
          if (!isCurrent()) return
          setScreen("create")
          void pick()
        }
      }
    }
    window.addEventListener("keydown", handleShortcut)
    return () => window.removeEventListener("keydown", handleShortcut)
  }, [screen, previousScreen, quickOpen, taskCenterOpen])

  const toolWarning = !preferencesReady ? undefined
    : toolError
    || (preferences.provider === "api"
      ? (!preferences.api.baseUrl || !preferences.api.model
        ? tr("请在设置中填写 API Base URL 和模型")
        : toolStatus && !toolStatus.keyAvailable && !isLoopbackBaseUrl(preferences.api.baseUrl)
          ? tr("请在设置中保存 API Token") : undefined)
      : toolStatus && !toolStatus.executable ? tr("未检测到 {0}", providerNames[preferences.provider])
      : toolStatus && !toolStatus.keyAvailable ? tr("缺少 ANTHROPIC_API_KEY") : undefined)

  const shelfActions = {
    library, error: libraryError, onRead: read, onRefresh: refreshLibrary,
    onDelete: removeArticle, onRegenerate: regenerate, onStateChange: changeArticleState,
    onEdit: (selected: Article) => { void showArticleDialog(selected, "details") },
    onExportMarkdown: (selected: Article) => { void showArticleDialog(selected, "export") },
  }
  const previewJob = previewTaskId ? jobs.find(task => task.id === previewTaskId)?.job : undefined
  const preview = article?.previewId && previewJob ? {
    id: article.previewId, status: previewJob.status,
    message: previewJob.status === "done" ? undefined : previewJob.message,
    activity: previewJob.status === "done" ? undefined : previewJob.activity,
    article: previewJob.status === "done" ? previewJob.article : undefined,
    onOpenComplete: () => { if (previewJob.status === "done") void read(previewJob.article, true) },
  } : undefined
  const readerTask = screen === "reader" && article && !article.previewId
    ? jobs.find(task => taskInputs.current.get(task.id)?.replacementArticleId === article.id
      && ["queued", "running", "cancelling"].includes(task.job.status)) : undefined
  const completion = screen === "reader" && article && !article.previewId ? latestCompletion(article.id) : undefined
  const readerUpdate = completion && completion.id !== openedCompletion ? completion.article : undefined
  const suppressedId = screen === "reader" ? (preview?.article ?? readerUpdate)?.id : undefined
  const visibleCompletions = completionNotice.filter(item => item.id !== suppressedId)
  const readyArticle = visibleCompletions[0]
  const origin = readingOrigin.current
  const backLabel = origin.tasks ? tr("Tasks") : origin.screen === "create" ? tr("Home") : origin.screen === "settings" ? tr("Settings") : tr("Library")
  const groupedTasks = groupTasks(jobs)
  const unreadTaskIds = unreviewedTaskIds(jobs, reviewedTasks)

  const hasDownloadedSubtitles = !localPath && Boolean(subtitleDownload.downloaded && subtitleDownload.source === source.trim())
  const isYoutubeLink = !localPath && sourcePlatform(source.trim()) === "youtube"
  const youtubeCookiesConfigured = hasYoutubeCookiesConfigured(preferences.youtubeCookies)
  const currentYoutubePrompt = !localPath && !hasDownloadedSubtitles && isYoutubeLink
    ? resolveYoutubeSubtitlePrompt({
        source: source.trim(),
        isYoutube: true,
        cookiesConfigured: youtubeCookiesConfigured,
        asrEnabled: preferences.asr.enabled,
        code: subtitleDownload.failure?.code ?? sourceReadiness.result?.code,
        status: subtitleDownload.failure ? undefined : sourceReadiness.result?.status,
        message: subtitleDownload.failure?.message ?? sourceReadiness.result?.message,
      })
    : undefined
  const sourceNeedsYoutubeCookies = currentYoutubePrompt?.kind === "configure_cookies"
    || currentYoutubePrompt?.kind === "fix_cookies"
  const sourceNeedsAsr = !hasDownloadedSubtitles && !sourceNeedsYoutubeCookies
    && sourceReadiness.result?.status === "needs_asr" && !preferences.asr.enabled
  const forcedReaderTheme = themeMode === "system" ? undefined : themeMode

  return (
    <div className="app-shell">
      <TitleBar
        screen={screen}
        taskCount={activeTaskCount(jobs)}
        unreadTasks={unreadTaskIds.length}
        themeMode={themeMode}
        resolvedTheme={resolvedAppTheme}
        onToggleTheme={next => {
          void changeTheme(next).catch(() => {})
        }}
        onTasks={showTasks}
        onSettings={() => void showSettings()}
        onQuickOpen={showQuickOpen}
      />
      <main ref={workspace} className={screen === "reader" ? "workspace workspace-reader" : "workspace"}>
        {screen === "create" && (
          <Home {...shelfActions} linkPreview={linkPreview} source={source} setSource={setLink} localPath={localPath}
            nativePaste={nativePaste} pasting={pasting} onPaste={paste}
            onPick={pick} onClear={() => setLink("")} onStart={() => void start()} busy={busy} jobs={jobs}
            reviewedTaskIds={reviewedTasks} onDismissTaskFailure={id => markReviewed([id])}
            onCancel={cancel} onRetry={retry} onRemove={removeJob} toolWarning={toolWarning}
            preferencesReady={preferencesReady} inputError={inputError}
            onPreview={readPreview} journalLoading={journalLoading} journalError={journalError} onRetryJournal={retryJournal}
            onTasks={showTasks} taskSummary={{ queued: groupedTasks.queued.length, attention: groupedTasks.attention.length, unread: unreadTaskIds.length }} resume={resume}
            existingArticle={existingArticle} existingTask={Boolean(existingTask)}
            onViewExistingTask={() => openTasks(existingTask?.id)}
            sourceBlocked={!hasDownloadedSubtitles && sourceReadiness.blocked}
            sourceNeedsYoutubeCookies={sourceNeedsYoutubeCookies}
            sourceNeedsAsr={sourceNeedsAsr}
            youtubeCookiesConfigured={youtubeCookiesConfigured}
            asrEnabled={preferences.asr.enabled}
            sourceReadiness={<SourceReadinessPanel state={sourceReadiness}
              subtitleDownload={subtitleDownload} isYoutube={isYoutubeLink}
              youtubeCookiesConfigured={youtubeCookiesConfigured}
              onPreviewDownloaded={downloaded => { void openDownloadedPreview(downloaded) }}
              onExportDownloaded={async (previewId, format: SubtitleExportFormat) => { await exportSubtitles(previewId, format, true) }}
              onYoutubeSettings={() => void showSettings("youtube")}
              onImport={pick} onOpenSource={() => { void openVideo(sourceReadiness.source).catch(error => setInputError(String(error))) }}
              onSettings={() => void showSettings("asr")} asrEnabled={preferences.asr.enabled} />}
            onSettings={target => void showSettings(target ?? "ai")} onLibrary={goToLibrary} dragging={dragging} />
        )}
        {screen === "library" && (
          <Bookshelf {...shelfActions} search={shelfSearch} onSearch={setShelfSearch} filters={shelfFilters}
            onFilters={setShelfFilters} onCreate={() => navigateTo("create")} />
        )}
        {screen === "settings" && (
          <Settings preferences={preferences} onSave={configure} leaveGuard={settingsLeaveGuard} target={settingsTarget}
            language={language} onLanguageChange={changeLanguage} languageError={languageError}
            theme={themeMode} onThemeChange={changeTheme}
            onTokenChanged={() => setApiTokenVersion(current => current + 1)}
            settingsGate={settingsGate.current} settingsRecovery={settingsRecovery}
            onBack={() => navigateTo(previousScreen)} />
        )}
        {screen === "reader" && article && <Reader key={`${article.id}:${readerSession}`} article={article} onBack={returnFromReader} backLabel={backLabel}
          onRegenerate={regenerate} onDelete={removeArticle} onStateChange={changeArticleState} preview={preview}
          standalonePreview={standaloneDownload && article.previewId === standaloneDownload.previewId
            ? { onGenerate: () => { void start(standaloneDownload) } } : undefined}
          onNavigationStart={beginNavigation}
          onThemeChange={setReaderTheme}
          forcedTheme={forcedReaderTheme}
          youtubeCookies={preferences.youtubeCookies}
          onViewRegeneration={readerTask ? () => openTasks(readerTask.id) : undefined}
          onOpenUpdated={readerUpdate ? () => void read(readerUpdate, true) : undefined}
          onEdit={shelfActions.onEdit} onExportMarkdown={shelfActions.onExportMarkdown} detailsRevision={detailsRevisions[article.id]} />}
      </main>
      {detailsArticle && <ArticleDetailsDialog key={detailsArticle.id} article={detailsArticle} onSaved={articleDetailsSaved} onClose={() => setDetailsArticle(undefined)} />}
      {exportArticle && <MarkdownExportDialog key={exportArticle.id} article={exportArticle} onClose={() => setExportArticle(undefined)} />}
      {youtubePrompt && screen === "create" && !detailsArticle && !exportArticle && !pendingConfirm && !closeConfirmOpen && !quickOpen && !taskCenterOpen && (
        <YoutubeSubtitleDialog
          prompt={youtubePrompt}
          onConfigureCookies={() => { setYoutubePrompt(undefined); setTaskCenterOpen(false); void showSettings("youtube") }}
          onConfigureAsr={() => { setYoutubePrompt(undefined); setTaskCenterOpen(false); void showSettings("asr") }}
          onImportSubtitles={() => { setYoutubePrompt(undefined); setTaskCenterOpen(false); setScreen("create"); void pick() }}
          onClose={() => setYoutubePrompt(undefined)} />
      )}
      {update.prompt && !youtubePrompt && !detailsArticle && !exportArticle && !pendingConfirm && !closeConfirmOpen && !quickOpen && !taskCenterOpen && (
        <AppUpdateDialog prompt={update.prompt} busy={!canInstallUpdate} onClose={update.dismiss}
          onCheck={() => update.check()} onInstall={() => void update.install()} />
      )}
      {taskCenterOpen && <TaskCenter jobs={jobs} reviewedIds={reviewedTasks} onReviewed={markReviewed}
        completed={completedArticles(library?.articles ?? [], completedArticleIds)}
        initialTaskId={taskCenterFocusId}
        initialCompletedArticleId={taskCenterCompletedId}
        youtubeCookiesConfigured={youtubeCookiesConfigured}
        asrEnabled={preferences.asr.enabled}
        onClose={() => setTaskCenterOpen(false)} onCancel={cancel} onRetry={retry} onRemove={removeJob}
        onPreview={id => void readPreview(id)}
        onOpenArticle={selected => void read(selected)}
        onSettings={target => { setTaskCenterOpen(false); void showSettings(target) }} onClearReviewed={clearReviewed}
        journalError={journalError} onRetryJournal={retryJournal} />}
      {quickOpen && <QuickOpen library={library} error={libraryError} recent={recentArticles}
        onClose={() => setQuickOpen(false)} onRefresh={refreshLibrary}
        onRead={selected => { setQuickOpen(false); void read(selected) }}
        onLibrary={query => {
          runWithLeaveGuard(() => {
            setQuickOpen(false)
            const isCurrent = beginNavigation()
            void (async () => {
              if (screen === "reader") await flushReadingPositions()
              if (!isCurrent()) return
              shelfScroll.current = 0
              setShelfSearch(query)
              setShelfFilters(defaultShelfFilters)
              setScreen("library")
            })()
          })
        }} />}
      {pendingConfirm && (
        <ConfirmDialog
          title={pendingConfirm.title}
          message={pendingConfirm.message}
          confirmLabel={pendingConfirm.confirmLabel}
          danger={pendingConfirm.danger}
          onConfirm={() => {
            const current = pendingConfirm
            setPendingConfirm(undefined)
            current.resolve(true)
          }}
          onCancel={() => {
            const current = pendingConfirm
            setPendingConfirm(undefined)
            current.resolve(false)
          }}
        />
      )}
      {closeConfirmOpen && (
        <ConfirmDialog
          title={tr("退出 LinkRead")}
          message={tr("当前仍有任务正在处理。\n退出后运行中的生成会中断，但已完成的阶段缓存会保留。确认关闭窗口？")}
          confirmLabel={tr("仍要关闭")}
          danger
          onConfirm={async () => {
            setCloseConfirmOpen(false)
            await finalizeWindowClose()
          }}
          onCancel={() => setCloseConfirmOpen(false)}
        />
      )}
      <ToastBanner
        readyArticle={readyArticle}
        extraCompletedCount={Math.max(0, visibleCompletions.length - 1)}
        notice={appNotice}
        onOpenArticle={selected => void read(selected)}
        onDismissCompleted={() => setCompletionNotice([])}
        onDismissNotice={() => setAppNotice("")}
      />
    </div>
  )
}

createRoot(document.getElementById("root")!).render(<App />)
