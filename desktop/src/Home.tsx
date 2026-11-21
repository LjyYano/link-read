import { normalizeVideoLink } from "./videoLinks"
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type FormEvent, type ReactNode } from "react"
import {
  ArrowDownToLine, ArrowLeft, ArrowRight, ArrowUpRight, BookOpen, Check, Circle, CircleAlert, CircleCheck, FolderOpen,
  FileUp, Link2, LoaderCircle, MoreHorizontal, Pencil, Search, Tv, X, CircleHelp, Sparkles, Trash2, Captions, Podcast, RotateCw, CircleStop, CirclePause, Star,
} from "lucide-react"
import { exportArticle, openVideo, revealArticle, type Article, type ExportFormat, type Library, type GenerationFailure } from "./api"
import { menuNavigation } from "./menu"
import { activeLanes, elapsedLabel, stageRows, progressCaption, progressDetail, requestCaption, type TaskActivity } from "./taskProgress"
import { coverText } from "./coverText"
import "./home.css"
import { useLinkPreview, type LinkPreview } from "./useLinkPreview"
import { thumbnailSource } from "./coverSource"
import { previewKey } from "./previewState"
import { ReadingActions, type ChangeArticleState } from "./ReadingActions"
import { articleState, filterArticles, searchArticles, shelfCounts, defaultShelfFilters, type ReadFilter, type ShelfFilters } from "./libraryState"
import { continueLabel, type ContinueReading } from "./continueReading"
import { selectRecentArticles, sourcePresentation, updatedDatePresentation } from "./homePresentation"

type ArticleIdentity = {
  title?: string
  platform?: Article["platform"]
  author?: string | null
  durationMs?: number | null
  thumbnailUrl?: string | null
  sourceUrl?: string | null
}

export type Job = (
  | { status: "queued"; message: string }
  | { status: "running" | "cancelling"; step: number; message: string; percent: number; count?: [number, number] }
  | { status: "error" | "cancelled" | "interrupted"; message: string }
) & ArticleIdentity & { activity?: TaskActivity; previewId?: string; failure?: GenerationFailure } | { status: "done"; article: Article }

export type TaskJob = { id: string; job: Job }

type ArticleListProps = {
  library: Library | undefined
  error: string
  onRead: (article: Article) => void
  onRefresh: () => void
  onDelete: (id: string) => Promise<void>
  onRegenerate: (article: Article) => Promise<void>
  onStateChange: ChangeArticleState
  onEdit?: (article: Article) => void
  onExportMarkdown?: (article: Article) => void
}

type HomeProps = ArticleListProps & {
  linkPreview: LinkPreview
  providerLabel: string
  source: string
  setSource: (source: string) => void
  localPath: string | null
  onPick: () => void
  onClear: () => void
  onStart: () => void
  busy: boolean
  dragging: boolean
  jobs: TaskJob[]
  onCancel: (id: string) => void
  onRetry: (id: string) => void
  onRemove: (id: string) => void
  onPreview?: (id: string) => void
  journalLoading?: boolean
  journalError?: string
  onRetryJournal?: () => void
  toolWarning?: string
  inputError?: string
  onSettings: () => void
  onLibrary: () => void
  onTasks: () => void
  taskSummary: { queued: number; attention: number; unread: number }
  resume?: ContinueReading
  sourceReadiness?: ReactNode
  sourceBlocked?: boolean
  sourceNeedsAsr?: boolean
  existingArticle?: Article
  existingTask?: boolean
  onViewExistingTask?: () => void
}

const platformLabel = { youtube: "YouTube", bilibili: "B 站", apple_podcasts: "Apple 播客", local: "本地字幕" } as const
const stages = ["提取字幕", "AI 提炼", "校对与整理", "生成文章"]

function fileName(path: string) {
  return path.split("/").at(-1) || path
}

function dateLabel(updatedAt: number) {
  const date = new Date(updatedAt)
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, "0")
  const day = String(date.getDate()).padStart(2, "0")
  return `${year}-${month}-${day}`
}

function PlatformIcon({ platform }: { platform?: Article["platform"] }) {
  if (platform === "youtube") {
    return (
      <svg width="18" height="13" viewBox="0 0 18 13" className="shelf-youtube-icon" aria-hidden="true">
        <rect width="18" height="13" rx="4" fill="currentColor" />
        <path d="m7.1 3.7 5 2.8-5 2.8Z" fill="#fff" />
      </svg>
    )
  }
  if (platform === "bilibili") return <Tv size={15} className="shelf-bilibili-icon" aria-hidden="true" />
  if (platform === "apple_podcasts") return <Podcast size={16} strokeWidth={1.7} className="shelf-podcast-icon" aria-hidden="true" />
  return <Captions size={16} strokeWidth={1.7} className="shelf-local-icon" aria-hidden="true" />
}

