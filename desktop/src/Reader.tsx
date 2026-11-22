import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import { ArrowLeft, ArrowUpRight, CircleAlert, Ellipsis, ExternalLink, FileText, FolderOpen, LoaderCircle, Pencil, Printer, RotateCw, Share, SunMoon, Sparkles, X } from "lucide-react"
import { exportArticle, openArticle, openExternal, openVideo, prepareArticle, preparePreview, refreshShownotes, revealArticle, loadReadingPosition, saveReadingPosition, type Article, type ExportFormat } from "./api"
import { menuNavigation } from "./menu"
import { ReadingActions, type ChangeArticleState } from "./ReadingActions"
import { ReadingPositionBridge } from "./readingPositionBridge"
import { ReaderEscapeBridge, readerShortcutsBlocked } from "./readerEscape"
import type { TaskActivity } from "./taskProgress"
import type { Job } from "./Home"
import "./reader.css"

type Menu = "export" | "more"
type PreparedReader = { status: "loading" } | { status: "ready"; revision: string; readingKey: string; shownotes: boolean }
  | { status: "error"; message: string }
type ShownotesRefresh = { status: "idle" } | { status: "loading" } | { status: "error"; message: string }

export type PreviewTask = { id: string; status: Job["status"]; message?: string; activity?: TaskActivity; article?: Article; onOpenComplete: () => void }

