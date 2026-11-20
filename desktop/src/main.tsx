import { isTauri } from "@tauri-apps/api/core"
import { getCurrentWebview } from "@tauri-apps/api/webview"
import { getCurrentWindow } from "@tauri-apps/api/window"
import { titlebarMouseDown } from "./titlebarActions"
import { useEffect, useLayoutEffect, useRef, useState } from "react"
import { createRoot } from "react-dom/client"
import { BookOpen, CircleCheck, ListTodo, Search, Settings as SettingsIcon, X } from "lucide-react"
import {
  cancelGeneration, checkDuplicate, checkTool, deleteArticle,
  articleTranscriptPath, generateArticle, listArticles, pickSubtitle, providerNames, updateArticleState,
  loadTaskJournal, saveTaskJournal, normalizeGenerationFailure, openVideo,
  type Article, type ArticleStatePatch, type Library, type Progress, type ToolStatus,
} from "./api"
import { Bookshelf, Home, type Job, type TaskJob } from "./Home"
import { Reader } from "./Reader"
import { ArticleDetailsDialog } from "./ArticleDetailsDialog"
import { MarkdownExportDialog } from "./MarkdownExportDialog"
import { useSourceDuplicate } from "./useSourceDuplicate"
import { QuickOpen } from "./QuickOpen"
import { TaskCenter } from "./TaskCenter"
import { groupTasks, activeTaskCount, unreviewedTaskIds, reviewedTerminalTaskIds,
  loadReviewedTasks, saveReviewedTasks, reviewTasks } from "./taskCenterState"
import { loadRecentArticles, rememberArticle } from "./quickOpenState"
import { useContinueReading } from "./useContinueReading"
import { useSourceReadiness, rememberSourceFailure, forgetSourceReadiness } from "./useSourceReadiness"
import { SourceReadinessPanel } from "./SourceReadinessPanel"
import { flushReadingPositions } from "./readingPositionBridge"
import { progressFromEvent } from "./progress"
import { SerialTaskQueue } from "./taskQueue"
import { TaskJournalWriter, parseTaskJournal, restoreTaskJournal, resumeNeedsAsrConfirmation, taskSnapshot,
  type GenerationInput, type TaskJournal } from "./taskJournal"
import { recordProgress } from "./taskProgress"
import { mergeArticleMetadata } from "./coverSource"
import { normalizeVideoLink } from "./videoLinks"
import { useLinkPreview } from "./useLinkPreview"
import { Settings } from "./Settings"
import type { Preferences } from "./settingsState"
import { defaultAsr, loadAsrOptions } from "./asrSettings"
import { ArticleStateChanges, ArticleStateWrites, defaultShelfFilters, withArticleState, type ShelfFilters } from "./libraryState"
import "./styles.css"
import "./source-preview.css"

type Screen = "create" | "library" | "settings" | "reader"
const storageKey = "linkread-preferences"
const subtitleExtension = /\.(srt|vtt|json)$/i

function loadPreferences(): Preferences {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || "{}")
    return {
      asr: loadAsrOptions(saved.asr),
      provider: saved.provider === "claude" || saved.provider === "api" ? saved.provider : "codex",
      paths: {
        codex: typeof saved.paths?.codex === "string" ? saved.paths.codex : "",
        claude: typeof saved.paths?.claude === "string" ? saved.paths.claude : "",
      },
      api: {
        baseUrl: typeof saved.api?.baseUrl === "string" ? saved.api.baseUrl : "",
        model: typeof saved.api?.model === "string" ? saved.api.model : "",
      },
    }
  } catch {
    return { provider: "codex", paths: { codex: "", claude: "" }, api: { baseUrl: "", model: "" }, asr: { ...defaultAsr } }
  }
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