function Cover({ platform, thumbnailUrl, title, sourceUrl, withBackdrop }: ArticleIdentity & { withBackdrop?: boolean }) {
  const text = coverText(title)
  const cover = useRef<HTMLSpanElement>(null)
  const [visible, setVisible] = useState(false)
  const [loaded, setLoaded] = useState<string>()
  const [broken, setBroken] = useState<string>()
  const provided = thumbnailSource(thumbnailUrl)
  // Old archives may predate thumbnail persistence. Resolve metadata only for
  // visible Bilibili/podcast cards, without altering transcript/note JSON or re-running AI.
  const preview = useLinkPreview(
    visible && (platform === "bilibili" || platform === "apple_podcasts") && !provided ? sourceUrl || "" : "", false,
  )
  const resolved = provided || (preview.status === "ready" ? thumbnailSource(preview.metadata.thumbnailUrl) : undefined)
  const showCover = visible && resolved && broken !== resolved

  useEffect(() => {
    const target = cover.current
    if (!target) return
    if (typeof IntersectionObserver === "undefined") { setVisible(true); return }
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return
      setVisible(true)
      observer.disconnect()
    })
    observer.observe(target)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!showCover || loaded === resolved) return
    const timeout = window.setTimeout(() => setBroken(resolved), 12000)
    return () => window.clearTimeout(timeout)
  }, [showCover, loaded, resolved])

  return (
    <span ref={cover} className="shelf-cover" data-platform={platform}
      data-cover-tone={text.tone} data-cover-loaded={Boolean(showCover && loaded === resolved)} aria-hidden="true">
      <span className="cover-typography">
        <span className="cover-kicker">LINKREAD / {platform === "local" ? "字幕笔记" : platform === "apple_podcasts" ? "播客笔记" : "阅读笔记"}</span>
        <span className="cover-title" data-compact={text.compact}>{text.label}</span>
        <span className="cover-rule" />
      </span>
      {withBackdrop && showCover && loaded === resolved &&
        <img className="cover-backdrop" alt="" src={resolved} referrerPolicy="no-referrer" />}
      {showCover && <img alt="" src={resolved} referrerPolicy="no-referrer"
        hidden={loaded !== resolved} onLoad={() => setLoaded(resolved)}
        onError={() => setBroken(resolved)} />}
    </span>
  )
}

function SourceDetails({ platform, author, durationMs }: ArticleIdentity) {
  const source = sourcePresentation({ platform, author, durationMs })
  if (!source.name && !source.duration) return null
  return (
    <span className="shelf-source">
      {platform && <span className="shelf-platform" role="img" title={source.platformLabel}
        aria-label={source.platformLabel} aria-hidden={source.name === source.platformLabel || undefined}>
        <PlatformIcon platform={platform} />
      </span>}
      {source.name && <span className="shelf-source-name" title={source.name}>{source.name}</span>}
      {source.name && source.duration && <span aria-hidden="true">·</span>}
      {source.duration && <span className="shelf-source-duration">{source.duration}</span>}
    </span>
  )
}

function ArticleUpdatedAt({ value }: { value: number }) {
  const date = updatedDatePresentation(value)
  return date && <time className="shelf-date" dateTime={date.dateTime} title={date.title} aria-label={date.title}>
    {date.label}
  </time>
}

