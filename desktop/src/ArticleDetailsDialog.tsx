import { useEffect, useId, useRef, useState, type FormEvent } from "react"
import { LoaderCircle, X } from "lucide-react"
import { getArticleDetails, updateArticleDetails, type Article, type ArticleDetails } from "./api"
import { useArticleDialog } from "./articleDialog"
import "./article-dialog.css"

export function ArticleDetailsDialog({ article, onSaved, onClose }: {
  article: Article; onSaved: (article: Article) => void; onClose: () => void
}) {
  const id = useId(), dialog = useArticleDialog()
  const [details, setDetails] = useState<ArticleDetails>()
  const [title, setTitle] = useState("")
  const [category, setCategory] = useState("")
  const [resetTitle, setResetTitle] = useState(false)
  const [resetCategory, setResetCategory] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")
  const [loadAttempt, setLoadAttempt] = useState(0)
  const mounted = useRef(true)
  const titleInput = useRef<HTMLInputElement>(null)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    let current = true
    setError("")
    getArticleDetails(article.id).then(value => {
      if (!current) return
      setDetails(value); setTitle(value.title); setCategory(value.category)
      setResetTitle(!value.titleOverridden); setResetCategory(!value.categoryOverridden)
      requestAnimationFrame(() => { if (current) titleInput.current?.focus() })
    }, failure => { if (current) setError(String(failure)) })
    return () => { current = false }
  }, [article.id, loadAttempt])

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!details || saving) return
    if (!title.trim()) { setError("Enter a display title."); titleInput.current?.focus(); return }
    setSaving(true); setError("")
    try {
      const updated = await updateArticleDetails(article.id, resetTitle ? null : title.trim(), resetCategory ? null : category.trim())
      if (mounted.current) { onSaved(updated); onClose() }
    } catch (failure) { if (mounted.current) setError(String(failure)) }
    finally { if (mounted.current) setSaving(false) }
  }

  return <dialog ref={dialog} className="article-dialog" aria-labelledby={id + "-title"}
    onCancel={event => { event.preventDefault(); event.stopPropagation(); if (!saving) onClose() }}
    onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); if (event.repeat || event.nativeEvent.isComposing) event.preventDefault() } }}>
    <form onSubmit={event => void save(event)}>
      <header><h2 id={id + "-title"}>Edit details</h2><button className="icon-button" type="button" aria-label="Close edit details" disabled={saving} onClick={onClose}><X size={18} /></button></header>
      {!details ? <div className="article-dialog-loading" role="status">
        {error ? <><p className="error-line" role="alert">{error}</p><button className="secondary" type="button" onClick={() => setLoadAttempt(value => value + 1)}>Try again</button></>
          : <><LoaderCircle className="spin" size={19} />Loading article details…</>}
      </div> : <>
        <div className="article-dialog-body">
          <label className="article-dialog-field" htmlFor={id + "-display-title"}>Display title
            <input ref={titleInput} id={id + "-display-title"} value={title} maxLength={300} required disabled={saving}
              onChange={event => { setTitle(event.target.value); setResetTitle(false) }} />
          </label>
          <div className="article-original"><span>Original title: {details.originalTitle}</span>
            <button className="text-button" type="button" disabled={saving || resetTitle} onClick={() => { setTitle(details.originalTitle); setResetTitle(true) }}>Reset to original</button></div>
          <label className="article-dialog-field" htmlFor={id + "-category"}>Category
            <input id={id + "-category"} value={category} maxLength={80} disabled={saving}
              onChange={event => { setCategory(event.target.value); setResetCategory(false) }} />
          </label>
          <div className="article-original"><span>Original category: {details.originalCategory || "None"}</span>
            <button className="text-button" type="button" disabled={saving || resetCategory} onClick={() => { setCategory(details.originalCategory); setResetCategory(true) }}>Reset to original</button></div>
          {error && <p className="error-line" role="alert">{error}</p>}
        </div>
        <footer><button className="secondary" type="button" disabled={saving} onClick={onClose}>Cancel</button>
          <button className="primary" type="submit" disabled={saving || !title.trim()}>{saving && <LoaderCircle className="spin" size={14} />}Save changes</button></footer>
      </>}
    </form>
  </dialog>
}