function TitleBar({ screen, taskCount, unreadTasks, onTasks, onSettings, onQuickOpen }: {
  screen: Screen
  taskCount: number
  unreadTasks: number
  onTasks: () => void
  onSettings: () => void
  onQuickOpen: () => void
}) {
  return (
    <header className="titlebar">
      <div className="titlebar-drag" onMouseDown={event => {
        if (!isTauri()) return
        void titlebarMouseDown(event, getCurrentWindow()).catch(() => {
          console.warn("窗口拖动或缩放操作未完成")
        })
      }} />
      <div className="titlebar-brand" aria-label="LinkRead">
        <span className="titlebar-mark"><BookOpen size={13} strokeWidth={2.1} aria-hidden="true" /></span>
        <span>LinkRead</span>
      </div>
      <div className="titlebar-tools">
      <button className="titlebar-tasks" type="button" aria-label={`任务中心${taskCount ? `，${taskCount} 个活动任务` : ""}${unreadTasks ? `，${unreadTasks} 条未查看` : ""}`}
        title={`任务中心${unreadTasks ? ` · ${unreadTasks} 条记录需要查看` : ""}`} onClick={event => {
          // WebKit does not focus buttons on pointer clicks by default. Make
          // this trigger the dialog's explicit return point before mounting it.
          event.currentTarget.focus({ preventScroll: true }); onTasks()
        }}>
        <ListTodo size={15} aria-hidden="true" /><span>任务</span>
        {taskCount > 0 && <span className="titlebar-task-count">{taskCount}</span>}
        {unreadTasks > 0 && <span className="titlebar-task-alert" aria-hidden="true">!</span>}
      </button>
      <button className="icon-button" type="button" aria-label="快速打开" title="快速打开（⌘K / Ctrl+K）" onClick={onQuickOpen}>
        <Search size={16} aria-hidden="true" />
      </button>
      {(screen === "create" || screen === "library") && (
        <button className="icon-button titlebar-settings" type="button" aria-label="设置" title="设置" onClick={onSettings}>
          <SettingsIcon size={17} aria-hidden="true" />
        </button>
      )}
      </div>
    </header>
  )
}