function ArticleActions({ article, onDelete, onRegenerate, onEdit, onExportMarkdown, onStateChange }: {
  article: Article
  onDelete: (id: string) => Promise<void>
  onRegenerate: (article: Article) => Promise<void>
  onEdit?: (article: Article) => void
  onExportMarkdown?: (article: Article) => void
  onStateChange?: ChangeArticleState
}) {
  const [open, setOpen] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState("")
  const container = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const [menuLayout, setMenuLayout] = useState<CSSProperties>()

  useLayoutEffect(() => {
    if (!open) return
    function place() {
      if (!trigger.current || !panel.current) return
      const anchor = trigger.current.getBoundingClientRect()
      const workspace = trigger.current.closest(".workspace")?.getBoundingClientRect()
      const top = Math.max(0, workspace?.top ?? 0) + 8
      const bottom = Math.min(window.innerHeight, workspace?.bottom ?? window.innerHeight) - 8
      const above = Math.max(0, anchor.top - top - 5), below = Math.max(0, bottom - anchor.bottom - 5)
      const upwards = below < panel.current.scrollHeight + 2 && above > below
      setMenuLayout({ insetBlockStart: upwards ? "auto" : "calc(100% + 5px)",
        insetBlockEnd: upwards ? "calc(100% + 5px)" : "auto", maxHeight: upwards ? above : below, overflowY: "auto" })
    }
    place()
    window.addEventListener("resize", place)
    window.addEventListener("scroll", place, true)
    return () => { window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true) }
  }, [open, error])

  useEffect(() => {
    if (!open) return
    panel.current?.querySelector("button")?.focus({ preventScroll: true })
    function outside(event: PointerEvent) {
      if (event.target instanceof Node && !container.current?.contains(event.target)) setOpen(false)
    }
    document.addEventListener("pointerdown", outside)
    return () => document.removeEventListener("pointerdown", outside)
  }, [open])

  async function perform(action: () => Promise<unknown>) {
    setPending(true)
    setError("")
    try {
      await action()
      setOpen(false)
      trigger.current?.focus()
    } catch (failure) {
      setError(String(failure))
    } finally {
      setPending(false)
    }
  }

  function save(format: ExportFormat) {
    if (format === "markdown" && onExportMarkdown) {
      setOpen(false); trigger.current?.focus(); onExportMarkdown(article); return
    }
    void perform(() => exportArticle(article.id, format))
  }

  function remove() {
    if (!window.confirm("确认永久删除《" + article.title + "》？\n本地字幕、AI 笔记、HTML 阅读页都会一起删除，且无法撤销。")) return
    void perform(() => onDelete(article.id))
  }

  const sourceUrl = article.sourceUrl
  const { isRead, isStarred } = articleState(article)
  const ReadIcon = isRead ? CircleCheck : Circle
  return (
    <div className="shelf-overflow" ref={container} onBlur={event => {
      if (!pending && event.relatedTarget && !container.current?.contains(event.relatedTarget)) setOpen(false)
    }} onKeyDown={event => {
      if (open && menuNavigation(event)) return
      if (event.key !== "Escape" || !open) return
      event.stopPropagation()
      setOpen(false)
      trigger.current?.focus()
    }}>
      <button ref={trigger} className="icon-button shelf-overflow-button" type="button" title="更多操作"
        aria-label={`${article.title}的更多操作`} aria-expanded={open} onClick={() => { setError(""); setOpen(!open) }}>
        <MoreHorizontal size={19} />
      </button>
      {open && <div ref={panel} style={menuLayout} className="shelf-overflow-panel" aria-label="文章操作" role="group" data-action-menu>
        {onStateChange && <>
          <button type="button" disabled={pending} onClick={() => void perform(() => onStateChange(article.id, { isRead: !isRead }))}>
            <ReadIcon size={15} aria-hidden="true" /> {isRead ? "标为未读" : "标为已读"}
          </button>
          <button type="button" disabled={pending} onClick={() => void perform(() => onStateChange(article.id, { isStarred: !isStarred }))}>
            <Star size={15} fill={isStarred ? "currentColor" : "none"} aria-hidden="true" /> {isStarred ? "取消收藏" : "收藏"}
          </button>
        </>}
        <button type="button" disabled={pending} onClick={() => void perform(() => onRegenerate(article))}>
          <Sparkles size={15} /> {article.hasAnalysis === false ? "AI 整理文章" : "重新整理"}
        </button>
        {onEdit && <button type="button" disabled={pending} onClick={() => { setOpen(false); trigger.current?.focus(); onEdit(article) }}>
          <Pencil size={15} /> Edit details
        </button>}
        <button type="button" disabled={pending} onClick={() => save("html")}><ArrowDownToLine size={15} /> 导出 HTML</button>
        <button type="button" disabled={pending} onClick={() => save("markdown")}><ArrowDownToLine size={15} /> 导出 Markdown</button>
        <button type="button" disabled={pending} onClick={() => void perform(() => revealArticle(article.id))}>
          <FolderOpen size={15} /> 在访达显示
        </button>
        {sourceUrl && <button type="button" disabled={pending} onClick={() => void perform(() => openVideo(sourceUrl))}>
          <ArrowUpRight size={15} /> {article.platform === "apple_podcasts" ? "打开原节目" : "打开原视频"}
        </button>}
        <button className="shelf-delete" type="button" disabled={pending} onClick={remove}>
          <Trash2 size={15} /> 删除文章
        </button>
        {error && <p role="alert" className="shelf-overflow-error">{error}</p>}
      </div>}
    </div>
  )
}

function ArticleRow({ article, onRead, onDelete, onRegenerate, onStateChange, onEdit, onExportMarkdown }: {
  article: Article
  onRead: (article: Article) => void
  onDelete: (id: string) => Promise<void>
  onRegenerate: (article: Article) => Promise<void>
  onStateChange: ChangeArticleState
  onEdit?: (article: Article) => void
  onExportMarkdown?: (article: Article) => void
}) {
  return (
    <li className="shelf-row" data-platform={article.platform} data-article-id={article.id}>
      <button type="button" className="shelf-article-link" onClick={() => onRead(article)}>
        <span className="shelf-media"><Cover {...article} /></span>
        <span className="shelf-article-info">
          <span className="shelf-article-title" title={article.title}>{article.title}</span>
          <SourceDetails {...article} />
          {article.hasAnalysis === false && <span className="shelf-raw">Raw transcript</span>}
        </span>
      </button>
      <ReadingActions article={article} onChange={onStateChange} className="shelf-reading-actions" />
      <time className="shelf-date" dateTime={dateLabel(article.updatedAt)}>{dateLabel(article.updatedAt)}</time>
      <ArticleActions article={article} onDelete={onDelete} onRegenerate={onRegenerate} onEdit={onEdit} onExportMarkdown={onExportMarkdown} />
    </li>
  )
}

