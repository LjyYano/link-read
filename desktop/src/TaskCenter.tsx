import { tr } from "./language.ts"
import { useEffect, useId, useMemo, useRef, useState } from "react"
import { ChevronDown, CircleCheck, Clock3, ListTodo, Trash2, X } from "lucide-react"
import { JobRow, type TaskJob } from "./Home"
import { groupTasks, reviewedTerminalTaskIds, unreviewedTaskIds } from "./taskCenterState"
import type { Article } from "./api"
import type { SettingsTarget } from "./Settings"
import "./task-center.css"

export type TaskCenterProps = {
  jobs: TaskJob[]
  completed: Article[]
  initialTaskId?: string
  initialCompletedArticleId?: string
  reviewedIds: readonly string[]
  onReviewed: (ids: string[]) => void
  onClose: () => void
  onCancel: (id: string) => void
  onRetry: (id: string) => void
  onRemove: (id: string) => Promise<void> | void
  onPreview?: (id: string) => void
  onOpenArticle: (article: Article) => void
  onSettings: (target?: SettingsTarget) => void
  onClearReviewed: (ids: string[]) => Promise<void> | void
  journalError?: string
  onRetryJournal?: () => void
  youtubeCookiesConfigured?: boolean
  asrEnabled?: boolean
}

export function TaskCenter({ jobs, completed, initialTaskId, initialCompletedArticleId, reviewedIds, onReviewed, onClose, onCancel, onRetry, onRemove,
  onPreview, onOpenArticle, onSettings, onClearReviewed, journalError, onRetryJournal,
  youtubeCookiesConfigured = false, asrEnabled = false }: TaskCenterProps) {
  const id = useId()
  const dialog = useRef<HTMLDialogElement>(null)
  const closeButton = useRef<HTMLButtonElement>(null)
  const scrollArea = useRef<HTMLDivElement>(null)
  const attentionList = useRef<HTMLUListElement>(null)
  const groups = useMemo(() => groupTasks(jobs), [jobs])
  const unseen = useMemo(() => unreviewedTaskIds(jobs, reviewedIds), [jobs, reviewedIds])
  const removable = useMemo(() => reviewedTerminalTaskIds(jobs, reviewedIds), [jobs, reviewedIds])
  const [attentionOpen, setAttentionOpen] = useState(unseen.length > 0 || groups.attention.some(task => task.id === initialTaskId))
  const [completedOpen, setCompletedOpen] = useState(Boolean(initialCompletedArticleId))
  const [clearing, setClearing] = useState(false)
  const [clearError, setClearError] = useState("")
  const announced = useRef(new Set(unseen))
  const acknowledge = useRef(onReviewed)
  acknowledge.current = onReviewed

  useEffect(() => {
    const previous = document.activeElement
    const element = dialog.current
    element?.showModal()
    closeButton.current?.focus({ preventScroll: true })
    return () => {
      element?.close()
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus({ preventScroll: true })
    }
  }, [])

  useEffect(() => {
    if (!initialTaskId) return
    const frame = requestAnimationFrame(() => {
      const row = Array.from(dialog.current?.querySelectorAll<HTMLElement>("[data-task-id]") ?? [])
        .find(element => element.dataset.taskId === initialTaskId)
      if (!row) return
      row.tabIndex = -1
      row.focus({ preventScroll: true })
      row.scrollIntoView({ block: "center", behavior: "auto" })
    })
    return () => cancelAnimationFrame(frame)
  }, [initialTaskId])

  useEffect(() => {
    if (!initialCompletedArticleId) return
    const button = Array.from(dialog.current?.querySelectorAll<HTMLButtonElement>("[data-completed-article]") ?? [])
      .find(element => element.dataset.completedArticle === initialCompletedArticleId)
    button?.focus()
  }, [initialCompletedArticleId])

  useEffect(() => {
    // Reveal new failures arriving while the drawer is already open. A user's
    // manual collapse otherwise remains stable across progress ticks.
    if (unseen.some(taskId => !announced.current.has(taskId))) setAttentionOpen(true)
    announced.current = new Set(unseen)
  }, [unseen])

  useEffect(() => {
    const root = scrollArea.current, list = attentionList.current
    if (!attentionOpen || !root || !list) return
    const reviewed = new Set(reviewedIds)
    const records = new Map<Element, string>()
    Array.from(list.children).forEach((element, index) => {
      const taskId = groups.attention[index]?.id
      // A visible cover/title alone does not mean the failure was seen. Observe
      // the actual reason, or the stopped/cancelled status when no reason exists.
      const explanation = element.querySelector(".task-error-summary, .task-detail-line")
        ?? element.querySelector(".task-status-line")
      if (taskId && explanation && !reviewed.has(taskId)) records.set(explanation, taskId)
    })
    if (!records.size) return
    const mark = (elements: Element[]) => {
      const ids = elements.flatMap(element => {
        const taskId = records.get(element)
        if (!taskId) return []
        records.delete(element)
        return [taskId]
      })
      if (ids.length) acknowledge.current(ids)
    }
    // Merely opening the drawer must not mark off-screen failures as seen.
    if (typeof IntersectionObserver !== "undefined") {
      const observer = new IntersectionObserver(entries => {
        const visible = entries.filter(entry => entry.isIntersecting && entry.intersectionRatio >= 0.6)
        mark(visible.map(entry => entry.target))
        visible.forEach(entry => observer.unobserve(entry.target))
      }, { root, threshold: [0, 0.6, 1] })
      records.forEach((_taskId, element) => observer.observe(element))
      return () => observer.disconnect()
    }
    const check = () => {
      const bounds = root.getBoundingClientRect()
      mark([...records.keys()].filter(element => {
        const rect = element.getBoundingClientRect()
        const height = Math.max(0, Math.min(rect.bottom, bounds.bottom) - Math.max(rect.top, bounds.top))
        const width = Math.max(0, Math.min(rect.right, bounds.right) - Math.max(rect.left, bounds.left))
        return rect.width * rect.height > 0 && height * width / (rect.width * rect.height) >= 0.6
      }))
    }
    check()
    root.addEventListener("scroll", check, { passive: true })
    return () => root.removeEventListener("scroll", check)
  }, [attentionOpen, groups.attention, reviewedIds])

  async function clearReviewed() {
    if (clearing || !removable.length) return
    const selected = [...removable]
    if (!window.confirm(tr("清理 {0} 条已查看的任务记录？\n仅移除这些已结束的记录，已保存的字幕和处理缓存不会删除。", selected.length))) return
    setClearing(true)
    setClearError("")
    try { await onClearReviewed(selected) } catch (error) { setClearError(String(error)) }
    finally { setClearing(false) }
  }

  function record(task: TaskJob) {
    return <JobRow key={task.id} taskId={task.id} job={task.job} onCancel={() => onCancel(task.id)}
      onRetry={() => onRetry(task.id)} onRemove={() => onRemove(task.id)}
      onPreview={onPreview ? () => onPreview(task.id) : undefined} onSettings={onSettings}
      youtubeCookiesConfigured={youtubeCookiesConfigured} asrEnabled={asrEnabled} />
  }

  return <dialog ref={dialog} className="task-center" aria-labelledby={`${id}-title`}
    aria-describedby={`${id}-description`}
    onCancel={event => { event.preventDefault(); event.stopPropagation(); onClose() }}
    onKeyDown={event => {
      if (event.nativeEvent.isComposing || event.keyCode === 229) return
      if (event.key === "Tab" && !event.altKey && !event.ctrlKey && !event.metaKey) {
        const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex="0"]',
        )).filter(element => element.getClientRects().length > 0 && !element.closest('[hidden]'))
        const first = controls[0], last = controls[controls.length - 1]
        if (first && last && (event.shiftKey ? document.activeElement === first : document.activeElement === last)) {
          event.preventDefault()
          ;(event.shiftKey ? last : first).focus({ preventScroll: true })
        }
      }
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose() }
    }}
    onClick={event => {
      if (event.target !== event.currentTarget) return
      const bounds = event.currentTarget.getBoundingClientRect()
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onClose()
    }}>
    <header className="task-center-header">
      <h2 id={`${id}-title`}><ListTodo size={19} aria-hidden="true" />{tr("任务")}</h2>
      <button ref={closeButton} className="icon-button" type="button" aria-label={tr("关闭任务中心")} title={tr("关闭 (Esc)")} onClick={onClose}>
        <X size={18} aria-hidden="true" />
      </button>
    </header>
    <p id={`${id}-description`} className="sr-only">{tr("查看处理进度、排队与待处理记录。任务在后台继续运行。")}</p>
    <div ref={scrollArea} className="task-center-body">
      {journalError && <div className="task-center-error" role="alert">
        <p>{journalError}</p>
        {onRetryJournal && <button type="button" className="text-button" onClick={onRetryJournal}>{tr("重试任务记录")}</button>}
      </div>}
      {groups.active.length > 0 && <section className="task-center-section" aria-labelledby={`${id}-active`}>
        <h3 id={`${id}-active`}>{tr("处理中")}<span>{groups.active.length}</span></h3>
        <ul className="task-list">{groups.active.map(record)}</ul>
      </section>}
      {groups.queued.length > 0 && <section className="task-center-section" aria-labelledby={`${id}-queued`}>
        <h3 id={`${id}-queued`}>{tr("等待中")}<span>{groups.queued.length}</span></h3>
        <ol className="task-center-queue">
          {groups.queued.map((task, index) => <li key={task.id} data-task-id={task.id}>
            <span className="task-center-queue-icon"><Clock3 size={17} aria-hidden="true" /></span>
            <span className="task-center-queue-copy"><strong title={task.job.status !== "done" ? task.job.title : undefined}>
              {task.job.status !== "done" ? task.job.title || tr("新文章") : tr("新文章")}
            </strong><small>{index + groups.active.length ? tr("前方 {0} 个任务", index + groups.active.length) : tr("等待启动")}</small></span>
            <button className="icon-button" type="button" title={tr("取消排队")} aria-label={tr("取消排队")} onClick={() => onCancel(task.id)}>
              <X size={16} aria-hidden="true" />
            </button>
          </li>)}
        </ol>
      </section>}
      {groups.attention.length > 0 && <section className="task-center-section task-center-attention" aria-labelledby={`${id}-attention`}>
        <h3 id={`${id}-attention`}>
          <button type="button" className="task-center-disclosure" aria-expanded={attentionOpen} aria-controls={`${id}-attention-list`}
            onClick={() => setAttentionOpen(open => !open)}>
            <span>{tr("需处理")}<span className="task-center-count">{groups.attention.length}</span></span>
            <span>{unseen.length > 0 && <small className="task-center-unseen">{unseen.length} {tr("条未查看")}</small>}
              <ChevronDown size={16} aria-hidden="true" /></span>
          </button>
        </h3>
        <ul ref={attentionList} id={`${id}-attention-list`} className="task-list" hidden={!attentionOpen}>
          {groups.attention.map(record)}
        </ul>
      </section>}
      {completed.length > 0 && <section className="task-center-section task-center-completed" aria-labelledby={`${id}-completed`}>
        <h3 id={`${id}-completed`}>
          <button className="task-center-disclosure" type="button" aria-expanded={completedOpen}
            aria-controls={`${id}-completed-list`} onClick={() => setCompletedOpen(open => !open)}>
            <span>{tr("最近完成")}<span className="task-center-count">{completed.length}</span></span>
            <ChevronDown size={16} aria-hidden="true" />
          </button>
        </h3>
        <ul id={`${id}-completed-list`} className="task-center-queue" hidden={!completedOpen}>
          {completed.map(article => <li key={article.id}>
            <span className="task-center-queue-icon"><CircleCheck size={16} aria-hidden="true" /></span>
            <span className="task-center-queue-copy"><strong title={article.title}>{article.title}</strong></span>
            <button className="text-button" type="button" aria-label={tr("打开文章《{0}》", article.title)}
              data-completed-article={article.id}
              onClick={() => onOpenArticle(article)}>{tr("打开文章")}</button>
          </li>)}
        </ul>
      </section>}
      {!groups.active.length && !groups.queued.length && !groups.attention.length && !completed.length && <div className="task-center-empty" role="status">
        <CircleCheck size={26} strokeWidth={1.5} aria-hidden="true" />
        <strong>{tr("没有待处理任务")}</strong>
      </div>}
    </div>
    <footer className="task-center-footer">
      {clearError && <p className="task-center-error" role="alert">{clearError}</p>}
      <button className="text-button" type="button" disabled={clearing || !removable.length}
        onClick={() => void clearReviewed()} title={tr("仅清理已查看且已结束的任务记录，保留字幕与缓存")}>
        <Trash2 size={14} aria-hidden="true" />{clearing ? tr("正在清理…") : tr("清理已查看记录")}
        {removable.length > 0 && <span>({removable.length})</span>}
      </button>
    </footer>
  </dialog>
}
