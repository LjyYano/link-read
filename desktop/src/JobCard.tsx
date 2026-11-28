import { useEffect, useRef, useState } from "react"
import {
  ArrowRight, BookOpen, Captions, Check, CircleAlert, CirclePause, CircleStop,
  LoaderCircle, Podcast, Trash2, X,
} from "lucide-react"
import bilibiliIcon from "./assets/bilibili.png"
import { openVideo, type Article, type GenerationFailure } from "./api"
import { ConfirmDialog } from "./ConfirmDialog"
import { localCoverSource, thumbnailSource } from "./coverSource"
import { coverText } from "./coverText"
import { sourcePresentation, updatedDatePresentation } from "./homePresentation"
import { localizedDetail, tr } from "./language.ts"
import { previewKey } from "./previewState"
import type { SettingsTarget } from "./Settings"
import { activeLanes, elapsedLabel, laneLabelFor, progressCaption, progressDetail, requestCaption, stageRows, type TaskActivity } from "./taskProgress"
import { useLinkPreview } from "./useLinkPreview"
import { isYoutubeCookieErrorCode } from "./youtubeGuidance"

export type ArticleIdentity = {
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

const platformLabel = { youtube: "YouTube", bilibili: "B 站", apple_podcasts: "Apple 播客", local: "本地字幕" } as const
const stages = ["提取字幕", "AI 提炼", "校对与整理", "生成文章"]

export function PlatformIcon({ platform }: { platform?: Article["platform"] }) {
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

export function Cover({ platform, thumbnailUrl, localCover, title, sourceUrl, withBackdrop }: ArticleIdentity & { withBackdrop?: boolean }) {
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
    visible && (platform === "bilibili" || platform === "apple_podcasts") && !local && !provided ? sourceUrl || "" : "",
    false,
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

export function SourceDetails({ platform, author, durationMs }: ArticleIdentity) {
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

export function ArticleUpdatedAt({ value }: { value: number }) {
  const date = updatedDatePresentation(value)
  return date && <time className="shelf-date" dateTime={date.dateTime} title={date.title} aria-label={date.title}>
    {date.label}
  </time>
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
  const [confirmRemoveOpen, setConfirmRemoveOpen] = useState(false)
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
  const firstErrorLine = jobMessage.split("\n")[0] ?? ""
  const hasExtraErrorDetail = job.status === "error" && jobMessage.trim() !== firstErrorLine.trim()
  const showEventStageTags = Boolean(activity && Object.keys(activity.lanes).length > 1)
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
    if (recoveryAction === "asr" || (recoveryAction === "setup" && job.status === "error" && job.failure?.stage === "asr")) {
      onSettings("asr"); return
    }
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
  async function confirmRemove() {
    setRemoving(true)
    setActionError("")
    try {
      await onRemove()
      setConfirmRemoveOpen(false)
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
            <button className="icon-button" type="button" disabled={removing} title={tr("移除任务记录")} aria-label={tr("移除任务记录")}
              onClick={() => { if (!removing) setConfirmRemoveOpen(true) }}>
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
        {job.status === "error" && <p className="task-error-summary">{firstErrorLine}</p>}
        {actionError && <p className="task-error-summary" role="alert">{actionError}</p>}
        {job.previewId && onPreview && <button className="text-button task-read-transcript" type="button" onClick={onPreview}>
          <BookOpen size={14} aria-hidden="true" />{tr("先读原文")}<ArrowRight size={13} aria-hidden="true" />
        </button>}
        {(activity || hasExtraErrorDetail) && <details className="task-details" ref={details}>
          <summary>{job.status === "error" ? tr("查看错误详情") : tr("查看处理详情")}</summary>
          {hasExtraErrorDetail && <pre className="task-error-detail">{jobMessage}</pre>}
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
            {activity.history.length > 0 && <ol className="task-event-list" aria-label={tr("最近处理记录")}>
              {activity.history.map((line, index) => {
                const stageTag = showEventStageTags ? laneLabelFor(line.stage) : undefined
                return <li key={`${line.at}-${index}`}>
                  <time>{elapsedLabel(line.at - activity.startedAt)}</time>
                  {stageTag && <span className="task-event-stage">{stageTag}</span>}
                  <span>{localizedDetail(line.message, tr("正在处理"))}</span>
                </li>
              })}
            </ol>}
          </>}
        </details>}
      </div>
      {confirmRemoveOpen && (
        <ConfirmDialog
          title={tr("移除任务记录")}
          message={tr("确认移除任务记录「{0}」？\n仅移除此任务记录，已保存的字幕与可复用的阶段缓存会保留。", title)}
          confirmLabel={tr("移除任务记录")}
          danger
          onConfirm={confirmRemove}
          onCancel={() => setConfirmRemoveOpen(false)}
        />
      )}
    </li>
  )
}
