import { localizedDetail, tr } from "./language.ts"
import type { ComposerPaste } from "./subtitlePaste"
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type FormEvent, type ReactNode } from "react"
import {
  ArrowDownToLine, ArrowRight, ArrowUpRight, BookOpen, Check, Circle, CircleAlert, CircleCheck, FolderOpen,
  FileUp, Link2, LoaderCircle, MoreHorizontal, Pencil, Plus, Search, X, CircleHelp, Sparkles, Trash2, Captions, Podcast, RotateCw, CircleStop, CirclePause, Star,
} from "lucide-react"
import bilibiliIcon from "./assets/bilibili.png"
import { exportArticle, openVideo, revealArticle, type Article, type ExportFormat, type Library, type GenerationFailure } from "./api"
import { menuNavigation } from "./menu"
import { activeLanes, elapsedLabel, stageRows, progressCaption, progressDetail, requestCaption, type TaskActivity } from "./taskProgress"
import { coverText } from "./coverText"
import "./home.css"
import { useLinkPreview, type LinkPreview } from "./useLinkPreview"
import { localCoverSource, thumbnailSource } from "./coverSource"
import { previewKey } from "./previewState"
import type { SettingsTarget } from "./Settings"
import { isYoutubeCookieErrorCode } from "./youtubeGuidance"
import type { ChangeArticleState } from "./ReadingActions"
import { articleState, filterArticles, searchArticles, shelfCounts, defaultShelfFilters, type ReadFilter, type ShelfFilters } from "./libraryState"
import type { ContinueReading } from "./continueReading"
import { selectRecentArticles, sourcePresentation, updatedDatePresentation } from "./homePresentation"

type ArticleIdentity = {
  title?: string
  platform?: Article["platform"]
  author?: string | null
  durationMs?: number | null
  thumbnailUrl?: string | null
  localCover?: string | null
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
  source: string
  setSource: (source: string) => void
  localPath: string | null
  onPick: () => void
  onPaste: (paste: ComposerPaste) => void
  nativePaste: boolean
  pasting: boolean
  onClear: () => void
  onStart: () => void
  busy: boolean
  dragging: boolean
  jobs: TaskJob[]
  onCancel: (id: string) => void
  onRetry: (id: string) => void
  onRemove: (id: string) => Promise<void> | void
  onPreview?: (id: string) => void
  journalLoading?: boolean
  journalError?: string
  onRetryJournal?: () => void
  toolWarning?: string
  inputError?: string
  onSettings: (target?: SettingsTarget) => void
  onLibrary: () => void
  onTasks: () => void
  taskSummary: { queued: number; attention: number; unread: number }
  resume?: ContinueReading
  sourceReadiness?: ReactNode
  sourceBlocked?: boolean
  sourceNeedsAsr?: boolean
  sourceNeedsYoutubeCookies?: boolean
  youtubeCookiesConfigured?: boolean
  asrEnabled?: boolean
  existingArticle?: Article
  existingTask?: boolean
  onViewExistingTask?: () => void
}

const platformLabel = { youtube: "YouTube", bilibili: "B 站", apple_podcasts: "Apple 播客", local: "本地字幕" } as const
const stages = ["提取字幕", "AI 提炼", "校对与整理", "生成文章"]

function fileName(path: string) {
  return path.split("/").at(-1) || path
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
  if (platform === "bilibili") return <img src={bilibiliIcon} width={16} height={16} alt="" />
  if (platform === "apple_podcasts") return <Podcast size={16} strokeWidth={1.7} className="shelf-podcast-icon" aria-hidden="true" />
  return <Captions size={16} strokeWidth={1.7} className="shelf-local-icon" aria-hidden="true" />
}

