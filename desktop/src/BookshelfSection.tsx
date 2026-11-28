import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react"
import {
  ArrowDownToLine, ArrowUpRight, BookOpen, Circle, CircleAlert, CircleCheck, FolderOpen,
  LoaderCircle, MoreHorizontal, Pencil, Plus, Search, Sparkles, Star, Trash2, X,
} from "lucide-react"
import { exportArticle, openVideo, revealArticle, type Article, type ExportFormat, type Library } from "./api"
import { ConfirmDialog } from "./ConfirmDialog"
import { ArticleUpdatedAt, Cover, SourceDetails } from "./JobCard"
import { tr } from "./language.ts"
import { articleState, defaultShelfFilters, filterArticles, searchArticles, shelfCounts, type ReadFilter, type ShelfFilters } from "./libraryState"
import { menuNavigation } from "./menu"
import type { ChangeArticleState } from "./ReadingActions"

export type ArticleListProps = {
  library: Library | undefined
  error: string
  onRead: (article: Article) => void
  onRefresh: () => void
  onDelete: (id: string) => Promise<void>
  onRegenerate: (article: Article) => Promise<void>
  onStateChange: ChangeArticleState
  onEdit?: (article: Article) => void
  onExportMarkdown?: (article: Article) => void
}

export function ArticleActions({ article, onDelete, onRegenerate, onEdit, onExportMarkdown, onStateChange, contextMenu, onContextMenuClose }: {
  article: Article
  onDelete: (id: string) => Promise<void>
  onRegenerate: (article: Article) => Promise<void>
  onEdit?: (article: Article) => void
  onExportMarkdown?: (article: Article) => void
  onStateChange?: ChangeArticleState
  contextMenu?: { x: number; y: number } | null
  onContextMenuClose?: () => void
}) {
  const [open, setOpen] = useState(false)
  const [pending, setPending] = useState(false)
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)
  const [error, setError] = useState("")
  const container = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const [menuLayout, setMenuLayout] = useState<CSSProperties>()
  const isOpen = open || Boolean(contextMenu)

  function closeMenu() {
    setOpen(false)
    onContextMenuClose?.()
  }

  useLayoutEffect(() => {
    if (!isOpen) return
    function place() {
      if (!panel.current) return
      if (contextMenu) {
        const width = panel.current.offsetWidth || 200
        const height = panel.current.scrollHeight || 260
        const margin = 10
        const left = Math.max(margin, Math.min(contextMenu.x, window.innerWidth - width - margin))
        const below = window.innerHeight - contextMenu.y - margin
        const above = contextMenu.y - margin
        const upwards = below < height && above > below
        const top = upwards
          ? Math.max(margin, contextMenu.y - Math.min(height, above))
          : Math.max(margin, Math.min(contextMenu.y, window.innerHeight - Math.min(height, window.innerHeight - 2 * margin) - margin))
        setMenuLayout({
          position: "fixed",
          left,
          top,
          right: "auto",
          insetBlockStart: "auto",
          insetBlockEnd: "auto",
          maxHeight: Math.max(120, upwards ? above : below),
          overflowY: "auto",
          zIndex: 50,
        })
        return
      }
      if (!trigger.current) return
      const anchor = trigger.current.getBoundingClientRect()
      const workspace = trigger.current.closest(".workspace")?.getBoundingClientRect()
      const top = Math.max(0, workspace?.top ?? 0) + 8
      const bottom = Math.min(window.innerHeight, workspace?.bottom ?? window.innerHeight) - 8
      const above = Math.max(0, anchor.top - top - 5), below = Math.max(0, bottom - anchor.bottom - 5)
      const upwards = below < panel.current.scrollHeight + 2 && above > below
      setMenuLayout({
        insetBlockStart: upwards ? "auto" : "calc(100% + 5px)",
        insetBlockEnd: upwards ? "calc(100% + 5px)" : "auto",
        maxHeight: upwards ? above : below,
        overflowY: "auto",
      })
    }
    place()
    window.addEventListener("resize", place)
    window.addEventListener("scroll", place, true)
    return () => { window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true) }
  }, [isOpen, contextMenu, error])

  useEffect(() => {
    if (!isOpen) return
    panel.current?.querySelector("button")?.focus({ preventScroll: true })
    function outside(event: PointerEvent) {
      if (event.target instanceof Node && !container.current?.contains(event.target)) {
        setOpen(false)
        onContextMenuClose?.()
      }
    }
    document.addEventListener("pointerdown", outside)
    return () => document.removeEventListener("pointerdown", outside)
  }, [isOpen, onContextMenuClose])

  function focusFallbackAfterRowRemoval(row: Element | null | undefined, bookshelf: Element | null | undefined, neighbors: (Element | null | undefined)[]) {
    requestAnimationFrame(() => {
      if (trigger.current?.isConnected) {
        trigger.current.focus({ preventScroll: true })
      } else if (bookshelf?.isConnected) {
        const next = neighbors.find(element => element?.isConnected && element !== row)?.querySelector<HTMLButtonElement>(".shelf-article-link")
        const filter = bookshelf.querySelector<HTMLButtonElement>(".shelf-star-filter[aria-pressed='true']")
          ?? bookshelf.querySelector<HTMLButtonElement>(".shelf-read-filters [aria-pressed='true']")
          ?? bookshelf.querySelector<HTMLButtonElement>(".bookshelf-title button")
        ;(next ?? filter)?.focus()
      }
    })
  }

  async function perform(action: () => Promise<unknown>) {
    const menu = container.current
    const button = trigger.current
    const row = button?.closest(".shelf-row")
    const bookshelf = row?.closest(".bookshelf-page")
    const neighbors = [row?.nextElementSibling, row?.previousElementSibling]
    setPending(true)
    setError("")
    try {
      await action()
      closeMenu()
      requestAnimationFrame(() => {
        if (document.activeElement !== document.body && !menu?.contains(document.activeElement)) return
        if (button?.isConnected) {
          button.focus({ preventScroll: true })
        } else {
          focusFallbackAfterRowRemoval(row, bookshelf, neighbors)
        }
      })
    } catch (failure) {
      setError(String(failure))
    } finally {
      setPending(false)
    }
  }

  function save(format: ExportFormat) {
    if (format === "markdown" && onExportMarkdown) {
      closeMenu(); trigger.current?.focus(); onExportMarkdown(article); return
    }
    void perform(() => exportArticle(article.id, format))
  }

  async function confirmRemove() {
    const button = trigger.current
    const row = button?.closest(".shelf-row")
    const bookshelf = row?.closest(".bookshelf-page")
    const neighbors = [row?.nextElementSibling, row?.previousElementSibling]
    await onDelete(article.id)
    setConfirmDeleteOpen(false)
    focusFallbackAfterRowRemoval(row, bookshelf, neighbors)
  }

  const sourceUrl = article.sourceUrl
  const { isRead, isStarred } = articleState(article)
  const ReadIcon = isRead ? CircleCheck : Circle
  return (
    <div className="shelf-overflow" ref={container} onBlur={event => {
      if (!pending && event.relatedTarget && !container.current?.contains(event.relatedTarget)) closeMenu()
    }} onKeyDown={event => {
      if (isOpen && menuNavigation(event)) return
      if (event.key !== "Escape" || !isOpen) return
      event.stopPropagation()
      closeMenu()
      trigger.current?.focus()
    }}>
      <button ref={trigger} className="icon-button shelf-overflow-button" type="button" title={tr("更多操作")}
        aria-label={tr("{0}的更多操作", article.title)} aria-expanded={isOpen} onClick={() => {
          setError("")
          if (isOpen) closeMenu()
          else setOpen(true)
        }}>
        <MoreHorizontal size={18} />
      </button>
      {isOpen && <div ref={panel} style={menuLayout} className="shelf-overflow-panel" aria-label={tr("文章操作")} role="group" data-action-menu>
        {onStateChange && <>
          <button type="button" disabled={pending} onClick={() => void perform(() => onStateChange(article.id, { isRead: !isRead }))}>
            <ReadIcon size={15} aria-hidden="true" /> {isRead ? tr("标为未读") : tr("标为已读")}
          </button>
          <button type="button" disabled={pending} onClick={() => void perform(() => onStateChange(article.id, { isStarred: !isStarred }))}>
            <Star size={15} fill={isStarred ? "currentColor" : "none"} aria-hidden="true" /> {isStarred ? tr("取消收藏") : tr("收藏")}
          </button>
          <div className="menu-separator" role="separator" />
        </>}
        <button type="button" disabled={pending} onClick={() => void perform(() => onRegenerate(article))}>
          <Sparkles size={15} /> {article.hasAnalysis === false ? tr("AI 整理文章") : tr("重新整理")}
        </button>
        {onEdit && <button type="button" disabled={pending} onClick={() => { closeMenu(); trigger.current?.focus(); onEdit(article) }}>
          <Pencil size={15} /> {tr("编辑详情")}
        </button>}
        <div className="menu-separator" role="separator" />
        <button type="button" disabled={pending} onClick={() => save("html")}><ArrowDownToLine size={15} /> {tr("导出 HTML")}</button>
        <button type="button" disabled={pending} onClick={() => save("markdown")}><ArrowDownToLine size={15} /> {tr("导出 Markdown")}</button>
        <button type="button" disabled={pending} onClick={() => void perform(() => revealArticle(article.id))}>
          <FolderOpen size={15} /> {tr("在访达显示")}
        </button>
        {sourceUrl && <button type="button" disabled={pending} onClick={() => void perform(() => openVideo(sourceUrl))}>
          <ArrowUpRight size={15} /> {article.platform === "apple_podcasts" ? tr("打开原节目") : tr("打开原视频")}
        </button>}
        <div className="menu-separator" role="separator" />
        <button className="shelf-delete" type="button" disabled={pending} onClick={() => { closeMenu(); setConfirmDeleteOpen(true) }}>
          <Trash2 size={15} /> {tr("删除文章")}
        </button>
        {error && <p role="alert" className="shelf-overflow-error">{error}</p>}
      </div>}
      {confirmDeleteOpen && (
        <ConfirmDialog
          title={tr("删除文章")}
          message={tr("确认永久删除《") + article.title + tr("》？\n本地字幕、AI 笔记、HTML 阅读页都会一起删除，且无法撤销。")}
          confirmLabel={tr("删除文章")}
          danger
          onConfirm={confirmRemove}
          onCancel={() => setConfirmDeleteOpen(false)}
        />
      )}
    </div>
  )
}

