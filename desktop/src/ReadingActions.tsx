import { useRef, useState } from "react"
import { Circle, CircleCheck, Star, X } from "lucide-react"
import type { Article, ArticleStatePatch } from "./api"
import { articleState } from "./libraryState"
import "./reading-actions.css"

export type ChangeArticleState = (id: string, patch: ArticleStatePatch) => Promise<void>

const labels = {
  unread: "Mark Unread", read: "Mark Complete", unstar: "Unstar", star: "Star", starred: "Starred",
  saveError: "Could not save.", dismissError: "Dismiss error",
} as const

export function ReadingActions({ article, onChange, compact = false, className = "", nativeTooltips = true }: {
  article: Article; onChange: ChangeArticleState; compact?: boolean; className?: string
  nativeTooltips?: boolean
}) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState("")
  const inFlight = useRef(false)
  const state = articleState(article)
  const completeLabel = state.isRead ? labels.unread : labels.read
  const starLabel = state.isStarred ? labels.unstar : labels.star

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
    role="group" aria-label={`Reading status: ${article.title}`} aria-busy={pending}>
    <button type="button" className="reading-state-button reading-complete-button" disabled={pending}
      aria-pressed={state.isRead} aria-label={completeLabel}
      title={nativeTooltips ? completeLabel : undefined} onClick={() => void save({ isRead: !state.isRead })}>
      {state.isRead ? <CircleCheck className="reading-complete-icon" size={17} aria-hidden="true" /> : <Circle size={17} aria-hidden="true" />}
    </button>
    <button type="button" className="reading-state-button reading-star-button" disabled={pending}
      aria-pressed={state.isStarred} aria-label={starLabel}
      title={nativeTooltips ? starLabel : undefined} onClick={() => void save({ isStarred: !state.isStarred })}>
      <Star size={17} fill={state.isStarred ? "currentColor" : "none"} aria-hidden="true" />
      <span>{state.isStarred ? labels.starred : labels.star}</span>
    </button>
    {error && <div className="reading-state-error" role="alert">
      <span>{labels.saveError} {error}</span>
      <button type="button" className="icon-button" aria-label={labels.dismissError} onClick={() => setError("")}><X size={13} /></button>
    </div>}
  </div>
}