function ArticleCard({ article, onRead, onDelete, onRegenerate, onStateChange, onEdit, onExportMarkdown }: {
  article: Article
  onRead: (article: Article) => void
  onDelete: (id: string) => Promise<void>
  onRegenerate: (article: Article) => Promise<void>
  onStateChange: ChangeArticleState
  onEdit?: (article: Article) => void
  onExportMarkdown?: (article: Article) => void
}) {
  return (
    <li className="article-card" data-platform={article.platform} data-article-id={article.id}>
      <button type="button" className="article-card-link" onClick={() => onRead(article)}>
        <span className="article-card-media"><Cover {...article} withBackdrop={article.platform === "apple_podcasts"} /></span>
        <span className="article-card-info">
          <span className="article-card-title" title={article.title}>{article.title}</span>
          <SourceDetails {...article} />
        </span>
      </button>
      <div className="article-card-footer">
        <ArticleUpdatedAt value={article.updatedAt} />
        <ArticleActions article={article} onDelete={onDelete} onRegenerate={onRegenerate} onEdit={onEdit}
          onExportMarkdown={onExportMarkdown} onStateChange={onStateChange} />
      </div>
    </li>
  )
}

export function JobRow({ job, queuePosition, onCancel, onRetry, onRemove, onPreview, onSettings, taskId }: {
  job: Job
  queuePosition?: number
  onCancel: () => void
  onRetry: () => void
  onRemove: () => void
  onPreview?: () => void
  onSettings: () => void
  taskId?: string
}) {
  const active = job.status === "running" || job.status === "cancelling"
  const [now, setNow] = useState(Date.now)
  const [actionError, setActionError] = useState("")
  const details = useRef<HTMLDetailsElement>(null)
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [active])
  if (job.status === "done") return null
  const queued = job.status === "queued"
  const unsupportedAudio = job.status === "error"
    && (job.failure?.code === "protected_media" || job.failure?.code === "unsupported_audio_stream")
  const activity = job.activity
  const outstanding = activeLanes(activity)
  const elapsed = activity ? elapsedLabel(Math.max(0, now - activity.startedAt)) : "00:00"
  const quietSeconds = activity ? Math.max(0, Math.floor((now - activity.lastEventAt) / 1000)) : 0
  const caption = queued ? `排队中${queuePosition ? ` · 前方 ${queuePosition} 个任务` : ""}`
    : job.status === "cancelling" ? "正在取消…"
    : active ? progressCaption(activity, stages[job.step] ?? "正在处理")
    : job.status === "interrupted" ? "任务已保留，等待继续"
    : job.status === "cancelled" ? "已取消" : unsupportedAudio ? "暂不支持此音源" : "生成失败"
  const waitingLabel = job.status === "cancelling" ? "等待任务退出"
    : outstanding.some(item => item.label === "提取字幕") ? "等待字幕平台响应"
    : outstanding.some(item => item.label === "生成阅读页") ? "等待文件写入"
    : "等待模型返回"
  const title = job.title || (job.platform === "apple_podcasts" ? "播客单集"
    : job.platform === "local" ? "字幕文件" : job.platform ? `${platformLabel[job.platform]} 视频` : "新文章")
  const lastMessage = progressDetail(activity, job.message)
  const percent = active ? Math.min(99, Math.max(0, job.percent)) : 0
  const requestStatus = requestCaption(activity, now)
  const recoveryAction = unsupportedAudio ? "source" : job.status === "error" ? job.failure?.action ?? "retry" : "retry"
  const recoveryLabel = job.status === "interrupted" ? "继续"
    : recoveryAction === "settings" ? "检查模型设置"
    : recoveryAction === "setup" ? "检查运行环境"
    : recoveryAction === "source" && job.sourceUrl ? "查看来源"
    : recoveryAction === "details" || recoveryAction === "source" ? "查看详情"
    : job.previewId ? "重试未完成部分" : "重试"
  function recover() {
    if (recoveryAction === "settings" || recoveryAction === "setup") { onSettings(); return }
    if (recoveryAction === "source" && job.status !== "done" && job.sourceUrl) {
      void openVideo(job.sourceUrl).catch(error => setActionError(String(error))); return
    }
    if (recoveryAction === "details" || recoveryAction === "source") {
      if (details.current) { details.current.open = true; details.current.querySelector("summary")?.focus() }
      return
    }
    onRetry()
  }
  return (
    <li className={`task-card${active ? " task-card-active" : ""}${job.status === "error" ? " task-card-error" : ""}`} data-platform={job.platform} data-task-id={taskId}>
      <div className="task-card-heading">
        <Cover {...job} />
        <div className="task-card-info">
          <h3 title={title}>{title}</h3>
          <SourceDetails {...job} />
        </div>
        <div className="task-card-actions">
          {active || queued ? (
            <button className="icon-button" type="button" aria-label={queued ? "取消排队" : "取消生成"}
              title={queued ? "取消排队" : "取消生成"} disabled={job.status === "cancelling"} onClick={onCancel}>
              <X size={17} />
            </button>
          ) : <>
            <button className="secondary" type="button" onClick={recover}>{recoveryLabel}</button>
            {recoveryAction !== "retry" && !unsupportedAudio && <button className="text-button" type="button" onClick={onRetry}>重试</button>}
            <button className="icon-button" type="button" title="删除任务" aria-label="删除任务" onClick={onRemove}>
              <Trash2 size={16} />
            </button>
          </>}
        </div>
      </div>
      <div className="task-card-progress">
        <div className="task-status-line">
          <span className="task-status" role="status">
            {active && <LoaderCircle size={14} className="spin" aria-hidden="true" />}
            {job.status === "error" && <CircleAlert size={14} aria-hidden="true" />}
            {job.status === "interrupted" && <CirclePause size={14} aria-hidden="true" />}
            {caption}
          </span>
          {active && <span className="task-elapsed" title="已用时，不是剩余时间">已用时 {elapsed}</span>}
        </div>
        {active && <>
          <div className="task-meter" data-running={job.status === "running"}>
            <progress className="shelf-progress" value={percent} max={100}
              aria-label="整体流程进度（按阶段估算，不代表剩余时间）" />
            <span className="task-meter-motion" style={{ width: `${percent}%` }} aria-hidden="true"><span className="task-meter-sweep" /></span>
          </div>
          <p className="task-detail-line">
            {requestStatus ?? (quietSeconds >= 20 ? `${waitingLabel} · ${quietSeconds} 秒未收到新结果` : lastMessage)}
          </p>
        </>}
        {queued && <p className="task-detail-line">前一任务结束后自动开始，可继续添加链接。</p>}
        {job.status === "interrupted" && <p className="task-detail-line">{job.message}</p>}
        {job.status === "error" && <p className="task-error-summary">{job.message.split("\n")[0].slice(0, 100)}</p>}
        {actionError && <p className="task-error-summary" role="alert">{actionError}</p>}
        {job.previewId && onPreview && <button className="text-button task-read-transcript" type="button" onClick={onPreview}>
          <BookOpen size={14} aria-hidden="true" />先读原文<ArrowRight size={13} aria-hidden="true" />
        </button>}
        {(activity || job.status === "error") && <details className="task-details" ref={details}>
          <summary>{job.status === "error" ? "查看错误详情" : "查看处理详情"}</summary>
          {activity && <>
            <ol className="task-stage-list">
              {stageRows(activity, job.status, job.failure?.stage).map(lane => <li key={lane.key} data-state={lane.state} data-lane={lane.key}>
                <span>
                  {lane.state === "complete" ? <Check size={13} aria-hidden="true" />
                    : lane.state === "running" ? <LoaderCircle size={13} className="spin" aria-hidden="true" />
                    : lane.state === "failed" ? <CircleAlert size={13} aria-hidden="true" />
                    : lane.state === "stopping" ? <CirclePause size={13} aria-hidden="true" />
                    : <CircleStop size={13} aria-hidden="true" />}
                  {lane.label}
                </span>
                <span>{lane.statusLabel}</span>
              </li>)}
            </ol>
            <ol className="task-event-list" aria-label="最近处理记录">
              {activity.history.map((line, index) => <li key={`${line.at}-${index}`}>
                <time>{elapsedLabel(line.at - activity.startedAt)}</time><span>{line.message}</span>
              </li>)}
            </ol>
          </>}
          {job.status === "error" && <pre>{job.message}</pre>}
        </details>}
      </div>
    </li>
  )
}