function App() {
  const [screen, setScreen] = useState<Screen>("create")
  const [previousScreen, setPreviousScreen] = useState<Exclude<Screen, "settings">>("create")
  const [source, setSource] = useState("")
  const [localPath, setLocalPath] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const linkPreview = useLinkPreview(source, localPath !== null)
  const sourceReadiness = useSourceReadiness(source, localPath !== null)
  const [inputError, setInputError] = useState("")
  const [preferences, setPreferences] = useState(loadPreferences)
  const [toolStatus, setToolStatus] = useState<ToolStatus>()
  const [apiTokenVersion, setApiTokenVersion] = useState(0)
  const [toolError, setToolError] = useState("")
  const [library, setLibrary] = useState<Library>()
  const [libraryError, setLibraryError] = useState("")
  const [shelfSearch, setShelfSearch] = useState("")
  const [quickOpen, setQuickOpen] = useState(false)
  const [taskCenterOpen, setTaskCenterOpen] = useState(false)
  const [taskCenterFocusId, setTaskCenterFocusId] = useState<string>()
  const [reviewedTasks, setReviewedTasks] = useState(loadReviewedTasks)
  const reviewedTasksRef = useRef(reviewedTasks)
  reviewedTasksRef.current = reviewedTasks
  const [recentArticles, setRecentArticles] = useState(loadRecentArticles)
  const resume = useContinueReading(library?.articles, recentArticles, screen === "create")
  const [shelfFilters, setShelfFilters] = useState<ShelfFilters>(defaultShelfFilters)
  const [article, setArticle] = useState<Article>()
  const [detailsArticle, setDetailsArticle] = useState<Article>()
  const [exportArticle, setExportArticle] = useState<Article>()
  const [detailsRevisions, setDetailsRevisions] = useState<Record<string, number>>({})
  const [previewTaskId, setPreviewTaskId] = useState<string>()
  const [completionNotice, setCompletionNotice] = useState<Article[]>([])
  const [jobs, setJobs] = useState<TaskJob[]>([])
  const jobsRef = useRef<TaskJob[]>([])
  const taskQueue = useRef(new SerialTaskQueue<Article>())
  const taskInputs = useRef(new Map<string, GenerationInput>())
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
  const workspace = useRef<HTMLElement>(null)
  const shelfScroll = useRef(0)
  const loadingVersion = useRef(0)
  const articleStateChanges = useRef(new ArticleStateChanges())
  const articleStateWrites = useRef(new ArticleStateWrites())
  const requestId = useRef<string | null>(null)
  const navigationVersion = useRef(0)
  const settingsLeaveGuard = useRef<(() => boolean) | null>(null)
  const picking = useRef(false)
  const busy = jobs.some(task => task.job.status === "running" || task.job.status === "cancelling" || task.job.status === "queued")
  const duplicate = useSourceDuplicate(localPath ?? source.trim(), localPath !== null, library)
  const existingArticle = duplicate?.article
    ? library?.articles.find(item => item.id === duplicate.article?.id) ?? duplicate.article : undefined
  const existingTask = matchingTask(duplicate?.key)

  function matchingTask(key: string | null | undefined) {
    if (!key) return undefined
    return jobsRef.current.find(task => ["running", "cancelling", "queued"].includes(task.job.status)
      && taskInputs.current.get(task.id)?.sourceKey === key)
  }

  function beginNavigation() {
    cancelPendingStart()
    const version = ++navigationVersion.current
    return () => navigationVersion.current === version
  }

  function cancelPendingStart() {
    if (!startSubmission.current) return
    startSubmission.current = undefined
    submitting.current = false
  }

  function allowSettingsNavigation() {
    return screen !== "settings" || settingsLeaveGuard.current?.() !== false
  }

  function navigateTo(next: Screen) {
    if (next !== "settings" && !allowSettingsNavigation()) return
    beginNavigation()
    setScreen(next)
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
      if (!input || !time) throw new Error("任务输入记录缺失，无法保存队列")
      return [taskSnapshot(task.id, input, task.job, time.createdAt, time.updatedAt)]
    })
    return parseTaskJournal({ version: 1, tasks })
  }

  async function persistJournal() {
    if (!journalReady.current) throw new Error("请先完成任务记录恢复")
    const version = ++journalSaveVersion.current
    try {
      await journalWriter.current.write(journalSnapshot())
      if (version === journalSaveVersion.current) setJournalError("")
    } catch (failure) {
      if (version === journalSaveVersion.current) setJournalError("任务记录未能保存。" + String(failure))
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
      if (version === journalLoadVersion.current) setJournalError("未能读取保存的任务。" + String(failure))
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
    if (paths.length !== 1) {
      setInputError("一次只能导入一个字幕文件")
      return
    }
    if (!subtitleExtension.test(paths[0])) {
      setInputError("请选择 SRT、VTT 或 JSON 字幕文件")
      return
    }
    cancelPendingStart()
    inputVersion.current++
    draft.current = paths[0]
    setLocalPath(paths[0])
    setSource("")
    setInputError("")
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
  useLayoutEffect(() => {
    if (workspace.current) workspace.current.scrollTop = screen === "library" ? shelfScroll.current : 0
  }, [screen])
  useEffect(() => {
    let active = true
    setToolStatus(undefined)
    setToolError("")
    checkTool(preferences.provider,
      preferences.provider === "api" ? "" : preferences.paths[preferences.provider]).then(
      status => { if (active) setToolStatus(status) },
      error => { if (active) setToolError(String(error)) },
    )
    return () => { active = false }
  }, [preferences, apiTokenVersion])

  useEffect(() => {
    if (!isTauri() || screen !== "create") return
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
      if (active) console.error("LinkRead file drop:", error)
    })
    return () => {
      active = false
      unlisten?.()
    }
  }, [screen])

  async function showSettings() {
    if (screen === "settings") return
    const isCurrent = beginNavigation()
    if (screen === "reader") await flushReadingPositions()
    if (!isCurrent()) return
    if (screen === "library") shelfScroll.current = workspace.current?.scrollTop ?? 0
    setPreviousScreen(screen)
    setDragging(false)
    setScreen("settings")
  }

  function configure(next: Preferences) {
    localStorage.setItem(storageKey, JSON.stringify(next))
    setPreferences(next)
  }

  function goToLibrary() {
    if (!allowSettingsNavigation()) return
    beginNavigation()
    if (screen === "create") {
      shelfScroll.current = 0
      setShelfSearch("")
    }
    setDragging(false)
    setScreen("library")
    void refreshLibrary()
  }

  function setLink(value: string) {
    cancelPendingStart()
    inputVersion.current++
    draft.current = value
    setSource(value)
    setLocalPath(null)
    setInputError("")
  }

  async function pick() {
    if (picking.current) return
    setInputError("")
    picking.current = true
    try {
      const selected = await pickSubtitle()
      if (selected) chooseLocal([selected])
    } catch (error) {
      setInputError(String(error))
    } finally {
      picking.current = false
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
  function enqueueTask(spec: GenerationInput, replaceId?: string) {
    if (!journalReady.current) throw new Error("请先恢复任务记录，再开始生成")
    const id = crypto.getRandomValues(new Uint32Array(4)).join("-")
    const inputs = new Map(taskInputs.current), times = new Map(taskTimes.current)
    if (replaceId) { inputs.delete(replaceId); times.delete(replaceId) }
    inputs.set(id, spec)
    times.set(id, { createdAt: Date.now(), updatedAt: Date.now() })
    const next: TaskJob[] = [
      ...jobsRef.current.filter(task => task.id !== replaceId),
      { id, job: { ...spec.identity, previewId: spec.previewId, status: "queued", message: "等待前面任务完成" } },
    ]
    // Refuse an oversized queue before mutating state; never silently discard older work.
    journalSnapshot(next, inputs, times)
    taskInputs.current = inputs
    taskTimes.current = times
    changeJobs(next)
    if (replaceId) setPreviewTaskId(current => current === replaceId ? id : current)
    taskQueue.current.enqueue({
      id,
      beforeStart: async () => {
        try { await persistJournal() } catch (failure) {
          throw { message: "任务尚未开始：无法保存恢复记录。" + String(failure),
            code: "task_journal", stage: null, retryable: true, action: "retry" }
        }
      },
      onStart: () => {
        requestId.current = id
        updateTask(id, () => ({
          ...spec.identity, previewId: spec.previewId, status: "running", step: 0,
          message: spec.previewId ? "正在检查已保存的字幕与处理结果…" : "正在读取字幕…", percent: 0,
          activity: recordProgress(undefined, { stage: "source", message: "正在检查已保存的处理结果", completed: null, total: null }),
        }))
      },
      run: () => generateArticle(spec.input, spec.provider, spec.executable,
        event => {
          spec.identity = mergeArticleMetadata(spec.identity, event.metadata)
          if (event.previewId) spec.previewId = event.previewId
          updateTask(id, job => updateJob(job, event) ?? job)
        }, id, spec.localFile, spec.replace, spec.baseUrl, spec.model, spec.asr, spec.previewId),
      onSuccess: generated => {
        if (!spec.localFile) forgetSourceReadiness(spec.input)
        updateTask(id, () => ({ status: "done", article: generated }))
        taskInputs.current.delete(id)
        taskTimes.current.delete(id)
        setCompletionNotice(current => [generated, ...current.filter(item => item.id !== generated.id)].slice(0, 5))
        void refreshLibrary()
      },
      onFailure: error => {
        const failure = normalizeGenerationFailure(error)
        if (!spec.localFile) rememberSourceFailure(spec.input, failure)
        if (failure.code !== "cancelled") markUnreviewed(id)
        updateTask(id, current => ({
          ...spec.identity,
          ...(current.status === "done" ? {} : current),
          status: failure.code === "cancelled" ? "cancelled" : "error",
          previewId: spec.previewId, message: failure.message, failure,
        }))
      },
      onFinish: () => {
        if (requestId.current === id) requestId.current = null
      },
    })
  }

  async function start() {
    if (submitting.current) return
    const input = localPath ?? source.trim()
    if (!input) return
    const version = inputVersion.current, navigation = navigationVersion.current
    const submission = Symbol("source submission")
    startSubmission.current = submission
    const current = () => startSubmission.current === submission
      && inputVersion.current === version && navigationVersion.current === navigation
    submitting.current = true
    try {
      const localFile = localPath !== null
      // An active task may publish its archive while the native lookup is in
      // flight. Recheck after a task transition before deciding to create work.
      const taskState = () => jobsRef.current.map(task => `${task.id}:${task.job.status}`).join("|")
      let checked: Awaited<ReturnType<typeof checkDuplicate>> | undefined
      for (let attempt = 0; attempt < 3; attempt++) {
        const before = taskState()
        const result = await checkDuplicate(input, localFile)
        if (!current()) return
        if (before === taskState()) { checked = result; break }
      }
      if (!checked) { setInputError("任务状态正在更新，请稍后再试。"); return }
      // Finding saved work comes before model, ASR and source-readiness checks.
      // A protected/unavailable source can still have a perfectly readable archive.
      if (checked.article) { setInputError(""); await read(checked.article); return }
      const inQueue = matchingTask(checked.key)
      if (inQueue) { setInputError(""); openTasks(inQueue.id); return }
      if (!journalReady.current) { setInputError(journalLoading ? "正在恢复任务记录，请稍候" : "请先重试读取保存的任务"); return }
      if (!localFile && sourceReadiness.blocked) {
        setInputError("此来源已确认暂不支持处理，请导入字幕、查看来源或重新检查。"); return
      }
      if (!localFile && sourceReadiness.result?.status === "needs_asr" && !preferences.asr.enabled) { void showSettings(); return }
      if (toolWarning) { void showSettings(); return }
      enqueueTask({
        input,
        localFile,
        provider: preferences.provider,
        executable: preferences.provider === "api" ? "" : preferences.paths[preferences.provider],
        baseUrl: preferences.api.baseUrl,
        model: preferences.api.model,
        sourceKey: checked.key,
        replace: false,
        asr: { ...preferences.asr },
        identity: {
          ...(linkPreview.status === "ready" && linkPreview.source === input ? linkPreview.metadata : {}),
          title: localFile ? input.split("/").pop()?.replace(subtitleExtension, "")
            : linkPreview.status === "ready" && linkPreview.source === input ? linkPreview.metadata.title : undefined,
          platform: localFile ? "local" : sourcePlatform(input),
        },
      })
      // Do not discard a new draft if the user typed while duplicate lookup was running.
      if (draft.current === input) {
        inputVersion.current++
        draft.current = ""
        setSource("")
        setLocalPath(null)
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
        message: "已取消排队",
      }))
      return
    }
    if (requestId.current !== id) return
    updateTask(id, job => job.status === "running"
      ? { ...job, status: "cancelling", message: "正在取消…" } : job)
    try {
      await cancelGeneration(id)
    } catch (error) {
      if (requestId.current === id) {
        updateTask(id, job => job.status === "cancelling"
          ? { ...job, status: "running", message: String(error) } : job)
      }
    }
  }

  function removeJob(id: string) {
    if (requestId.current === id) return
    taskQueue.current.cancelWaiting(id)
    taskInputs.current.delete(id)
    taskTimes.current.delete(id)
    changeJobs(current => current.filter(task => task.id !== id))
    setPreviewTaskId(current => current === id ? undefined : current)
  }

  async function clearReviewed(ids: string[]) {
    if (!journalReady.current) throw new Error("请先恢复任务记录")
    const eligible = new Set(reviewedTerminalTaskIds(jobsRef.current, reviewedTasksRef.current))
    const selected = new Set(ids.filter(id => eligible.has(id)))
    if (!selected.size) return
    // Persist the removal before hiding rows. Any concurrent progress update is
    // followed by the fresh snapshot below; running/queued jobs never qualify.
    try {
      await journalWriter.current.write(journalSnapshot(jobsRef.current.filter(task => !selected.has(task.id))))
    } catch (failure) {
      setJournalError("任务记录未能保存。" + String(failure))
      throw new Error("清理未完成，任务记录已保留。" + String(failure))
    }
    const stillEligible = new Set(reviewedTerminalTaskIds(jobsRef.current, reviewedTasksRef.current))
    const removed = new Set([...selected].filter(id => stillEligible.has(id)))
    for (const id of removed) { taskInputs.current.delete(id); taskTimes.current.delete(id) }
    changeJobs(current => current.filter(task => !removed.has(task.id)))
    setPreviewTaskId(current => current && removed.has(current) ? undefined : current)
    setReviewedTasks(current => saveReviewedTasks(current.filter(id => !removed.has(id))))
  }

  async function removeArticle(id: string) {
    await deleteArticle(id)
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
    // Invalidate an older library response before publishing the successful edit.
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
    if (!journalReady.current) throw new Error("请先在新建文章页面恢复保存的任务记录")
    if (toolWarning) throw new Error(toolWarning + "，请先打开设置")
    submitting.current = true
    try {
      const title = selected.hasAnalysis === false ? "AI 整理" : "重新整理"
      if (!window.confirm(title + "《" + selected.title + "》？\n使用 " + providerNames[preferences.provider]
        + " 处理已保存的字幕，可能产生模型费用。新版本成功后才替换旧文章。")) return
      const isCurrent = beginNavigation()
      const input = await articleTranscriptPath(selected.id)
      const duplicate = await checkDuplicate(input, true)
      enqueueTask({
        input, localFile: true, provider: preferences.provider,
        executable: preferences.provider === "api" ? "" : preferences.paths[preferences.provider],
        baseUrl: preferences.api.baseUrl, model: preferences.api.model,
        sourceKey: duplicate.key, replace: true, asr: { ...preferences.asr },
        identity: selected,
      })
      if (isCurrent()) {
        if (screen === "reader") await flushReadingPositions()
        if (isCurrent()) setScreen("create")
      }
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
      // A restored record is not an execution plan or a saved spending permission.
      const provider = preferences.provider
      const executable = provider === "api" ? "" : preferences.paths[provider]
      const currentTool = await checkTool(provider, executable)
      const unavailable = provider === "api"
        ? !preferences.api.baseUrl || !preferences.api.model || !currentTool.keyAvailable
        : !currentTool.executable || (provider === "claude" && !currentTool.keyAvailable)
      if (unavailable) throw { message: "请检查当前 AI 服务设置，再继续此任务。", code: "settings", stage: null, retryable: true, action: "settings" }
      const previewId = previous.status === "done" ? original.previewId : previous.previewId ?? original.previewId
      const duplicate = await checkDuplicate(original.input, original.localFile, previewId)
      const inQueue = duplicate.key !== null && [...taskInputs.current.entries()].some(([key, input]) =>
        key !== id && input.sourceKey === duplicate.key
        && (taskQueue.current.activeId === key || taskQueue.current.waitingIds.includes(key)))
      const replace = duplicate.article !== null || inQueue
      if (replace && !window.confirm(duplicate.article
        ? "《" + duplicate.article.title + "》已在书架中。是否重新生成并替换？\n旧文章会在新版本成功后被替换。"
        : "该内容已经在生成或排队。是否继续添加重新生成任务？\n新任务会排在后面，并替换前一个版本。")) return
      const asr = { ...preferences.asr }
      if (!original.localFile && !previewId && resumeNeedsAsrConfirmation(original.asr, asr) && !window.confirm(
        "当前语音识别设置比此任务上次使用的范围更大，或服务/分片配置已改变。\n继续时只会在没有可读取字幕的情况下使用已启用的语音识别，可能产生新费用。是否继续？")) return
      enqueueTask({ ...original, provider, executable,
        baseUrl: preferences.api.baseUrl, model: preferences.api.model, asr,
        sourceKey: duplicate.key, replace, previewId,
      }, id)
    } catch (error) {
      const failure = normalizeGenerationFailure(error)
      markUnreviewed(id)
      updateTask(id, current => current.status === "done" ? current
        : { ...current, status: "error", message: failure.message, failure })
    } finally { submitting.current = false }
  }

  async function read(selected: Article, positionFlushed = false) {
    if (!allowSettingsNavigation()) return
    const isCurrent = beginNavigation()
    if (screen === "reader" && !positionFlushed) await flushReadingPositions()
    if (!isCurrent()) return
    if (screen === "library") shelfScroll.current = workspace.current?.scrollTop ?? 0
    else {
      shelfScroll.current = 0
      setShelfSearch("")
      setShelfFilters(defaultShelfFilters)
    }
    setPreviewTaskId(undefined)
    setCompletionNotice(current => current.filter(item => item.id !== selected.id))
    setArticle(articleStateChanges.current.reconcile([selected], 0)[0])
    setRecentArticles(current => rememberArticle(current, selected.id))
    setDragging(false)
    setScreen("reader")
  }

  async function readPreview(id: string) {
    const completed = jobsRef.current.find(task => task.id === id)
    if (completed?.job.status === "done") { void read(completed.job.article); return }
    if (!allowSettingsNavigation()) return
    const isCurrent = beginNavigation()
    if (screen === "reader") await flushReadingPositions()
    if (!isCurrent()) return
    const task = jobsRef.current.find(task => task.id === id)
    if (!task) return
    if (task.job.status === "done") { void read(task.job.article, true); return }
    const previewId = task.job.previewId
    if (!previewId) return
    const identity = { ...taskInputs.current.get(id)?.identity, ...task.job }
    setPreviewTaskId(id)
    setArticle({ id: "preview-" + previewId, previewId, title: identity.title || "Transcript preview",
      hasAnalysis: false, category: "", platform: identity.platform || "local", updatedAt: Date.now(),
      sourceUrl: identity.sourceUrl ?? null, author: identity.author ?? null, publishedAt: identity.publishedAt ?? null,
      durationMs: identity.durationMs ?? null, thumbnailUrl: identity.thumbnailUrl ?? null,
    })
    setDragging(false)
    setScreen("reader")
  }

  useEffect(() => {
    async function handleShortcut(event: KeyboardEvent) {
      if (event.defaultPrevented || event.isComposing || event.keyCode === 229 || event.altKey
          || quickOpen || taskCenterOpen || document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) return
      if (event.key === "Escape") {
        // Settings and Reader own Escape so they can handle their topmost
        // layer and preserve unsaved work before navigating.
      } else if (event.metaKey || event.ctrlKey) {
        if (event.key.toLowerCase() === "k") {
          event.preventDefault()
          showQuickOpen()
        } else if (event.key === ",") {
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

  const toolWarning = toolError
    || (preferences.provider === "api"
      ? (!preferences.api.baseUrl || !preferences.api.model
        ? "请在设置中填写 API Base URL 和模型"
        : toolStatus && !toolStatus.keyAvailable ? "请在设置中保存 API Token" : undefined)
      : toolStatus && !toolStatus.executable ? `未检测到 ${providerNames[preferences.provider]}`
      : toolStatus && !toolStatus.keyAvailable ? "缺少 ANTHROPIC_API_KEY" : undefined)

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
  const readyArticle = completionNotice[0]
  const showCompletionNotice = readyArticle && !(screen === "reader" && preview?.article?.id === readyArticle.id)
  const groupedTasks = groupTasks(jobs)
  const unreadTaskIds = unreviewedTaskIds(jobs, reviewedTasks)

  return (
    <div className="app-shell">
      <TitleBar screen={screen} taskCount={activeTaskCount(jobs)} unreadTasks={unreadTaskIds.length} onTasks={showTasks} onSettings={showSettings}
        onQuickOpen={showQuickOpen} />
      <main ref={workspace} className={screen === "reader" ? "workspace workspace-reader" : "workspace"}>
        {screen === "create" && (
          <Home {...shelfActions} providerLabel={providerNames[preferences.provider]} linkPreview={linkPreview} source={source} setSource={setLink} localPath={localPath}
            onPick={pick} onClear={() => setLink("")} onStart={start} busy={busy} jobs={jobs}
            onCancel={cancel} onRetry={retry} onRemove={removeJob} toolWarning={toolWarning} inputError={inputError}
            onPreview={readPreview} journalLoading={journalLoading} journalError={journalError} onRetryJournal={retryJournal}
            onTasks={showTasks} taskSummary={{ queued: groupedTasks.queued.length, attention: groupedTasks.attention.length, unread: unreadTaskIds.length }} resume={resume}
            existingArticle={existingArticle} existingTask={Boolean(existingTask)}
            onViewExistingTask={() => openTasks(existingTask?.id)}
            sourceBlocked={sourceReadiness.blocked} sourceNeedsAsr={sourceReadiness.result?.status === "needs_asr" && !preferences.asr.enabled}
            sourceReadiness={<SourceReadinessPanel state={sourceReadiness}
              onImport={pick} onOpenSource={() => { void openVideo(sourceReadiness.source).catch(error => setInputError(String(error))) }}
              onSettings={showSettings} asrEnabled={preferences.asr.enabled} />}
            onSettings={showSettings} onLibrary={goToLibrary} dragging={dragging} />
        )}
        {screen === "library" && (
          <Bookshelf {...shelfActions} search={shelfSearch} onSearch={setShelfSearch} filters={shelfFilters}
            onFilters={setShelfFilters} onCreate={() => navigateTo("create")} />
        )}
        {screen === "settings" && (
          <Settings preferences={preferences} onSave={configure} leaveGuard={settingsLeaveGuard}
            onTokenChanged={() => setApiTokenVersion(current => current + 1)}
            onBack={() => navigateTo(previousScreen)} />
        )}
        {screen === "reader" && article && <Reader key={article.id} article={article} onBack={goToLibrary}
          onRegenerate={regenerate} onStateChange={changeArticleState} preview={preview} onNavigationStart={beginNavigation}
          onEdit={shelfActions.onEdit} onExportMarkdown={shelfActions.onExportMarkdown} detailsRevision={detailsRevisions[article.id]} />}
      </main>
      {detailsArticle && <ArticleDetailsDialog key={detailsArticle.id} article={detailsArticle} onSaved={articleDetailsSaved} onClose={() => setDetailsArticle(undefined)} />}
      {exportArticle && <MarkdownExportDialog key={exportArticle.id} article={exportArticle} onClose={() => setExportArticle(undefined)} />}
      {taskCenterOpen && <TaskCenter jobs={jobs} reviewedIds={reviewedTasks} onReviewed={markReviewed}
        initialTaskId={taskCenterFocusId}
        onClose={() => setTaskCenterOpen(false)} onCancel={cancel} onRetry={retry} onRemove={removeJob}
        onPreview={id => { setTaskCenterOpen(false); void readPreview(id) }}
        onSettings={() => { setTaskCenterOpen(false); void showSettings() }} onClearReviewed={clearReviewed}
        journalError={journalError} onRetryJournal={retryJournal} />}
      {quickOpen && <QuickOpen library={library} error={libraryError} recent={recentArticles}
        onClose={() => setQuickOpen(false)} onRefresh={refreshLibrary}
        onRead={selected => { setQuickOpen(false); void read(selected) }}
        onLibrary={query => {
          if (!allowSettingsNavigation()) return
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
        }} />}
      {showCompletionNotice && <div className="completion-toast" role="status" aria-live="polite">
        <CircleCheck size={18} aria-hidden="true" />
        <div className="completion-toast-content"><strong>Ready to read</strong>
          <span>{readyArticle.title}{completionNotice.length > 1 ? ` · ${completionNotice.length - 1} more completed` : ""}</span>
        </div>
        <button className="text-button" type="button" onClick={() => read(readyArticle)}>Open</button>
        <button className="icon-button" type="button" aria-label="Dismiss notification" onClick={() => setCompletionNotice([])}><X size={15} /></button>
      </div>}
    </div>
  )
}

createRoot(document.getElementById("root")!).render(<App />)

import "./experience.css"
