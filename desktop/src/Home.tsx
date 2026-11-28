import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react"
import {
  ArrowRight, ArrowUpRight, BookOpen, Captions, Check, CircleAlert, CircleHelp,
  CirclePause, FileUp, Link2, LoaderCircle, Podcast, RotateCw, X,
} from "lucide-react"
import type { Article } from "./api"
import {
  ArticleActions, ArticleCard, Bookshelf, LibraryState, type ArticleListProps,
} from "./BookshelfSection"
import type { ContinueReading } from "./continueReading"
import { selectRecentArticles } from "./homePresentation"
import { Cover, JobRow, SourceDetails, type Job, type TaskJob } from "./JobCard"
import { tr } from "./language.ts"
import { previewKey } from "./previewState"
import type { SettingsTarget } from "./Settings"
import type { ComposerPaste } from "./subtitlePaste"
import type { LinkPreview } from "./useLinkPreview"
import "./home.css"

export { Bookshelf, JobRow, type Job, type TaskJob }

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
  reviewedTaskIds?: readonly string[]
  onDismissTaskFailure?: (id: string) => void
  onCancel: (id: string) => void
  onRetry: (id: string) => void
  onRemove: (id: string) => Promise<void> | void
  onPreview?: (id: string) => void
  journalLoading?: boolean
  journalError?: string
  onRetryJournal?: () => void
  toolWarning?: string
  preferencesReady?: boolean
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
  focusComposerNonce?: number
}

function fileName(path: string) {
  return path.split("/").at(-1) || path
}

export function Home({
  source, setSource, localPath, onPick, onClear, onStart, busy, dragging, jobs,
  reviewedTaskIds = [], onDismissTaskFailure,
  onCancel, onRetry, onRemove, linkPreview,
  onPaste, nativePaste, pasting,
  toolWarning, preferencesReady = true, inputError, onSettings, onLibrary, library, error,
  onRead, onRefresh, onDelete, onRegenerate, onStateChange,
  onPreview, journalLoading, journalError, onRetryJournal,
  onTasks, taskSummary, resume, sourceReadiness, sourceBlocked, sourceNeedsAsr, sourceNeedsYoutubeCookies,
  youtubeCookiesConfigured = false, asrEnabled = false,
  existingArticle, existingTask, onViewExistingTask, onEdit, onExportMarkdown, focusComposerNonce = 0,
}: HomeProps) {
  // Keep newly completed articles visible while the library refreshes.
  const finished = jobs.flatMap(task => task.job.status === "done" ? [task.job.article] : []).reverse()
  const recent = selectRecentArticles(finished, library?.articles, resume?.article.id)
  const activeJobs = jobs.filter(task => task.job.status === "running" || task.job.status === "cancelling").slice(0, 1)
  const reviewedSet = new Set(reviewedTaskIds)
  const latestUnreviewedError = !activeJobs.length
    ? [...jobs].reverse().find(task => task.job.status === "error" && !reviewedSet.has(task.id))
    : undefined
  const currentJobs = activeJobs.length ? activeJobs : (latestUnreviewedError ? [latestUnreviewedError] : [])
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
  const totalArticles = (library?.articles.length ?? 0) + finished.filter(item => !library?.articles.some(existingItem => existingItem.id === item.id)).length
  const showSetupBanner = preferencesReady && !existing && Boolean(toolWarning) && !inputError && !source.trim() && !localPath

  function showImportHint() {
    setHint(current => current === "pinned-help" ? current : "import")
  }

  function hideImportHint() {
    setHint(current => current === "import" ? undefined : current)
  }

  useEffect(() => {
    if (!focusComposerNonce) return
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [focusComposerNonce])

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
        {!existingArticle && !selectedFile && (linkPreview.status !== "idle" || (!existing && Boolean(sourceReadiness))) && (
          <div className="source-card" aria-label={tr("来源预览与状态")}>
            {linkPreview.status === "loading" && <div className="source-preview preview-loading" role="status" aria-label={tr("正在识别链接")}>
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
            {linkPreview.status === "ready" && <div className="source-preview" aria-label={tr("来源预览")}>
              <Cover {...linkPreview.metadata} />
              <div className="source-preview-info">
                <strong>{linkPreview.metadata.title}</strong>
                <SourceDetails {...linkPreview.metadata} />
              </div>
            </div>}
            {linkPreview.status === "error" && <div className="preview-unavailable" role="status">
              <span>{linkPreview.timedOut ? tr("来源预览等待超时") : tr("暂未获取到标题与封面")}</span>
              <button className="text-button" type="button" onClick={linkPreview.retry}><RotateCw size={12} aria-hidden="true" />{tr("重试预览")}</button>
            </div>}
            {!existing && sourceReadiness}
          </div>
        )}
        {inputError && <p className="home-tool-warning" role="alert"><CircleAlert size={15} /><span>{inputError}</span></p>}
        {showSetupBanner && <div className="home-setup-banner" role="status">
          <CircleAlert size={15} aria-hidden="true" />
          <span>{toolWarning} · {tr("完成 AI 工具配置后即可开始生成文章")}</span>
          <button className="secondary" type="button" onClick={() => onSettings("ai")}>{tr("前往设置")} <ArrowUpRight size={13} /></button>
        </div>}
        {!showSetupBanner && preferencesReady && !existing && toolWarning && !inputError && <div className="home-tool-warning" role="status">
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
        {latestUnreviewedError && !activeJobs.length && onDismissTaskFailure && (
          <div className="home-inline-failure-bar" role="status">
            <span>{tr("上次生成未能完成")}</span>
            <button type="button" className="text-button" onClick={() => onDismissTaskFailure(latestUnreviewedError.id)}>
              {tr("收起")}
            </button>
          </div>
        )}
        <ul className="task-list">
          {currentJobs.map(task => <JobRow key={task.id} taskId={task.id} job={task.job}
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
        </ul> : library && !error && totalArticles === 0 && !resume && <div className="shelf-state home-empty" role="status">
          <BookOpen size={22} strokeWidth={1.5} /><span>{tr("还没有文章，粘贴链接开始阅读")}</span>
        </div>}
        <LibraryState library={library} error={error} onRefresh={onRefresh} />
      </section>
    </div>
  )
}