function Cover({ platform, thumbnailUrl, localCover, title, sourceUrl, withBackdrop }: ArticleIdentity & { withBackdrop?: boolean }) {
  const text = coverText(title)
  const coverPlatform = platform === "local" && sourceUrl && previewKey(sourceUrl)?.startsWith("apple_podcasts:")
    ? "apple_podcasts" : platform
  const cover = useRef<HTMLSpanElement>(null)
  const [visible, setVisible] = useState(false)
  const [loaded, setLoaded] = useState<string>()
  const [broken, setBroken] = useState<string[]>([])
  const local = localCoverSource(localCover)
  const provided = thumbnailSource(thumbnailUrl)
  // Old archives may predate local cover and thumbnail persistence. Resolve metadata
  // only for visible Bilibili/podcast cards, without altering transcript/note JSON or re-running AI.
  const preview = useLinkPreview(
    visible && (platform === "bilibili" || platform === "apple_podcasts") && !local && !provided ? sourceUrl || "" : "", false,
  )
  const remote = provided || (preview.status === "ready" ? thumbnailSource(preview.metadata.thumbnailUrl) : undefined)
  const resolved = (local && !broken.includes(local)) ? local : (remote && !broken.includes(remote) ? remote : undefined)
  const showCover = visible && Boolean(resolved)
  const markBroken = (src: string) => setBroken(current => current.includes(src) ? current : [...current, src])

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
    if (!showCover || !resolved || loaded === resolved) return
    const timeout = window.setTimeout(() => markBroken(resolved), 12000)
    return () => window.clearTimeout(timeout)
  }, [showCover, loaded, resolved])

  return (
    <span ref={cover} className="shelf-cover" data-platform={coverPlatform}
      data-cover-tone={text.tone} data-cover-loaded={Boolean(showCover && loaded === resolved)} aria-hidden="true">
      <span className="cover-typography">
        <span className="cover-kicker">LINKREAD / {platform === "local" ? tr("字幕笔记") : platform === "apple_podcasts" ? tr("播客笔记") : tr("阅读笔记")}</span>
        <span className="cover-title" data-compact={text.compact}>{text.label}</span>
        <span className="cover-rule" />
      </span>
      {withBackdrop && coverPlatform === "apple_podcasts" && showCover && resolved && loaded === resolved &&
        <img className="cover-backdrop" alt="" src={resolved} referrerPolicy="no-referrer" />}
      {showCover && resolved && <img alt="" src={resolved} referrerPolicy="no-referrer"
        hidden={loaded !== resolved} onLoad={() => setLoaded(resolved)}
        onError={() => markBroken(resolved)} />}
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
    const menu = container.current
    const button = trigger.current
    const row = button?.closest(".shelf-row")
    const bookshelf = row?.closest(".bookshelf-page")
    const neighbors = [row?.nextElementSibling, row?.previousElementSibling]
    setPending(true)
    setError("")
    try {
      await action()
      setOpen(false)
      requestAnimationFrame(() => {
        if (document.activeElement !== document.body && !menu?.contains(document.activeElement)) return
        if (button?.isConnected) {
          button.focus({ preventScroll: true })
        } else if (bookshelf?.isConnected) {
          const next = neighbors.find(element => element?.isConnected)?.querySelector<HTMLButtonElement>(".shelf-article-link")
          const filter = bookshelf.querySelector<HTMLButtonElement>(".shelf-star-filter[aria-pressed='true']")
            ?? bookshelf.querySelector<HTMLButtonElement>(".shelf-read-filters [aria-pressed='true']")
            ?? bookshelf.querySelector<HTMLButtonElement>(".bookshelf-title button")
          ;(next ?? filter)?.focus()
        }
      })
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
    if (!window.confirm(tr("确认永久删除《") + article.title + tr("》？\n本地字幕、AI 笔记、HTML 阅读页都会一起删除，且无法撤销。"))) return
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
      <button ref={trigger} className="icon-button shelf-overflow-button" type="button" title={tr("更多操作")}
        aria-label={tr("{0}的更多操作", article.title)} aria-expanded={open} onClick={() => { setError(""); setOpen(!open) }}>
        <MoreHorizontal size={19} />
      </button>
      {open && <div ref={panel} style={menuLayout} className="shelf-overflow-panel" aria-label={tr("文章操作")} role="group" data-action-menu>
        {onStateChange && <>
          <button type="button" disabled={pending} onClick={() => void perform(() => onStateChange(article.id, { isRead: !isRead }))}>
            <ReadIcon size={15} aria-hidden="true" /> {isRead ? tr("标为未读") : tr("标为已读")}
          </button>
          <button type="button" disabled={pending} onClick={() => void perform(() => onStateChange(article.id, { isStarred: !isStarred }))}>
            <Star size={15} fill={isStarred ? "currentColor" : "none"} aria-hidden="true" /> {isStarred ? tr("取消收藏") : tr("收藏")}
          </button>
        </>}
        <button type="button" disabled={pending} onClick={() => void perform(() => onRegenerate(article))}>
          <Sparkles size={15} /> {article.hasAnalysis === false ? tr("AI 整理文章") : tr("重新整理")}
        </button>
        {onEdit && <button type="button" disabled={pending} onClick={() => { setOpen(false); trigger.current?.focus(); onEdit(article) }}>
          <Pencil size={15} /> {tr("编辑详情")}
        </button>}
        <button type="button" disabled={pending} onClick={() => save("html")}><ArrowDownToLine size={15} /> {tr("导出 HTML")}</button>
        <button type="button" disabled={pending} onClick={() => save("markdown")}><ArrowDownToLine size={15} /> {tr("导出 Markdown")}</button>
        <button type="button" disabled={pending} onClick={() => void perform(() => revealArticle(article.id))}>
          <FolderOpen size={15} /> {tr("在访达显示")}
        </button>
        {sourceUrl && <button type="button" disabled={pending} onClick={() => void perform(() => openVideo(sourceUrl))}>
          <ArrowUpRight size={15} /> {article.platform === "apple_podcasts" ? tr("打开原节目") : tr("打开原视频")}
        </button>}
        <button className="shelf-delete" type="button" disabled={pending} onClick={remove}>
          <Trash2 size={15} /> {tr("删除文章")}
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
  const { isRead, isStarred } = articleState(article)
  return (
    <li className="shelf-row" data-platform={article.platform} data-article-id={article.id}>
      <button type="button" className="shelf-article-link" onClick={() => onRead(article)}>
        <span className="shelf-media"><Cover {...article} /></span>
        <span className="shelf-article-info">
          <span className="shelf-article-heading">
            <span className="shelf-article-title" title={article.title}>{article.title}</span>
            {(isRead || isStarred) && <span className="shelf-article-status">
              {isRead && <span className="shelf-status-read" role="img" aria-label={tr("已读")} title={tr("已读")}>
                <CircleCheck size={14} aria-hidden="true" />
              </span>}
              {isStarred && <span className="shelf-status-star" role="img" aria-label={tr("已收藏")} title={tr("已收藏")}>
                <Star size={13} fill="currentColor" aria-hidden="true" />
              </span>}
            </span>}
          </span>
          <span className="shelf-article-meta">
            <SourceDetails {...article} />
            <ArticleUpdatedAt value={article.updatedAt} />
            {article.hasAnalysis === false && <span className="shelf-raw">{tr("原文字幕")}</span>}
          </span>
        </span>
      </button>
      <ArticleActions article={article} onDelete={onDelete} onRegenerate={onRegenerate} onEdit={onEdit}
        onExportMarkdown={onExportMarkdown} onStateChange={onStateChange} />
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
        <span className="article-card-media"><Cover {...article} withBackdrop /></span>
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

export function JobRow({ job, queuePosition, onCancel, onRetry, onRemove, onPreview, onSettings, taskId, youtubeCookiesConfigured = false, asrEnabled = false }: {
  job: Job
  queuePosition?: number
  onCancel: () => void
  onRetry: () => void
  onRemove: () => Promise<void> | void
  onPreview?: () => void
  onSettings: (target?: SettingsTarget) => void
  taskId?: string
  youtubeCookiesConfigured?: boolean
  asrEnabled?: boolean
}) {
  const active = job.status === "running" || job.status === "cancelling"
  const [now, setNow] = useState(Date.now)
  const [actionError, setActionError] = useState("")
  const [removing, setRemoving] = useState(false)
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
  const subtitleLoginRequired = job.status === "error" && job.failure?.code === "subtitle_login_required"
  const isYoutube = job.platform === "youtube"
  const youtubeCookieIssue = job.status === "error" && isYoutubeCookieErrorCode(job.failure?.code)
  const youtubeNeedsCookies = job.status === "error" && isYoutube && !youtubeCookiesConfigured && (
    youtubeCookieIssue
    || job.failure?.code === "subtitles_unavailable"
    || job.failure?.code === "youtube_subtitles_unconfirmed"
    || job.failure?.code === "youtube_subtitle_invalid"
  )
  const offerAsr = job.status === "error" && job.failure?.code === "subtitles_unavailable"
    && (!isYoutube || youtubeCookiesConfigured) && !asrEnabled
  const activity = job.activity
  const outstanding = activeLanes(activity)
  const elapsed = activity ? elapsedLabel(Math.max(0, now - activity.startedAt)) : "00:00"
  const quietSeconds = activity ? Math.max(0, Math.floor((now - activity.lastEventAt) / 1000)) : 0
  const caption = queued ? tr("排队中{0}", queuePosition ? tr(" · 前方 {0} 个任务", queuePosition) : "")
    : job.status === "cancelling" ? tr("正在取消…")
    : active ? progressCaption(activity, tr(stages[job.step] ?? "正在处理"))
    : job.status === "interrupted" ? tr("任务已保留，等待继续")
    : job.status === "cancelled" ? tr("已取消") : subtitleLoginRequired ? tr("字幕需要登录查看")
    : unsupportedAudio ? tr("暂不支持此音源") : tr("生成失败")
  const waitingLabel = job.status === "cancelling" ? tr("等待任务退出")
    : outstanding.some(item => item.label === tr("提取字幕")) ? tr("等待字幕平台响应")
    : outstanding.some(item => item.label === tr("生成阅读页")) ? tr("等待文件写入")
    : tr("等待模型返回")
  const title = job.title || (job.platform === "apple_podcasts" ? tr("播客单集")
    : job.platform === "local" ? tr("字幕文件") : job.platform ? tr("{0} 视频", tr(platformLabel[job.platform])) : tr("新文章"))
  const lastMessage = localizedDetail(progressDetail(activity, job.message), caption)
  const jobMessage = localizedDetail(job.message, caption)
  const percent = active ? Math.min(99, Math.max(0, job.percent)) : 0
  const requestStatus = requestCaption(activity, now)
  const recoveryAction = unsupportedAudio ? "source"
    : youtubeCookieIssue || youtubeNeedsCookies ? "youtube"
    : offerAsr ? "asr"
    : job.status === "error" ? job.failure?.action ?? "retry" : "retry"
  const recoveryLabel = job.status === "interrupted" ? tr("继续")
    : recoveryAction === "youtube" ? tr("配置 YouTube 认证")
    : recoveryAction === "asr" ? tr("设置语音识别")
    : recoveryAction === "settings" ? tr("检查模型设置")
    : recoveryAction === "setup" ? tr("检查运行环境")
    : recoveryAction === "source" && job.sourceUrl ? tr("查看来源")
    : recoveryAction === "details" || recoveryAction === "source" ? tr("查看详情")
    : job.previewId ? tr("重试未完成部分") : tr("重试")
  function recover() {
    if (recoveryAction === "youtube") { onSettings("youtube"); return }
    if (recoveryAction === "asr") { onSettings("asr"); return }
    if (recoveryAction === "settings" || recoveryAction === "setup") { onSettings("ai"); return }
    if (recoveryAction === "source" && job.status !== "done" && job.sourceUrl) {
      void openVideo(job.sourceUrl).catch(error => setActionError(String(error))); return
    }
    if (recoveryAction === "details" || recoveryAction === "source") {
      if (details.current) { details.current.open = true; details.current.querySelector("summary")?.focus() }
      return
    }
    onRetry()
  }
  async function handleRemove() {
    if (removing) return
    if (!window.confirm(tr("确认删除任务「{0}」？\n未生成的本地字幕、预览快照和临时处理缓存也会一并从磁盘删除，且无法撤销。", title))) return
    setRemoving(true)
    setActionError("")
    try {
      await onRemove()
    } catch (error) {
      setActionError(String(error))
    } finally {
      setRemoving(false)
    }
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
            <button className="icon-button" type="button" aria-label={queued ? tr("取消排队") : tr("取消生成")}
              title={queued ? tr("取消排队") : tr("取消生成")} disabled={job.status === "cancelling"} onClick={onCancel}>
              <X size={17} />
            </button>
          ) : <>
            <button className="secondary" type="button" disabled={removing} onClick={recover}>{recoveryLabel}</button>
            {recoveryAction !== "retry" && !unsupportedAudio && <button className="text-button" type="button" disabled={removing} onClick={onRetry}>{tr("重试")}</button>}
            <button className="icon-button" type="button" disabled={removing} title={tr("删除任务与本地缓存")} aria-label={tr("删除任务与本地缓存")} onClick={() => void handleRemove()}>
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
          {active && <span className="task-elapsed" title={tr("已用时，不是剩余时间")}>{tr("已用时")} {elapsed}</span>}
        </div>
        {active && <>
          <div className="task-meter" data-running={job.status === "running"}>
            <progress className="shelf-progress" value={percent} max={100}
              aria-label={tr("整体流程进度（按阶段估算，不代表剩余时间）")} />
            <span className="task-meter-motion" style={{ width: `${percent}%` }} aria-hidden="true"><span className="task-meter-sweep" /></span>
          </div>
          <p className="task-detail-line">
            {requestStatus ?? (quietSeconds >= 20 ? tr("{0} · {1} 秒未收到新结果", waitingLabel, quietSeconds) : lastMessage)}
          </p>
        </>}
        {queued && <p className="task-detail-line">{tr("前一任务结束后自动开始，可继续添加链接。")}</p>}
        {job.status === "interrupted" && <p className="task-detail-line">{jobMessage}</p>}
        {job.status === "error" && <p className="task-error-summary">{jobMessage.split("\n")[0].slice(0, 100)}</p>}
        {actionError && <p className="task-error-summary" role="alert">{actionError}</p>}
        {job.previewId && onPreview && <button className="text-button task-read-transcript" type="button" onClick={onPreview}>
          <BookOpen size={14} aria-hidden="true" />{tr("先读原文")}<ArrowRight size={13} aria-hidden="true" />
        </button>}
        {(activity || job.status === "error") && <details className="task-details" ref={details}>
          <summary>{job.status === "error" ? tr("查看错误详情") : tr("查看处理详情")}</summary>
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
            <ol className="task-event-list" aria-label={tr("最近处理记录")}>
              {activity.history.map((line, index) => <li key={`${line.at}-${index}`}>
                <time>{elapsedLabel(line.at - activity.startedAt)}</time><span>{localizedDetail(line.message, tr("正在处理"))}</span>
              </li>)}
            </ol>
          </>}
          {job.status === "error" && <pre>{jobMessage}</pre>}
        </details>}
      </div>
    </li>
  )
}

function LibraryState({ library, error, onRefresh }: Pick<ArticleListProps, "library" | "error" | "onRefresh">) {
  if (error) return (
    <div className="shelf-state shelf-state-error" role="alert">
      <CircleAlert size={19} /><span>{error}</span><button className="secondary" type="button" onClick={onRefresh}>{tr("重新加载")}</button>
    </div>
  )
  if (!library) return <div className="shelf-state" role="status"><LoaderCircle className="spin" size={19} /><span>{tr("正在读取书架…")}</span></div>
  if (library.unavailable) return <p className="shelf-warning" role="status">{library.unavailable} {tr("篇文章无法读取")}</p>
  return null
}

export function Home({
  source, setSource, localPath, onPick, onClear, onStart, busy, dragging, jobs, onCancel, onRetry, onRemove, linkPreview,
  onPaste, nativePaste, pasting,
  toolWarning, inputError, onSettings, onLibrary, library, error, onRead, onRefresh, onDelete, onRegenerate, onStateChange,
  onPreview, journalLoading, journalError, onRetryJournal,
  onTasks, taskSummary, resume, sourceReadiness, sourceBlocked, sourceNeedsAsr, sourceNeedsYoutubeCookies,
  youtubeCookiesConfigured = false, asrEnabled = false,
  existingArticle, existingTask, onViewExistingTask, onEdit, onExportMarkdown,
}: HomeProps) {
  // Keep newly completed articles visible while the library refreshes.
  const finished = jobs.flatMap(task => task.job.status === "done" ? [task.job.article] : []).reverse()
  const recent = selectRecentArticles(finished, library?.articles, resume?.article.id)
  const currentJobs = jobs.filter(task => task.job.status === "running" || task.job.status === "cancelling").slice(0, 1)
  const selectedFile = localPath && fileName(localPath)
  const [hint, setHint] = useState<"import" | "help" | "pinned-help">()
  const helpOpen = hint === "help" || hint === "pinned-help"
  const importRef = useRef<HTMLDivElement>(null)
  const helpRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const sourceKey = previewKey(source)
  const podcastPreview = sourceKey?.startsWith("apple_podcasts:")
  const existing = Boolean(existingArticle || existingTask)
  const submitDisabled = pasting || (!existing && (journalLoading || sourceBlocked)) || (!localPath && !source.trim())
  const configureAI = !existing && !sourceNeedsAsr && !sourceNeedsYoutubeCookies && !submitDisabled && Boolean(toolWarning && (sourceKey || localPath))

  function showImportHint() {
    setHint(current => current === "pinned-help" ? current : "import")
  }

  function hideImportHint() {
    setHint(current => current === "import" ? undefined : current)
  }

  useEffect(() => {
    if (!hint) return
    const outside = (event: PointerEvent) => {
      const owner = hint === "import" ? importRef.current : helpRef.current
      if (event.target instanceof Node && !owner?.contains(event.target)) setHint(undefined)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented || event.isComposing || event.repeat
        || document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) return
      event.preventDefault()
      event.stopPropagation()
      setHint(undefined)
    }
    document.addEventListener("pointerdown", outside)
    document.addEventListener("keydown", escape, true)
    return () => {
      document.removeEventListener("pointerdown", outside)
      document.removeEventListener("keydown", escape, true)
    }
  }, [hint])

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!submitDisabled) onStart()
  }

  return (
    <div className="home-page">
      <form className="home-form" aria-busy={pasting} onSubmit={submit}>
        <div className={`home-dropzone${dragging ? " home-dropzone-active" : ""}`}>
          <div className="home-input-field">
            <span className="home-input-mark" aria-hidden="true">
              {dragging ? <FileUp size={20} /> : selectedFile ? <Captions size={20} /> : <Link2 size={20} />}
            </span>
            <div className="home-input-content">
              <input ref={inputRef} aria-label={selectedFile ? tr("选中的字幕文件") : tr("视频或播客链接")} type="text" inputMode={selectedFile ? "text" : "url"}
                readOnly={Boolean(selectedFile)} autoComplete="off" spellCheck={false} value={selectedFile || source}
                title={selectedFile || undefined} placeholder={tr("粘贴视频或播客链接")}
                onChange={event => setSource(event.target.value)}
                onPaste={event => {
                  event.preventDefault()
                  const { selectionStart, selectionEnd } = event.currentTarget
                  onPaste({ clipboard: event.clipboardData, start: selectionStart ?? source.length, end: selectionEnd ?? source.length })
                }}
                onKeyDown={event => {
                  if (!nativePaste || !(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "v"
                      || event.altKey || event.shiftKey || event.nativeEvent.isComposing) return
                  event.preventDefault()
                  if (event.repeat) return
                  const { selectionStart, selectionEnd } = event.currentTarget
                  onPaste({ clipboard: null, start: selectionStart ?? source.length, end: selectionEnd ?? source.length })
                }} />
              {dragging && <span className="home-drop-hint" role="status">{tr("松开以导入字幕文件")}</span>}
            </div>
            {(selectedFile || source) && <button className="icon-button home-inline-button" type="button"
              title={selectedFile ? tr("移除字幕文件") : tr("清空链接")} aria-label={selectedFile ? tr("移除字幕文件") : tr("清空链接")}
              onClick={() => { onClear(); inputRef.current?.focus() }}><X size={16} /></button>}
          </div>
          <div className="home-input-actions">
            <div ref={importRef} className="home-import" onMouseEnter={showImportHint}
              onMouseLeave={event => { if (!event.currentTarget.querySelector(":focus-visible")) hideImportHint() }}
              onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) hideImportHint() }}>
              <button className="icon-button home-inline-button" type="button" aria-label={tr("导入字幕")} onFocus={showImportHint}
                onClick={() => { setHint(undefined); onPick() }}><FileUp size={18} aria-hidden="true" /></button>
              {hint === "import" && <span className="home-import-tooltip" aria-hidden="true">{tr("导入字幕")}</span>}
            </div>
            <div ref={helpRef} className="home-help"
              onMouseEnter={() => setHint(current => current === "pinned-help" ? current : "help")}
              onMouseLeave={event => {
                if (!event.currentTarget.querySelector(":focus-visible")) {
                  setHint(current => current === "help" ? undefined : current)
                }
              }}
              onBlur={event => {
                if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget)) {
                  setHint(current => current === "import" ? current : undefined)
                }
              }}>
              <button className="icon-button home-inline-button" type="button" aria-label={tr("查看支持的来源")}
                aria-expanded={helpOpen} aria-controls={helpOpen ? "home-help-content" : undefined}
                onClick={event => {
                  event.currentTarget.focus({ preventScroll: true })
                  setHint(current => current === "pinned-help" ? undefined : "pinned-help")
                }}>
                <CircleHelp size={19} aria-hidden="true" />
              </button>
              {helpOpen && <div id="home-help-content" className="home-help-popover" role="note">
                {tr("支持 YouTube、B 站视频和 Apple 播客单集。")}
                <span>{tr("可粘贴、选择或拖入 SRT、VTT、JSON 字幕。无字幕时可在设置中开启语音识别。")}</span>
              </div>}
            </div>
            <button className="primary home-primary" type="submit" disabled={submitDisabled}
              onClick={event => event.currentTarget.focus({ preventScroll: true })}>
              {existingArticle ? tr("打开文章") : existingTask ? tr("查看任务") : sourceBlocked ? tr("暂不支持此来源") : sourceNeedsYoutubeCookies ? tr("配置 YouTube 认证") : sourceNeedsAsr ? tr("设置语音识别") : configureAI ? tr("配置 AI 工具") : busy ? tr("加入队列") : tr("生成文章")}
            </button>
          </div>
        </div>
        {existingArticle && <div className="source-preview existing-source-preview" aria-label={tr("已有文章")}>
          <Cover {...existingArticle} />
          <div className="source-preview-info"><strong>{existingArticle.title}</strong><SourceDetails {...existingArticle} />
            <span className="existing-source-status"><Check size={13} />{tr("已在书架中")}{existingTask ? tr(" · 正在更新") : ""}</span></div>
          {existingTask && <button className="text-button" type="button" onClick={event => {
            event.currentTarget.focus({ preventScroll: true }); (onViewExistingTask ?? onTasks)()
          }}>{tr("查看任务")}</button>}
          <ArticleActions article={existingArticle} onDelete={onDelete} onRegenerate={onRegenerate} onEdit={onEdit} onExportMarkdown={onExportMarkdown} />
        </div>}
        {!existingArticle && !selectedFile && linkPreview.status === "loading" && <div className="source-preview preview-loading" role="status" aria-label={tr("正在识别链接")}>
          <span className="preview-cover preview-pending-cover" data-platform={podcastPreview ? "apple_podcasts" : undefined} aria-hidden="true">
            {podcastPreview ? <Podcast size={28} strokeWidth={1.5} /> : <Link2 size={25} strokeWidth={1.6} />}
          </span>
          <div className="source-preview-info preview-loading-info">
            <strong>{podcastPreview ? tr("Apple 播客单集") : tr("正在识别来源")}</strong>
            <span className="preview-status"><LoaderCircle className="spin" size={13} aria-hidden="true" />
              {linkPreview.slow ? tr("获取信息较慢，仍在等待标题与封面") : tr("正在获取标题与封面…")}
            </span>
          </div>
        </div>}
        {!existingArticle && !selectedFile && linkPreview.status === "ready" && <div className="source-preview" aria-label={tr("来源预览")}>
          <Cover {...linkPreview.metadata} />
          <div className="source-preview-info">
            <strong>{linkPreview.metadata.title}</strong>
            <SourceDetails {...linkPreview.metadata} />
          </div>
        </div>}
        {!existingArticle && !selectedFile && linkPreview.status === "error" && <div className="preview-unavailable" role="status">
          <span>{linkPreview.timedOut ? tr("来源预览等待超时") : tr("暂未获取到标题与封面")}</span>
          <button className="text-button" type="button" onClick={linkPreview.retry}><RotateCw size={12} aria-hidden="true" />{tr("重试预览")}</button>
        </div>}
        {!existing && !selectedFile && sourceReadiness}
        {inputError && <p className="home-tool-warning" role="alert"><CircleAlert size={15} /><span>{inputError}</span></p>}
        {!existing && toolWarning && !inputError && <div className="home-tool-warning" role="status">
          <CircleAlert size={15} /><span>{toolWarning}</span>
          {!configureAI && <button className="text-button" type="button" onClick={() => onSettings("ai")}>{tr("检查设置")} <ArrowUpRight size={13} /></button>}
        </div>}
      </form>
      {journalLoading && <p className="task-journal-notice" role="status"><LoaderCircle size={14} className="spin" />{tr("正在恢复任务记录…")}</p>}
      {journalError && <div className="task-journal-notice task-journal-error" role="alert">
        <CircleAlert size={15} /><span>{journalError}</span>
        {onRetryJournal && <button type="button" className="text-button" onClick={onRetryJournal}>{tr("重试保存或加载")}</button>}
      </div>}
      {currentJobs.length > 0 && <section className="home-tasks" aria-label={tr("生成任务")}>
        <ul className="task-list">
          {currentJobs.map(task => <JobRow key={task.id} job={task.job}
            onCancel={() => onCancel(task.id)} onRetry={() => onRetry(task.id)} onRemove={() => onRemove(task.id)}
            onPreview={onPreview ? () => onPreview(task.id) : undefined} onSettings={onSettings}
            youtubeCookiesConfigured={youtubeCookiesConfigured} asrEnabled={asrEnabled} />)}
        </ul>
      </section>}
      {(taskSummary.queued > 0 || taskSummary.attention > 0) && <div className="home-task-summary" data-unread={taskSummary.unread > 0} role="status">
        {taskSummary.unread > 0 ? <CircleAlert size={15} aria-hidden="true" /> : <CirclePause size={15} aria-hidden="true" />}
        <span>{[
          taskSummary.queued > 0 ? tr("{0} 个任务排队中", taskSummary.queued) : "",
          taskSummary.unread > 0 ? tr("{0} 个任务需要查看", taskSummary.unread)
            : taskSummary.attention > 0 ? tr("{0} 条已保留记录", taskSummary.attention) : "",
        ].filter(Boolean).join(" · ")}</span>
        <button type="button" className="text-button" onClick={event => {
          event.currentTarget.focus({ preventScroll: true }); onTasks()
        }}>{tr("查看任务")}<ArrowRight size={13} aria-hidden="true" /></button>
      </div>}
      {resume && <section className="continue-reading" aria-label={tr("继续阅读")}>
        <button type="button" className="continue-reading-link" onClick={() => onRead(resume.article)}>
          <span className="continue-reading-cover"><Cover {...resume.article} /></span>
          <span className="continue-reading-copy"><small>{tr("继续阅读")}</small>
            <strong title={resume.article.title}>{resume.article.title}</strong>
          </span>
          <span className="continue-reading-action" aria-hidden="true"><ArrowRight size={18} /></span>
        </button>
      </section>}
      <section aria-labelledby="home-recent-title" className="home-recent">
        <div className="home-section-title">
          <h2 id="home-recent-title">{tr("最近文章")}</h2>
          <button className="text-button" type="button" onClick={onLibrary}>{tr("查看书架")} <ArrowRight size={15} /></button>
        </div>
        {recent.length > 0 ? <ul className="recent-grid">
          {recent.map(article => <ArticleCard key={article.id} article={article}
            onRead={onRead} onDelete={onDelete} onRegenerate={onRegenerate} onStateChange={onStateChange} onEdit={onEdit} onExportMarkdown={onExportMarkdown} />)}
        </ul> : library && !error && <div className="shelf-state home-empty" role="status">
          <BookOpen size={22} strokeWidth={1.5} /><span>{tr("还没有文章，粘贴链接开始阅读")}</span>
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
  const searchField = useRef<HTMLInputElement>(null)
  const matching = searchArticles(library?.articles ?? [], search)
  const articles = filterArticles(matching, filters)
  const counts = shelfCounts(matching, filters.starred)
  const filtered = Boolean(search.trim() || filters.read !== "all" || filters.starred)
  const readFilters: { key: ReadFilter; label: string }[] = [
    { key: "all", label: tr("全部") }, { key: "unread", label: tr("未读") }, { key: "completed", label: tr("已读") },
  ]

  function clearSearch() {
    onSearch("")
    searchField.current?.focus()
  }

  function resetFilters() {
    clearSearch()
    onFilters(defaultShelfFilters)
  }

  return (
    <section className="bookshelf-page" aria-labelledby="bookshelf-title">
      <header className="bookshelf-title">
        <h1 id="bookshelf-title">{tr("书架")}</h1>
        <button className="primary" type="button" onClick={onCreate}><Plus size={16} aria-hidden="true" />{tr("新建文章")}</button>
      </header>
      {library && library.articles.length > 0 && <div className="shelf-toolbar">
        <div className="shelf-filters" role="group" aria-label={tr("筛选文章")}>
          <div className="shelf-read-filters" role="group" aria-label={tr("阅读状态")}>
            {readFilters.map(filter => <button key={filter.key} type="button" aria-pressed={filters.read === filter.key}
              onClick={() => onFilters({ ...filters, read: filter.key })}>
              {filter.label}<span>{counts[filter.key]}</span>
            </button>)}
          </div>
          <button className="shelf-star-filter" type="button" aria-pressed={filters.starred}
            onClick={() => onFilters({ ...filters, starred: !filters.starred })}>
            <Star size={14} aria-hidden="true" fill={filters.starred ? "currentColor" : "none"} />{tr("收藏")}<span>{counts.starred}</span>
          </button>
        </div>
        <span className="shelf-search">
          <Search size={16} aria-hidden="true" />
          <input ref={searchField} aria-label={tr("搜索书架")} type="search" value={search} placeholder={tr("搜索标题、作者、分类或来源")}
            onChange={event => onSearch(event.target.value)} />
          {search && <button className="icon-button" type="button" aria-label={tr("清空搜索")} title={tr("清空搜索")} onClick={clearSearch}><X size={15} /></button>}
        </span>
        <span className="sr-only" aria-live="polite">{tr("显示")} {articles.length} {tr("篇文章")}</span>
      </div>}
      {articles.length > 0 ? <ul className="shelf-list">
        {articles.map(article => <ArticleRow key={article.id} article={article} onRead={onRead} onDelete={onDelete} onRegenerate={onRegenerate} onStateChange={onStateChange} onEdit={onEdit} onExportMarkdown={onExportMarkdown} />)}
      </ul> : library && !error && <div className="shelf-state bookshelf-empty" role="status">
        {filtered ? <><Search size={20} aria-hidden="true" /><span>{tr("没有符合条件的文章")}</span>
          <button className="text-button" type="button" onClick={resetFilters}>{tr("清除搜索和筛选")}</button>
        </> : <><BookOpen size={24} strokeWidth={1.5} aria-hidden="true" /><span>{tr("书架还没有文章")}</span>
        </>}
      </div>}
      <LibraryState library={library} error={error} onRefresh={onRefresh} />
    </section>
  )
}
