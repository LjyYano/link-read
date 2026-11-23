import { tr } from "./language.ts"
import { useId, useRef, useState, type FormEvent } from "react"
import { LoaderCircle, X } from "lucide-react"
import { exportArticle, type Article, type MarkdownExportOptions } from "./api"
import { useArticleDialog } from "./articleDialog"
import "./article-dialog.css"

export function MarkdownExportDialog({ article, onClose }: { article: Article; onClose: () => void }) {
  const id = useId(), dialog = useArticleDialog()
  const [content, setContent] = useState<MarkdownExportOptions["content"]>("readable")
  const [includeTimestamped, setIncludeTimestamped] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")
  const submitting = useRef(false)
  async function save(event: FormEvent) {
    event.preventDefault()
    if (submitting.current) return
    submitting.current = true; setSaving(true); setError("")
    try {
      const saved = await exportArticle(article.id, "markdown", { content, includeTimestamped })
      // Cancelling the system save dialog leaves these options available.
      if (saved) onClose()
    } catch (failure) { setError(String(failure)) }
    finally { submitting.current = false; setSaving(false) }
  }
  return <dialog ref={dialog} className="article-dialog markdown-export-dialog" aria-labelledby={id + "-title"}
    onCancel={event => { event.preventDefault(); event.stopPropagation(); if (!saving) onClose() }}
    onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); if (event.repeat || event.nativeEvent.isComposing) event.preventDefault() } }}>
    <form onSubmit={event => void save(event)}>
      <header><div><h2 id={id + "-title"}>{tr("Export Markdown")}</h2><p className="article-dialog-subtitle">{article.title}</p></div>
        <button className="icon-button" type="button" aria-label={tr("Close Markdown export")} disabled={saving} onClick={onClose}><X size={18} /></button></header>
      <div className="article-dialog-body">
        <fieldset disabled={saving} className="export-content-options"><legend>{tr("Content")}</legend>
          <label><input type="radio" name={id + "-content"} value="readable" checked={content === "readable"} onChange={() => setContent("readable")} />
            <span><strong>{tr("Readable article")}</strong><small>{tr("Summary and clean transcript")}</small></span></label>
          <label><input type="radio" name={id + "-content"} value="full" checked={content === "full"} onChange={() => setContent("full")} />
            <span><strong>{tr("Full note")}</strong><small>{tr("Also include saved takeaways, chapter guide, highlights, keywords and Shownotes")}</small></span></label>
        </fieldset>
        <label className="export-transcript-option"><input type="checkbox" checked={includeTimestamped} disabled={saving} onChange={event => setIncludeTimestamped(event.target.checked)} />
          <span>{tr("Include timestamped transcript")}</span></label>
        {error && <p className="error-line" role="alert">{error}</p>}
      </div>
      <footer><button className="secondary" type="button" disabled={saving} onClick={onClose}>{tr("Cancel")}</button>
        <button className="primary" type="submit" disabled={saving}>{saving && <LoaderCircle className="spin" size={14} />}{tr("Export Markdown")}</button></footer>
    </form>
  </dialog>
}