export function Reader({ article, onBack, backLabel = "Library", onRegenerate, onStateChange, preview,
  onNavigationStart, onEdit, onExportMarkdown, detailsRevision = 0, onThemeChange, onViewRegeneration, onOpenUpdated }: {
  article: Article; onBack: () => void; onRegenerate: (article: Article) => Promise<void>; onStateChange: ChangeArticleState
  preview?: PreviewTask
  backLabel?: string
  onThemeChange?: (theme: "light" | "dark") => void
  onViewRegeneration?: () => void
  onOpenUpdated?: () => void
  onNavigationStart?: () => () => boolean
  onEdit?: (article: Article) => void
  onExportMarkdown?: (article: Article) => void
  detailsRevision?: number
}) {
  const frame = useRef<HTMLIFrameElement>(null)
  const actions = useRef<HTMLDivElement>(null)
  const exportButton = useRef<HTMLButtonElement>(null)
  const moreButton = useRef<HTMLButtonElement>(null)
  const [menu, setMenu] = useState<Menu>()
  const [error, setError] = useState("")
  const [exporting, setExporting] = useState(false)
  const [prepared, setPrepared] = useState<PreparedReader>({ status: "loading" })
  const [shownotesRefresh, setShownotesRefresh] = useState<ShownotesRefresh>({ status: "idle" })
  const prepareVersion = useRef(0)
  const preparing = useRef(false)
  const articleReady = useRef(false)
  const positionBridge = useRef<ReadingPositionBridge | undefined>(undefined)
  const escapeBridge = useRef(new ReaderEscapeBridge(message => frame.current?.contentWindow?.postMessage(message, "*")))
  const [positionError, setPositionError] = useState("")
  const isPreview = Boolean(article.previewId)
  const readingKey = prepared.status === "ready" ? prepared.readingKey : null
  const lastDetailsRevision = useRef(detailsRevision)

  useEffect(() => () => escapeBridge.current.cancel(),
    [article.id, article.previewId, prepared.status === "ready" ? prepared.revision : null, menu])

  function closeMenu() {
    setMenu(undefined)
    const trigger = menu === "export" ? exportButton : moreButton
    trigger.current?.focus({ preventScroll: true })
  }

  async function leave(action: () => void) {
    const isCurrent = onNavigationStart?.() ?? (() => true)
    await positionBridge.current?.flush()
    if (isCurrent()) action()
  }

  const prepare = useCallback(async (refreshing = false) => {
    if (preparing.current) return
    preparing.current = true
    const version = ++prepareVersion.current
    const keepArticle = refreshing && articleReady.current
    if (keepArticle) setShownotesRefresh({ status: "loading" })
    else {
      articleReady.current = false
      setPrepared({ status: "loading" })
      setShownotesRefresh({ status: "idle" })
    }
    try {
      if (refreshing) await positionBridge.current?.flush()
      const result = await (article.previewId ? preparePreview(article.previewId)
        : refreshing ? refreshShownotes(article.id) : prepareArticle(article.id))
      if (version === prepareVersion.current) {
        articleReady.current = true
        setPrepared({ status: "ready", revision: result.revision, readingKey: result.readingKey, shownotes: refreshing })
        setShownotesRefresh({ status: "idle" })
      }
    } catch (failure) {
      if (version === prepareVersion.current) {
        if (keepArticle) setShownotesRefresh({ status: "error", message: String(failure) })
        else setPrepared({ status: "error", message: String(failure) })
      }
    } finally {
      if (version === prepareVersion.current) preparing.current = false
    }
  }, [article.id, article.previewId])

  useEffect(() => {
    void prepare()
    return () => { prepareVersion.current++; preparing.current = false; articleReady.current = false }
  }, [prepare])

  useEffect(() => {
    if (lastDetailsRevision.current === detailsRevision) return
    lastDetailsRevision.current = detailsRevision
    let current = true
    void (async () => {
      await positionBridge.current?.flush()
      if (current) {
        // Supersede a preparation started before the successful metadata edit.
        prepareVersion.current++
        preparing.current = false
        await prepare()
      }
    })()
    return () => { current = false }
  }, [detailsRevision, prepare])

  useLayoutEffect(() => {
    if (!readingKey) return
    // A Shownotes refresh may change only the hash, without loading a new
    // document. Keep its session; real iframe reloads announce a fresh one.
    const bridge = new ReadingPositionBridge({ key: readingKey, load: loadReadingPosition, save: saveReadingPosition,
      send: data => frame.current?.contentWindow?.postMessage(data, "*"),
      onError: () => setPositionError("Could not save or restore reading position."),
    })
    positionBridge.current = bridge
    const message = (event: MessageEvent) => {
      if (event.source === frame.current?.contentWindow && event.data && typeof event.data === "object") bridge.receive(event.data)
    }
    window.addEventListener("message", message)
    return () => { window.removeEventListener("message", message); bridge.close(); if (positionBridge.current === bridge) positionBridge.current = undefined }
  }, [readingKey])

  useEffect(() => {
    function back(event: KeyboardEvent) {
      if (readerShortcutsBlocked() || event.defaultPrevented || event.isComposing || event.keyCode === 229) return
      if (!event.altKey && (event.metaKey || event.ctrlKey)
        && event.key.toLowerCase() === "f" && prepared.status === "ready") {
        event.preventDefault()
        setMenu(undefined)
        frame.current?.contentWindow?.postMessage({ linkReadCommand: "search" }, "*")
        return
      }
      if (event.key !== "Escape" || event.repeat) return
      event.preventDefault()
      event.stopImmediatePropagation()
      if (menu) { closeMenu(); return }
      if (prepared.status !== "ready" || !frame.current?.contentWindow) { void leave(onBack); return }
      const isCurrent = onNavigationStart?.() ?? (() => true)
      escapeBridge.current.request(() => {
        // A dialog/menu may have opened while the frame was answering.
        if (isCurrent() && !readerShortcutsBlocked() && !actions.current?.querySelector("[data-action-menu]")) void leave(onBack)
      })
    }
    window.addEventListener("keydown", back, true)
    return () => window.removeEventListener("keydown", back, true)
  }, [onBack, menu, onNavigationStart, prepared.status])

  useLayoutEffect(() => {
    function handleFrameMessage(event: MessageEvent) {
      if (event.source !== frame.current?.contentWindow) return
      escapeBridge.current.receive(event.data)
      const theme = event.data?.linkReadTheme
      if (theme === "light" || theme === "dark") onThemeChange?.(theme)
      if (event.data?.linkReadGoBack === true && !readerShortcutsBlocked()) void leave(onBack)
      if (typeof event.data?.linkReadVideo === "string") {
        setError("")
        openVideo(event.data.linkReadVideo).catch(error => setError(String(error)))
      }
      if (typeof event.data?.linkReadExternal === "string") {
        setError("")
        openExternal(event.data.linkReadExternal).catch(error => setError(String(error)))
      }
      if (event.data?.linkReadRefreshShownotes === true && !isPreview) void prepare(true)
      const key = event.data?.linkReadShortcut
      if (key === "Escape" || key === "o" || key === "," || key === "k") {
        document.dispatchEvent(new KeyboardEvent("keydown", { key, metaKey: key !== "Escape", bubbles: true, cancelable: true }))
      }
    }
    window.addEventListener("message", handleFrameMessage)
    return () => window.removeEventListener("message", handleFrameMessage)
  }, [onBack, prepare, isPreview, onNavigationStart, onThemeChange])

  useEffect(() => {
    if (!menu) return
    actions.current?.querySelector<HTMLButtonElement>("[data-action-menu] button")?.focus()
    function dismiss(event: PointerEvent) {
      if (!actions.current?.contains(event.target as Node)) setMenu(undefined)
    }
    document.addEventListener("pointerdown", dismiss)
    return () => {
      document.removeEventListener("pointerdown", dismiss)
    }
  }, [menu])

  function toggle(next: Menu) {
    setMenu(menu === next ? undefined : next)
  }

  async function open(action: () => Promise<unknown>) {
    setMenu(undefined)
    setError("")
    try {
      await action()
    } catch (error) {
      setError(String(error))
    }
  }

  async function save(format: ExportFormat) {
    if (format === "markdown" && onExportMarkdown) {
      setMenu(undefined); exportButton.current?.focus(); onExportMarkdown(article); return
    }
    setExporting(true)
    try {
      await open(() => exportArticle(article.id, format))
    } finally {
      setExporting(false)
      requestAnimationFrame(() => exportButton.current?.focus())
    }
  }

  return (
    <section className="desktop-reader" aria-label="Article reader">
      <header className="desktop-reader-toolbar">
        <button className="desktop-reader-action desktop-reader-back" type="button" onClick={() => void leave(onBack)}>
          <ArrowLeft size={17} aria-hidden="true" />{backLabel}
        </button>
        <div className="desktop-reader-actions" ref={actions} onKeyDown={menuNavigation} onBlur={event => {
          // Safari can blur a focused menu button to null on mouse-down. Keep
          // it mounted for the following click; outside pointer-down dismisses.
          if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget)) setMenu(undefined)
        }}>
          {!isPreview && <>
          <ReadingActions article={article} onChange={onStateChange} compact nativeTooltips={false} />
          <button className="desktop-reader-action" type="button" aria-label="Open in Browser"
            onClick={() => void open(() => openArticle(article.id))}>
            <ExternalLink size={17} aria-hidden="true" />
          </button>
          <div className="desktop-reader-popover">
            <button className="desktop-reader-action" type="button" ref={exportButton} disabled={exporting}
              aria-label="Export" aria-expanded={menu === "export"} onClick={() => toggle("export")}>
              <Share size={17} aria-hidden="true" />
            </button>
            {menu === "export" && (
              <div className="desktop-reader-menu" role="group" aria-label="Export format" data-action-menu>
                <button type="button" onClick={() => void save("html")}><FileText size={15} aria-hidden="true" />HTML</button>
                <button type="button" onClick={() => void save("markdown")}><FileText size={15} aria-hidden="true" />Markdown</button>
                <button type="button" disabled={prepared.status !== "ready"} onClick={() => {
                  frame.current?.contentWindow?.postMessage({ linkReadCommand: "print" }, "*")
                  setMenu(undefined)
                  exportButton.current?.focus()
                }}><Printer size={15} aria-hidden="true" />Print transcript / Save PDF</button>
              </div>
            )}
          </div>
          <div className="desktop-reader-popover">
            <button className="desktop-reader-action" type="button" ref={moreButton}
              aria-label="More actions" aria-expanded={menu === "more"} onClick={() => toggle("more")}>
              <Ellipsis size={20} aria-hidden="true" />
            </button>
            {menu === "more" && (
              <div className="desktop-reader-menu" role="group" aria-label="More actions" data-action-menu>
                {onEdit && <button type="button" onClick={() => { setMenu(undefined); moreButton.current?.focus(); onEdit(article) }}>
                  <Pencil size={15} aria-hidden="true" />Edit details
                </button>}
                <button type="button" disabled={Boolean(onViewRegeneration)} onClick={() => {
                  moreButton.current?.focus({ preventScroll: true })
                  void open(() => onRegenerate(article))
                }}>
                  <Sparkles size={15} aria-hidden="true" />{article.hasAnalysis === false ? "Enrich with AI" : "Regenerate"}
                </button>
                <button type="button" onClick={() => {
                  void open(() => revealArticle(article.id))
                  moreButton.current?.focus()
                }}><FolderOpen size={15} aria-hidden="true" />Show in Finder</button>
                {article.sourceUrl && (
                  <button type="button" onClick={() => {
                    if (article.sourceUrl) void open(() => openVideo(article.sourceUrl!))
                    moreButton.current?.focus()
                  }}><ArrowUpRight size={15} aria-hidden="true" />Original Source</button>
                )}
                <button type="button" onClick={() => {
                  frame.current?.contentWindow?.postMessage({ linkReadCommand: "toggle-theme" }, "*")
                  setMenu(undefined)
                  moreButton.current?.focus()
                }}><SunMoon size={15} aria-hidden="true" />Toggle Theme</button>
              </div>
            )}
          </div>
          </>}
        </div>
      </header>
      {onOpenUpdated ? <div className="reader-update-notice" role="status">
        <span>A new version is ready.</span>
        <button className="text-button" type="button" onClick={() => void leave(onOpenUpdated)}>Open updated article</button>
      </div> : onViewRegeneration && <div className="reader-update-notice" role="status">
        <span>Reprocessing in the background.</span>
        <button className="text-button" type="button" onClick={event => {
          event.currentTarget.focus({ preventScroll: true })
          onViewRegeneration()
        }}>View task</button>
      </div>}
      {isPreview && <div className="reader-preview-notice" role="status" data-status={preview?.status ?? "interrupted"}>
        {preview?.status === "running" && <LoaderCircle size={14} className="spin" aria-hidden="true" />}
        <span>{preview?.status === "done" ? "Finished article ready"
          : preview?.status === "running" ? "Original transcript · AI in progress"
          : preview?.status === "queued" ? "Original transcript · AI queued"
          : "Original transcript · AI stopped"}</span>
        {preview?.status === "done" && preview.article && <button className="text-button" type="button" onClick={() => void leave(preview.onOpenComplete)}>
          View finished article<ArrowUpRight size={13} aria-hidden="true" />
        </button>}
        {article.sourceUrl && <button className="text-button" type="button" onClick={() => void open(() => openVideo(article.sourceUrl!))}>Original Source</button>}
      </div>}
      {positionError && <div className="reader-position-error" role="status"><span>{positionError}</span>
        <button className="icon-button" type="button" aria-label="Dismiss position notice" onClick={() => setPositionError("")}><X size={14} /></button>
      </div>}
      {!isPreview && article.hasAnalysis === false && <div className="reader-raw-notice">
        <span>Original transcript</span>
        <button className="text-button" type="button" disabled={Boolean(onViewRegeneration)} onClick={() => void open(() => onRegenerate(article))}>
          <Sparkles size={14} aria-hidden="true" />Enrich with AI
        </button>
      </div>}
      {error && <p className="desktop-reader-error" role="alert">{error}</p>}
      {shownotesRefresh.status !== "idle" && <div className="reader-shownotes-refresh"
        role={shownotesRefresh.status === "error" ? "alert" : "status"} data-state={shownotesRefresh.status}>
        {shownotesRefresh.status === "loading"
          ? <><LoaderCircle size={14} className="spin" aria-hidden="true" /><span>Fetching Shownotes…</span></>
          : <><CircleAlert size={14} aria-hidden="true" /><span>Could not fetch Shownotes. {shownotesRefresh.message}</span>
            <button className="text-button" type="button" onClick={() => void prepare(true)}><RotateCw size={13} />Retry</button>
            <button className="icon-button" type="button" aria-label="Dismiss Shownotes error" onClick={() => setShownotesRefresh({ status: "idle" })}><X size={14} /></button>
          </>}
      </div>}
      {prepared.status === "ready" ? <iframe ref={frame} className="desktop-reader-frame" sandbox="allow-scripts allow-same-origin allow-modals" title={article.title}
        src={`linkread-reader://localhost/${article.previewId ? `preview/${encodeURIComponent(article.previewId)}` : encodeURIComponent(article.id)}?v=${encodeURIComponent(prepared.revision)}&platform=${article.platform}${prepared.shownotes ? "#shownotes" : ""}`} />
        : <div className="desktop-reader-loading" role={prepared.status === "error" ? "alert" : "status"}>
          {prepared.status === "loading" ? <><LoaderCircle size={20} className="spin" aria-hidden="true" /><span>Preparing your article…</span></>
            : <><CircleAlert size={20} aria-hidden="true" /><span>{prepared.message}</span>
              <button className="secondary" type="button" onClick={() => void prepare()}><RotateCw size={14} />Try Again</button>
            </>}
        </div>}
    </section>
  )
}
