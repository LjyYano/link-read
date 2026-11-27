import { tr } from "./language.ts"
import { useEffect, useId, useRef, useState, type FormEvent } from "react"
import { LoaderCircle, X } from "lucide-react"
import { getArticleDetails, updateArticleDetails, type Article, type ArticleDetails } from "./api"
import { useArticleDialog } from "./articleDialog"
import { localCoverSource, thumbnailSource } from "./coverSource"
import { useLinkPreview } from "./useLinkPreview"
import "./article-dialog.css"

export function ArticleDetailsDialog({ article, onSaved, onClose }: {
  article: Article; onSaved: (article: Article) => void; onClose: () => void
}) {
  const id = useId(), dialog = useArticleDialog()
  const [details, setDetails] = useState<ArticleDetails>()
  const [sourceUrl, setSourceUrl] = useState("")
  const [title, setTitle] = useState("")
  const [category, setCategory] = useState("")
  const [resetTitle, setResetTitle] = useState(false)
  const [resetCategory, setResetCategory] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [brokenCover, setBrokenCover] = useState<string>()
  const mounted = useRef(true)
  const titleInput = useRef<HTMLInputElement>(null)
  const link = sourceUrl.trim()
  const savedLink = details?.linkedSourceUrl ?? ""
  const changedLink = article.platform === "local" && details && link !== savedLink ? link : undefined
  const preview = useLinkPreview(changedLink ?? "", false)
  const newSource = preview.status === "ready" ? preview.metadata : undefined
  const sourceTitle = Array.from(newSource?.title.trim().replace(/\s+/g, " ") ?? "").slice(0, 300).join("").trimEnd()
  const originalTitle = (changedLink === undefined ? details?.originalTitle
    : sourceTitle || details?.subtitleTitle || details?.originalTitle) ?? ""
  const displayTitle = resetTitle ? originalTitle : title
  const cover = changedLink === undefined
    ? (localCoverSource(article.localCover) ?? thumbnailSource(details?.linkedThumbnailUrl))
    : thumbnailSource(newSource?.thumbnailUrl)

  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    let current = true
    setError("")
    getArticleDetails(article.id).then(value => {
      if (!current) return
      setDetails(value); setTitle(value.title); setCategory(value.category)
      setSourceUrl(value.linkedSourceUrl ?? "")
      setResetTitle(!value.titleOverridden); setResetCategory(!value.categoryOverridden)
      requestAnimationFrame(() => { if (current) titleInput.current?.focus() })
    }, failure => { if (current) setError(String(failure)) })
    return () => { current = false }
  }, [article.id, loadAttempt])

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!details || saving) return
    if (!displayTitle.trim()) { setError(tr("Enter a display title.")); titleInput.current?.focus(); return }
    setSaving(true); setError("")
    try {
      const updated = await updateArticleDetails(article.id, resetTitle ? null : title.trim(),
        resetCategory ? null : category.trim(), changedLink)
      if (mounted.current) { onSaved(updated); onClose() }
    } catch (failure) { if (mounted.current) setError(String(failure)) }
    finally { if (mounted.current) setSaving(false) }
  }

  return <dialog ref={dialog} className="article-dialog" aria-labelledby={id + "-title"}
    onCancel={event => { event.preventDefault(); event.stopPropagation(); if (!saving) onClose() }}
    onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); if (event.repeat || event.nativeEvent.isComposing) event.preventDefault() } }}>
    <form onSubmit={event => void save(event)}>
      <header><h2 id={id + "-title"}>{tr("Edit details")}</h2><button className="icon-button" type="button" aria-label={tr("Close edit details")} disabled={saving} onClick={onClose}><X size={18} /></button></header>
      {!details ? <div className="article-dialog-loading" role="status">
        {error ? <><p className="error-line" role="alert">{error}</p><button className="secondary" type="button" onClick={() => setLoadAttempt(value => value + 1)}>{tr("Try again")}</button></>
          : <><LoaderCircle className="spin" size={19} />{tr("Loading article details…")}</>}
      </div> : <>
        <div className="article-dialog-body">
          {article.platform === "local" && <>
            <label className="article-dialog-field" htmlFor={id + "-source-link"}>{tr("Original video or episode link")}
              <input id={id + "-source-link"} inputMode="url" placeholder="https://" value={sourceUrl}
                maxLength={4096} spellCheck={false} autoCapitalize="none" disabled={saving}
                aria-describedby={id + "-source-link-hint"} onChange={event => setSourceUrl(event.target.value)} />
            </label>
            <div className="article-original" id={id + "-source-link-hint"}>
              <span>{tr("Uses the source cover and title. A custom display title takes priority.")}</span>
            </div>
            {cover && brokenCover !== cover && <img className="article-source-cover" src={cover} alt={tr("来源封面")}
              referrerPolicy="no-referrer" onError={() => setBrokenCover(cover)} />}
            {preview.status === "loading" && <div className="article-source-feedback" role="status">
              <LoaderCircle className="spin" size={13} aria-hidden="true" />
              {tr(preview.slow ? "获取信息较慢，仍在等待标题与封面" : "正在获取标题与封面…")}
            </div>}
            {preview.status === "error" && <div className="article-source-feedback">
              <span role="status">{tr(preview.timedOut ? "来源预览等待超时" : "暂未获取到标题与封面")}</span>
              <button className="text-button" type="button" disabled={saving} onClick={preview.retry}>{tr("重试预览")}</button>
            </div>}
          </>}
          <label className="article-dialog-field" htmlFor={id + "-display-title"}>{tr("Display title")}
            <input ref={titleInput} id={id + "-display-title"} value={displayTitle} maxLength={300} required disabled={saving}
              onChange={event => { setTitle(event.target.value); setResetTitle(false) }} />
          </label>
          <div className="article-original"><span>{tr("Original title:")} {originalTitle}</span>
            <button className="text-button" type="button" disabled={saving || resetTitle} onClick={() => setResetTitle(true)}>{tr("Reset to original")}</button></div>
          <label className="article-dialog-field" htmlFor={id + "-category"}>{tr("Category")}
            <input id={id + "-category"} value={category} maxLength={80} disabled={saving}
              onChange={event => { setCategory(event.target.value); setResetCategory(false) }} />
          </label>
          <div className="article-original"><span>{tr("Original category:")} {details.originalCategory || "None"}</span>
            <button className="text-button" type="button" disabled={saving || resetCategory} onClick={() => { setCategory(details.originalCategory); setResetCategory(true) }}>{tr("Reset to original")}</button></div>
          {error && <p className="error-line" role="alert">{error}</p>}
        </div>
        <footer><button className="secondary" type="button" disabled={saving} onClick={onClose}>{tr("Cancel")}</button>
          <button className="primary" type="submit" disabled={saving || !displayTitle.trim()}>{saving && <LoaderCircle className="spin" size={14} />}{tr("Save changes")}</button></footer>
      </>}
    </form>
  </dialog>
}