function ArticleRow({ article, onRead, onDelete, onRegenerate, onStateChange, onEdit, onExportMarkdown }: {
  article: Article
  onRead: (article: Article) => void
  onDelete: (id: string) => Promise<void>
  onRegenerate: (article: Article) => Promise<void>
  onStateChange: ChangeArticleState
  onEdit?: (article: Article) => void
  onExportMarkdown?: (article: Article) => void
}) {
  const { isRead, isStarred } = articleState(article)
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null)
  return (
    <li className="shelf-row" data-platform={article.platform} data-article-id={article.id}
      onContextMenu={event => {
        if (event.target instanceof Element && event.target.closest("[data-action-menu]")) return
        event.preventDefault()
        setContextMenu({ x: event.clientX, y: event.clientY })
      }}>
      <button type="button" className="shelf-article-link" onClick={() => onRead(article)}>
        <span className="shelf-media"><Cover {...article} /></span>
        <span className="shelf-article-info">
          <span className="shelf-article-heading">
            <span className="shelf-article-title" title={article.title}>{article.title}</span>
            {(isRead || isStarred) && <span className="shelf-article-status">
              {isRead && <span className="shelf-status-read" role="img" aria-label={tr("已读")} title={tr("已读")}>
                <CircleCheck size={14} aria-hidden="true" />
              </span>}
              {isStarred && <span className="shelf-status-star" role="img" aria-label={tr("已收藏")} title={tr("已收藏")}>
                <Star size={13} fill="currentColor" aria-hidden="true" />
              </span>}
            </span>}
          </span>
          <span className="shelf-article-meta">
            <SourceDetails {...article} />
            <ArticleUpdatedAt value={article.updatedAt} />
            {article.hasAnalysis === false && <span className="shelf-raw">{tr("原文字幕")}</span>}
          </span>
        </span>
      </button>
      <ArticleActions article={article} onDelete={onDelete} onRegenerate={onRegenerate} onEdit={onEdit}
        onExportMarkdown={onExportMarkdown} onStateChange={onStateChange}
        contextMenu={contextMenu} onContextMenuClose={() => setContextMenu(null)} />
    </li>
  )
}

