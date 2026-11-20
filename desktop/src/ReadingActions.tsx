import { useRef, useState } from "react"
import { Circle, CircleCheck, Star, X } from "lucide-react"
import type { Article, ArticleStatePatch } from "./api"
import { articleState } from "./libraryState"
import "./reading-actions.css"

export type ChangeArticleState = (id: string, patch: ArticleStatePatch) => Promise<void>

const labels = {
  en: { unread: "Mark Unread", read: "Mark Complete", unstar: "Unstar", star: "Star", starred: "Starred",
    saveError: "Could not save.", dismissError: "Dismiss error" },
  zh: { unread: "标为未读", read: "标为已读", unstar: "取消收藏", star: "收藏", starred: "已收藏",
    saveError: "保存失败。", dismissError: "关闭错误提示" },
} as const

export function ReadingActions({ article, onChange, compact = false, className = "", locale = "en" }: {
  article: Article; onChange: ChangeArticleState; compact?: boolean; className?: string; locale?: keyof typeof labels
}) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState("")
  const inFlight = useRef(false)
  const state = articleState(article)
  const copy = labels[locale]

  async function save(patch: ArticleStatePatch) {
    if (inFlight.current) return
    inFlight.current = true
    setPending(true)
    setError("")
    try {
      await onChange(article.id, patch)
    } catch (failure) {
      setError(String(failure))
    } finally {
      inFlight.current = false
      setPending(false)
    }
  }

  return <div className={`reading-actions${compact ? " reading-actions-compact" : ""} ${className}`}
    role="group" aria-label={locale === "zh" ? `阅读状态：${article.title}` : `Reading status: ${article.title}`} aria-busy={pending}>
    <button type="button" className="reading-state-button reading-complete-button" disabled={pending}
      aria-pressed={state.isRead} aria-label={state.isRead ? copy.unread : copy.read}
      title={state.isRead ? copy.unread : copy.read} onClick={() => void save({ isRead: !state.isRead })}>
      {state.isRead ? <CircleCheck className="reading-complete-icon" size={17} aria-hidden="true" /> : <Circle size={17} aria-hidden="true" />}
    </button>
    <button type="button" className="reading-state-button reading-star-button" disabled={pending}
      aria-pressed={state.isStarred} aria-label={state.isStarred ? copy.unstar : copy.star}
      title={state.isStarred ? copy.unstar : copy.star} onClick={() => void save({ isStarred: !state.isStarred })}>
      <Star size={17} fill={state.isStarred ? "currentColor" : "none"} aria-hidden="true" />
      <span>{state.isStarred ? copy.starred : copy.star}</span>
    </button>
    {error && <div className="reading-state-error" role="alert">
      <span>{copy.saveError} {error}</span>
      <button type="button" className="icon-button" aria-label={copy.dismissError} onClick={() => setError("")}><X size={13} /></button>
    </div>}
  </div>
}