function LibraryState({ library, error, onRefresh }: Pick<ArticleListProps, "library" | "error" | "onRefresh">) {
  if (error) return (
    <div className="shelf-state shelf-state-error" role="alert">
      <CircleAlert size={19} /><span>{error}</span><button className="secondary" type="button" onClick={onRefresh}>重新加载</button>
    </div>
  )
  if (!library) return <div className="shelf-state" role="status"><LoaderCircle className="spin" size={19} /><span>正在读取书架…</span></div>
  if (library.unavailable) return <p className="shelf-warning" role="status">{library.unavailable} 篇文章无法读取</p>
  return null
}

export function Home({
  source, setSource, localPath, onPick, onClear, onStart, busy, dragging, jobs, onCancel, onRetry, onRemove, linkPreview,
  toolWarning, inputError, onSettings, onLibrary, library, error, onRead, onRefresh, onDelete, onRegenerate, onStateChange, providerLabel,
  onPreview, journalLoading, journalError, onRetryJournal,
  onTasks, taskSummary, resume, sourceReadiness, sourceBlocked, sourceNeedsAsr, existingArticle, existingTask, onViewExistingTask, onEdit, onExportMarkdown,
}: HomeProps) {
  // Keep newly completed articles visible while the library refreshes.
  const finished = jobs.flatMap(task => task.job.status === "done" ? [task.job.article] : []).reverse()
  const recent = selectRecentArticles(finished, library?.articles, resume?.article.id)
  const currentJobs = jobs.filter(task => task.job.status === "running" || task.job.status === "cancelling").slice(0, 1)
  const selectedFile = localPath && fileName(localPath)
  const [helpOpen, setHelpOpen] = useState(false)
  const helpRef = useRef<HTMLDivElement>(null)
  const helpPinned = useRef(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const podcastPreview = previewKey(source)?.startsWith("apple_podcasts:")
  const existing = Boolean(existingArticle || existingTask)

  function closeHelp() {
    helpPinned.current = false
    setHelpOpen(false)
  }

  useEffect(() => {
    if (!helpOpen) return
    const outside = (event: PointerEvent) => {
      if (!helpRef.current?.contains(event.target as Node)) closeHelp()
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented || event.isComposing || event.repeat
        || document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) return
      event.preventDefault()
      event.stopPropagation()
      closeHelp()
    }
    document.addEventListener("pointerdown", outside)
    document.addEventListener("keydown", escape, true)
    return () => {
      document.removeEventListener("pointerdown", outside)
      document.removeEventListener("keydown", escape, true)
    }
  }, [helpOpen])

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if ((existing || !sourceBlocked) && (localPath || source.trim())) onStart()
  }

  return (
    <div className="home-page">
      <form className="home-form" onSubmit={submit}>
        <div className={`home-dropzone${dragging ? " home-dropzone-active" : ""}`}>
          <div className="home-input-field">
            <span className="home-input-mark" aria-hidden="true">
              {selectedFile ? <Captions size={25} className="home-input-icon" /> : <Link2 size={25} className="home-input-icon" />}
            </span>
            <input ref={inputRef} aria-label={selectedFile ? "选中的字幕文件" : "视频或播客链接"} type="text" inputMode={selectedFile ? "text" : "url"}
              readOnly={Boolean(selectedFile)} autoComplete="off" spellCheck={false} value={selectedFile || source}
              title={selectedFile || undefined} placeholder={dragging ? "松开以导入字幕文件" : "粘贴视频或播客链接"}
              onChange={event => setSource(event.target.value)}
                onPaste={event => {
                  if (localPath) return
                  try {
                    const link = normalizeVideoLink(event.clipboardData.getData("text"))
                    event.preventDefault()
                    setSource(link)
                  } catch { /* Keep incomplete input editable; generation shows the validation error. */ }
                }} />
            {(selectedFile || source) && <button className="icon-button home-inline-button" type="button"
              title={selectedFile ? "移除字幕文件" : "清空链接"} aria-label={selectedFile ? "移除字幕文件" : "清空链接"}
              onClick={() => { onClear(); inputRef.current?.focus() }}><X size={16} /></button>}
          </div>
          <div className="home-input-actions">
            <button className="plain home-import-button" type="button" title="导入字幕" aria-label="导入字幕" onClick={onPick}>
              <FileUp size={18} aria-hidden="true" /><span>导入字幕</span>
            </button>
            <div ref={helpRef} className="home-help" onMouseEnter={() => setHelpOpen(true)}
              onMouseLeave={event => {
                if (!helpPinned.current && !event.currentTarget.contains(document.activeElement)) setHelpOpen(false)
              }}
              onBlur={event => { if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget)) closeHelp() }}>
              <button className="icon-button home-inline-button" type="button" aria-label="查看支持的来源"
                aria-expanded={helpOpen} aria-controls={helpOpen ? "home-help-content" : undefined}
                onClick={event => {
                  event.currentTarget.focus({ preventScroll: true })
                  helpPinned.current = !helpPinned.current
                  setHelpOpen(helpPinned.current)
                }}>
                <CircleHelp size={19} aria-hidden="true" />
              </button>
              {helpOpen && <div id="home-help-content" className="home-help-popover" role="note">
                支持 YouTube、B 站视频、Apple 播客单集，以及 SRT / VTT / JSON 字幕文件。
                <span>文件可选择或拖入，生成时会调用所选 AI 整理。无字幕时可在设置中开启语音识别。</span>
              </div>}
            </div>
            <button className="primary home-primary" type="submit" disabled={(!existing && (journalLoading || sourceBlocked)) || (!localPath && !source.trim())}
              onClick={event => event.currentTarget.focus({ preventScroll: true })}>
              {existingArticle ? <BookOpen size={17} /> : existingTask ? <CirclePause size={17} /> : <Sparkles size={17} />}
              {existingArticle ? "打开文章" : existingTask ? "查看任务" : sourceBlocked ? "暂不支持此来源" : sourceNeedsAsr ? "设置语音识别" : busy ? "加入队列" : "生成文章"}
            </button>
          </div>
        </div>
        {existingArticle && <div className="source-preview existing-source-preview" aria-label="已有文章">
          <Cover {...existingArticle} />
          <div className="source-preview-info"><strong>{existingArticle.title}</strong><SourceDetails {...existingArticle} />
            <span className="existing-source-status"><Check size={13} />已在书架中{existingTask ? " · 正在更新" : ""}</span></div>
          {existingTask && <button className="text-button" type="button" onClick={event => {
            event.currentTarget.focus({ preventScroll: true }); (onViewExistingTask ?? onTasks)()
          }}>查看任务</button>}
          <ArticleActions article={existingArticle} onDelete={onDelete} onRegenerate={onRegenerate} onEdit={onEdit} onExportMarkdown={onExportMarkdown} />
        </div>}
        {!existingArticle && !selectedFile && linkPreview.status === "loading" && <div className="source-preview preview-loading" role="status" aria-label="正在识别链接">
          <span className="preview-cover preview-pending-cover" data-platform={podcastPreview ? "apple_podcasts" : undefined} aria-hidden="true">
            {podcastPreview ? <Podcast size={28} strokeWidth={1.5} /> : <Link2 size={25} strokeWidth={1.6} />}
          </span>
          <div className="source-preview-info preview-loading-info">
            <strong>{podcastPreview ? "Apple 播客单集" : "正在识别来源"}</strong>
            <span className="preview-status"><LoaderCircle className="spin" size={13} aria-hidden="true" />
              {linkPreview.slow ? "获取信息较慢，仍在等待标题与封面" : "正在获取标题与封面…"}
            </span>
          </div>
        </div>}
        {!existingArticle && !selectedFile && linkPreview.status === "ready" && <div className="source-preview" aria-label="来源预览">
          <Cover {...linkPreview.metadata} />
          <div className="source-preview-info">
            <strong>{linkPreview.metadata.title}</strong>
            <SourceDetails {...linkPreview.metadata} />
          </div>
        </div>}
        {!existingArticle && !selectedFile && linkPreview.status === "error" && <div className="preview-unavailable" role="status">
          <span>{linkPreview.timedOut ? "来源预览等待超时" : "暂未获取到标题与封面"}</span>
          <button className="text-button" type="button" onClick={linkPreview.retry}><RotateCw size={12} aria-hidden="true" />重试预览</button>
        </div>}
        {existingTask && !existingArticle && <p className="existing-source-status" role="status"><CirclePause size={14} />该内容正在生成或排队，可直接查看任务。</p>}
        {!existing && !selectedFile && sourceReadiness}
        {!existing && selectedFile && <p className="local-import-hint"><Sparkles size={13} aria-hidden="true" />
          使用 {providerLabel} 整理正文、摘要与导图
        </p>}
        {inputError && <p className="home-tool-warning" role="alert"><CircleAlert size={15} /><span>{inputError}</span></p>}
        {!existing && toolWarning && !inputError && <div className="home-tool-warning" role="status">
          <CircleAlert size={15} /><span>{toolWarning}</span>
          <button className="text-button" type="button" onClick={onSettings}>检查设置 <ArrowUpRight size={13} /></button>
        </div>}
      </form>
      {journalLoading && <p className="task-journal-notice" role="status"><LoaderCircle size={14} className="spin" />正在恢复任务记录…</p>}
      {journalError && <div className="task-journal-notice task-journal-error" role="alert">
        <CircleAlert size={15} /><span>{journalError}</span>
        {onRetryJournal && <button type="button" className="text-button" onClick={onRetryJournal}>重试保存或加载</button>}
      </div>}
      {currentJobs.length > 0 && <section className="home-tasks" aria-label="生成任务">
        <ul className="task-list">
          {currentJobs.map(task => <JobRow key={task.id} job={task.job}
            onCancel={() => onCancel(task.id)} onRetry={() => onRetry(task.id)} onRemove={() => onRemove(task.id)}
            onPreview={onPreview ? () => onPreview(task.id) : undefined} onSettings={onSettings} />)}
        </ul>
      </section>}
      {(taskSummary.queued > 0 || taskSummary.attention > 0) && <div className="home-task-summary" data-unread={taskSummary.unread > 0} role="status">
        {taskSummary.unread > 0 ? <CircleAlert size={15} aria-hidden="true" /> : <CirclePause size={15} aria-hidden="true" />}
        <span>{[
          taskSummary.queued > 0 ? `${taskSummary.queued} 个任务排队中` : "",
          taskSummary.unread > 0 ? `${taskSummary.unread} 个任务需要查看`
            : taskSummary.attention > 0 ? `${taskSummary.attention} 条已保留记录` : "",
        ].filter(Boolean).join(" · ")}</span>
        <button type="button" className="text-button" onClick={event => {
          event.currentTarget.focus({ preventScroll: true }); onTasks()
        }}>查看任务<ArrowRight size={13} aria-hidden="true" /></button>
      </div>}
      {resume && <section className="continue-reading" aria-label="继续阅读">
        <button type="button" className="continue-reading-link" onClick={() => onRead(resume.article)}>
          <span className="continue-reading-cover"><Cover {...resume.article} /></span>
          <span className="continue-reading-copy"><small>继续阅读</small>
            <strong title={resume.article.title}>{resume.article.title}</strong><span>{continueLabel(resume)}</span>
          </span>
          <span className="continue-reading-action" aria-hidden="true"><ArrowRight size={18} /></span>
        </button>
      </section>}
      <section aria-labelledby="home-recent-title" className="home-recent">
        <div className="home-section-title">
          <h2 id="home-recent-title">最近文章</h2>
          <button className="text-button" type="button" onClick={onLibrary}>查看书架 <ArrowRight size={15} /></button>
        </div>
        {recent.length > 0 ? <ul className="recent-grid">
          {recent.map(article => <ArticleCard key={article.id} article={article}
            onRead={onRead} onDelete={onDelete} onRegenerate={onRegenerate} onStateChange={onStateChange} onEdit={onEdit} onExportMarkdown={onExportMarkdown} />)}
        </ul> : library && !error && <div className="shelf-state home-empty" role="status">
          <BookOpen size={22} strokeWidth={1.5} /><span>还没有文章，粘贴链接开始阅读</span>
        </div>}
        <LibraryState library={library} error={error} onRefresh={onRefresh} />
      </section>
    </div>
  )
}

