import { useEffect, useId, useMemo, useRef, useState } from "react"
import { ArrowDown, ArrowUp, BookOpen, CircleCheck, CornerDownLeft, LoaderCircle, Search, Star, X } from "lucide-react"
import { platformNames, type Article, type Library } from "./api"
import { quickOpenArticles } from "./quickOpenState"
import "./quick-open.css"

export function QuickOpen({ library, error, recent, onClose, onRead, onLibrary, onRefresh }: {
  library?: Library; error: string; recent: string[]; onClose: () => void
  onRead: (article: Article) => void; onLibrary: (query: string) => void; onRefresh: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const [query, setQuery] = useState("")
  const [selectedId, setSelectedId] = useState<string>()
  const id = useId()
  const matches = useMemo(() => quickOpenArticles(library?.articles ?? [], query, recent), [library, query, recent])
  const visible = matches.slice(0, 12)
  const selected = Math.max(0, visible.findIndex(article => article.id === selectedId))

  useEffect(() => {
    const previous = document.activeElement
    dialog.current?.showModal()
    input.current?.focus()
    return () => {
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus({ preventScroll: true })
    }
  }, [])

  useEffect(() => {
    document.getElementById(`${id}-option-${selected}`)?.scrollIntoView({ block: "nearest" })
  }, [id, selected, query])

  return <dialog className="quick-open" ref={dialog} aria-labelledby={`${id}-title`}
    onCancel={event => { event.preventDefault(); onClose() }}
    onClick={event => {
      if (event.target !== event.currentTarget) return
      const bounds = event.currentTarget.getBoundingClientRect()
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onClose()
    }}
    onKeyDown={event => {
      if (event.nativeEvent.isComposing) return
      event.stopPropagation()
      if (event.key === "Escape" || ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k")) {
        event.preventDefault(); onClose()
      } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault()
        if (visible.length) {
          const next = (selected + (event.key === "ArrowDown" ? 1 : -1) + visible.length) % visible.length
          setSelectedId(visible[next].id)
        }
      } else if (event.key === "Enter" && event.target === input.current) {
        event.preventDefault()
        if (visible[selected]) onRead(visible[selected])
      }
    }}>
    <h2 id={`${id}-title`} className="quick-open-sr">Quick open</h2>
    <div className="quick-open-search">
      <Search size={19} aria-hidden="true" />
      <input ref={input} type="text" role="combobox" aria-label="Search library"
        aria-expanded="true" aria-autocomplete="list" aria-controls={`${id}-results`}
        aria-activedescendant={visible.length ? `${id}-option-${selected}` : undefined}
        placeholder="Search articles, creators, or sources…" value={query}
        onChange={event => { setQuery(event.target.value); setSelectedId(undefined) }} autoComplete="off" spellCheck={false} />
      <button className="icon-button" type="button" aria-label="Close quick open" title="Close (Esc)" onClick={onClose}><X size={17} /></button>
    </div>
    <div className="quick-open-section" aria-live="polite">
      {query.trim() ? `${matches.length} ${matches.length === 1 ? "result" : "results"}` : "Recent articles"}
    </div>
    <ul className="quick-open-results" id={`${id}-results`} role="listbox" aria-label="Articles">
      {visible.map((article, index) => <li key={article.id} id={`${id}-option-${index}`} role="option"
        aria-selected={index === selected} onPointerMove={() => setSelectedId(article.id)}
        onClick={() => onRead(article)}>
        <span className="quick-open-document"><BookOpen size={18} aria-hidden="true" /></span>
        <span className="quick-open-text"><strong>{article.title}</strong>
          <small>{[article.author, platformNames[article.platform]].filter(Boolean).join(" · ")}</small>
        </span>
        <span className="quick-open-flags">
          {article.isStarred && <Star size={14} aria-label="Starred" />}
          {article.isRead && <CircleCheck size={14} aria-label="Completed" />}
        </span>
        {index === selected && <CornerDownLeft className="quick-open-enter" size={15} aria-hidden="true" />}
      </li>)}
    </ul>
    {!visible.length && !error && <div className="quick-open-empty" role="status">
      {!library ? <><LoaderCircle className="spin" size={18} />Loading library…</>
        : query.trim() ? "No articles found. Try a title, creator, or source."
        : "Your saved articles will appear here."}
    </div>}
    {error && <div className="quick-open-error" role="status">
      <span>{library ? "The latest library could not be loaded." : "Could not load your library."}</span>
      <button type="button" className="text-button" onClick={onRefresh}>Retry</button>
    </div>}
    <footer className="quick-open-footer">
      <span className="quick-open-hints"><ArrowUp size={12} /><ArrowDown size={12} /> Navigate <CornerDownLeft size={13} /> Open</span>
      <button type="button" className="text-button" onClick={() => onLibrary(query)}>
        {matches.length > visible.length ? `View all ${matches.length} results` : "Open library"}
      </button>
    </footer>
  </dialog>
}