export function ArticleCard({ article, onRead, onDelete, onRegenerate, onStateChange, onEdit, onExportMarkdown }: {
  article: Article
  onRead: (article: Article) => void
  onDelete: (id: string) => Promise<void>
  onRegenerate: (article: Article) => Promise<void>
  onStateChange: ChangeArticleState
  onEdit?: (article: Article) => void
  onExportMarkdown?: (article: Article) => void
}) {
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null)
  return (
    <li className="article-card" data-platform={article.platform} data-article-id={article.id}
      onContextMenu={event => {
        if (event.target instanceof Element && event.target.closest("[data-action-menu]")) return
        event.preventDefault()
        setContextMenu({ x: event.clientX, y: event.clientY })
      }}>
      <button type="button" className="article-card-link" onClick={() => onRead(article)}>
        <span className="article-card-media"><Cover {...article} withBackdrop /></span>
        <span className="article-card-info">
          <span className="article-card-title" title={article.title}>{article.title}</span>
          <SourceDetails {...article} />
        </span>
      </button>
      <div className="article-card-footer">
        <ArticleUpdatedAt value={article.updatedAt} />
        <ArticleActions article={article} onDelete={onDelete} onRegenerate={onRegenerate} onEdit={onEdit}
          onExportMarkdown={onExportMarkdown} onStateChange={onStateChange}
          contextMenu={contextMenu} onContextMenuClose={() => setContextMenu(null)} />
      </div>
    </li>
  )
}