export function Bookshelf({
  library, error, onRead, onRefresh, onDelete, onRegenerate, onStateChange, onCreate, search, onSearch, filters, onFilters, onEdit, onExportMarkdown,
}: ArticleListProps & {
  onCreate: () => void; search: string; onSearch: (text: string) => void
  filters: ShelfFilters; onFilters: (filters: ShelfFilters) => void
}) {
  const matching = searchArticles(library?.articles ?? [], search)
  const articles = filterArticles(matching, filters)
  const counts = shelfCounts(matching, filters.starred)
  const filtered = Boolean(search.trim() || filters.read !== "all" || filters.starred)
  const readFilters: { key: ReadFilter; label: string }[] = [
    { key: "all", label: "All" }, { key: "unread", label: "Unread" }, { key: "completed", label: "Completed" },
  ]

  function resetFilters() {
    onSearch("")
    onFilters(defaultShelfFilters)
  }

  return (
    <section className="bookshelf-page" aria-labelledby="bookshelf-title">
      <button className="plain shelf-back" type="button" onClick={onCreate}><ArrowLeft size={17} /> New Article</button>
      <div className="home-section-title bookshelf-title">
        <h1 id="bookshelf-title">Library</h1>
        {library && library.articles.length > 0 && <span className="shelf-search">
          <Search size={17} aria-hidden="true" />
          <input aria-label="Search library" type="search" value={search} placeholder="Search library" onChange={event => onSearch(event.target.value)} />
          {search && <button className="icon-button" type="button" aria-label="Clear search" title="Clear search" onClick={() => onSearch("")}><X size={15} /></button>}
        </span>}
      </div>
      {library && library.articles.length > 0 && <div className="shelf-filters" role="group" aria-label="Filter library">
        <div className="shelf-read-filters" role="group" aria-label="Reading progress">
          {readFilters.map(filter => <button key={filter.key} type="button" aria-pressed={filters.read === filter.key}
            onClick={() => onFilters({ ...filters, read: filter.key })}>
            {filter.label}<span>{counts[filter.key]}</span>
          </button>)}
        </div>
        <button className="shelf-star-filter" type="button" aria-pressed={filters.starred}
          onClick={() => onFilters({ ...filters, starred: !filters.starred })}>
          <Star size={14} aria-hidden="true" fill={filters.starred ? "currentColor" : "none"} />Starred<span>{counts.starred}</span>
        </button>
        <span className="shelf-filter-result" aria-live="polite">{articles.length} {articles.length === 1 ? "article" : "articles"}</span>
      </div>}
      {articles.length > 0 ? <ul className="shelf-list">
        {articles.map(article => <ArticleRow key={article.id} article={article} onRead={onRead} onDelete={onDelete} onRegenerate={onRegenerate} onStateChange={onStateChange} onEdit={onEdit} onExportMarkdown={onExportMarkdown} />)}
      </ul> : library && !error && <div className="shelf-state bookshelf-empty" role="status">
        {filtered ? <><Search size={20} /><span>No articles match these filters.</span>
          <button className="text-button" type="button" onClick={resetFilters}>Clear filters</button>
        </> : <><BookOpen size={24} strokeWidth={1.5} />
          <button className="text-button" onClick={onCreate} type="button">New Article <ArrowRight size={15} /></button>
        </>}
      </div>}
      <LibraryState library={library} error={error} onRefresh={onRefresh} />
    </section>
  )
}
