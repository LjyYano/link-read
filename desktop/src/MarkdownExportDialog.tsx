import { useId, useRef, useState, type FormEvent } from "react"
import { FileText, LoaderCircle, X } from "lucide-react"
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
      <header><div><h2 id={id + "-title"}>Export Markdown</h2><p className="article-dialog-subtitle">{article.title}</p></div>
        <button className="icon-button" type="button" aria-label="Close Markdown export" disabled={saving} onClick={onClose}><X size={18} /></button></header>
      <div className="article-dialog-body">
        <fieldset disabled={saving} className="export-content-options"><legend>Content</legend>
          <label><input type="radio" name={id + "-content"} value="readable" checked={content === "readable"} onChange={() => setContent("readable")} />
            <span><strong>Readable article</strong><small>Summary and clean transcript</small></span></label>
          <label><input type="radio" name={id + "-content"} value="full" checked={content === "full"} onChange={() => setContent("full")} />
            <span><strong>Full note</strong><small>Also include saved takeaways, chapter guide, highlights, keywords and Shownotes</small></span></label>
        </fieldset>
        <label className="export-transcript-option"><input type="checkbox" checked={includeTimestamped} disabled={saving} onChange={event => setIncludeTimestamped(event.target.checked)} />
          <span>Include timestamped transcript<small>Add a separate subtitle appendix</small></span></label>
        <div className="export-contents-preview" aria-label="Export contents" aria-live="polite"><FileText size={17} /><span>
          Title &amp; source{article.hasAnalysis !== false ? " · Summary" : ""} · Transcript
          {content === "full" && <small>Additional saved sections, when available. The interactive mindmap is included in HTML exports.</small>}
          {includeTimestamped && <small>Timestamped subtitle appendix</small>}
        </span></div>
        <p className="article-dialog-hint">Uses saved content. Source attribution is included.</p>
        {error && <p className="error-line" role="alert">{error}</p>}
      </div>
      <footer><button className="secondary" type="button" disabled={saving} onClick={onClose}>Cancel</button>
        <button className="primary" type="submit" disabled={saving}>{saving && <LoaderCircle className="spin" size={14} />}Export Markdown</button></footer>
    </form>
  </dialog>
}