export function LibraryState({ library, error, onRefresh }: Pick<ArticleListProps, "library" | "error" | "onRefresh">) {
  if (error) return (
    <div className="shelf-state shelf-state-error" role="alert">
      <CircleAlert size={19} /><span>{error}</span><button className="secondary" type="button" onClick={onRefresh}>{tr("重新加载")}</button>
    </div>
  )
  if (!library) return <div className="shelf-state" role="status"><LoaderCircle className="spin" size={19} /><span>{tr("正在读取书架…")}</span></div>
  if (library.unavailable) return <p className="shelf-warning" role="status">{library.unavailable} {tr("篇文章无法读取")}</p>
  return null
}

export function Bookshelf({
  library, error, onRead, onRefresh, onDelete, onRegenerate, onStateChange, onCreate, search, onSearch, filters, onFilters, onEdit, onExportMarkdown,
}: ArticleListProps & {
  onCreate: () => void; search: string; onSearch: (text: string) => void
  filters: ShelfFilters; onFilters: (filters: ShelfFilters) => void
}) {
  const searchField = useRef<HTMLInputElement>(null)
  const matching = searchArticles(library?.articles ?? [], search)
  const articles = filterArticles(matching, filters)
  const counts = shelfCounts(matching, filters.starred)
  const filtered = Boolean(search.trim() || filters.read !== "all" || filters.starred)
  const readFilters: { key: ReadFilter; label: string }[] = [
    { key: "all", label: tr("全部") }, { key: "unread", label: tr("未读") }, { key: "completed", label: tr("已读") },
  ]

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented || event.isComposing || event.keyCode === 229 || event.altKey) return
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "f") return
      if (document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) return
      if (!searchField.current) return
      event.preventDefault()
      searchField.current.focus()
      searchField.current.select()
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [])

  function clearSearch() {
    onSearch("")
    searchField.current?.focus()
  }

  function resetFilters() {
    clearSearch()
    onFilters(defaultShelfFilters)
  }

  return (
    <section className="bookshelf-page" aria-labelledby="bookshelf-title">
      <header className="bookshelf-title">
        <h1 id="bookshelf-title">{tr("书架")}</h1>
        <button className="primary" type="button" onClick={onCreate}><Plus size={16} aria-hidden="true" />{tr("新建文章")}</button>
      </header>
      {library && library.articles.length > 0 && <div className="shelf-toolbar">
        <div className="shelf-filters" role="group" aria-label={tr("筛选文章")}>
          <div className="shelf-read-filters" role="group" aria-label={tr("阅读状态")}>
            {readFilters.map(filter => <button key={filter.key} type="button" aria-pressed={filters.read === filter.key}
              onClick={() => onFilters({ ...filters, read: filter.key })}>
              {filter.label}<span>{counts[filter.key]}</span>
            </button>)}
          </div>
          <button className="shelf-star-filter" type="button" aria-pressed={filters.starred}
            onClick={() => onFilters({ ...filters, starred: !filters.starred })}>
            <Star size={14} aria-hidden="true" fill={filters.starred ? "currentColor" : "none"} />{tr("收藏")}<span>{counts.starred}</span>
          </button>
        </div>
        <span className="shelf-search">
          <Search size={16} aria-hidden="true" />
          <input ref={searchField} aria-label={tr("搜索书架")} type="search" value={search} placeholder={tr("搜索标题、作者、分类或来源")}
            onChange={event => onSearch(event.target.value)} />
          {search && <button className="icon-button" type="button" aria-label={tr("清空搜索")} title={tr("清空搜索")} onClick={clearSearch}><X size={15} /></button>}
        </span>
        <span className="sr-only" aria-live="polite">{tr("显示")} {articles.length} {tr("篇文章")}</span>
      </div>}
      {articles.length > 0 ? <ul className="shelf-list">
        {articles.map(article => <ArticleRow key={article.id} article={article} onRead={onRead} onDelete={onDelete} onRegenerate={onRegenerate} onStateChange={onStateChange} onEdit={onEdit} onExportMarkdown={onExportMarkdown} />)}
      </ul> : library && !error && <div className="shelf-state bookshelf-empty" role="status">
        {filtered ? <><Search size={20} aria-hidden="true" /><span>{tr("没有符合条件的文章")}</span>
          <button className="text-button" type="button" onClick={resetFilters}>{tr("清除搜索和筛选")}</button>
        </> : <><BookOpen size={24} strokeWidth={1.5} aria-hidden="true" /><span>{tr("书架还没有文章")}</span>
        </>}
      </div>}
      <LibraryState library={library} error={error} onRefresh={onRefresh} />
    </section>
  )
}
