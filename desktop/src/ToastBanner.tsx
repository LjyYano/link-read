import { CircleAlert, CircleCheck, X } from "lucide-react"
import type { Article } from "./api"
import { tr } from "./language.ts"

export function ToastBanner({
  readyArticle,
  extraCompletedCount,
  notice,
  onOpenArticle,
  onDismissCompleted,
  onDismissNotice,
}: {
  readyArticle?: Article
  extraCompletedCount: number
  notice?: string
  onOpenArticle: (article: Article) => void
  onDismissCompleted: () => void
  onDismissNotice: () => void
}) {
  if (!readyArticle && !notice) return null

  return (
    <div className="toast-stack">
      {notice && (
        <div className="completion-toast completion-toast-error" role="alert">
          <CircleAlert size={18} aria-hidden="true" />
          <div className="completion-toast-content">
            <span>{notice}</span>
          </div>
          <button
            className="icon-button"
            type="button"
            aria-label={tr("关闭提示")}
            onClick={onDismissNotice}
          >
            <X size={15} aria-hidden="true" />
          </button>
        </div>
      )}
      {readyArticle && (
        <div className="completion-toast" role="status" aria-live="polite">
          <CircleCheck size={18} aria-hidden="true" />
          <div className="completion-toast-content">
            <strong>{tr("文章已生成")}</strong>
            <span>
              {readyArticle.title}
              {extraCompletedCount > 0 ? tr(" · 另有 {0} 篇已完成", extraCompletedCount) : ""}
            </span>
          </div>
          <button className="text-button" type="button" onClick={() => onOpenArticle(readyArticle)}>
            {tr("打开文章")}
          </button>
          <button
            className="icon-button"
            type="button"
            aria-label={tr("关闭完成提示")}
            onClick={onDismissCompleted}
          >
            <X size={15} aria-hidden="true" />
          </button>
        </div>
      )}
    </div>
  )
}
