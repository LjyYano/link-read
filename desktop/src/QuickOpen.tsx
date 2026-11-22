import { useEffect, useId, useMemo, useRef, useState } from "react"
import { BookOpen, CircleCheck, CornerDownLeft, LoaderCircle, Search, Star, X } from "lucide-react"
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
    <h2 id={`${id}-title`} className="quick-open-sr">快速打开</h2>
    <div className="quick-open-search">
      <Search size={19} aria-hidden="true" />
      <input ref={input} type="text" role="combobox" aria-label="搜索书架"
        aria-expanded="true" aria-autocomplete="list" aria-controls={`${id}-results`}
        aria-activedescendant={visible.length ? `${id}-option-${selected}` : undefined}
        placeholder="搜索标题、作者、分类或来源" value={query}
        onChange={event => { setQuery(event.target.value); setSelectedId(undefined) }} autoComplete="off" spellCheck={false} />
      <button className="icon-button" type="button" aria-label="关闭快速打开" title="关闭 (Esc)" onClick={onClose}><X size={17} /></button>
    </div>
    <div className="quick-open-section" aria-live="polite">
      {query.trim() ? `${matches.length} 篇匹配文章` : "最近文章"}
    </div>
    <ul className="quick-open-results" id={`${id}-results`} role="listbox" aria-label="文章">
      {visible.map((article, index) => <li key={article.id} id={`${id}-option-${index}`} role="option"
        aria-selected={index === selected} onPointerMove={() => setSelectedId(article.id)}
        onClick={() => onRead(article)}>
        <span className="quick-open-document"><BookOpen size={18} aria-hidden="true" /></span>
        <span className="quick-open-text"><strong>{article.title}</strong>
          <small>{[article.author, platformNames[article.platform]].filter(Boolean).join(" · ")}</small>
        </span>
        <span className="quick-open-flags">
          {article.isStarred && <Star size={14} aria-label="已收藏" />}
          {article.isRead && <CircleCheck size={14} aria-label="已读" />}
        </span>
        {index === selected && <CornerDownLeft className="quick-open-enter" size={15} aria-hidden="true" />}
      </li>)}
    </ul>
    {!visible.length && !error && <div className="quick-open-empty" role="status">
      {!library ? <><LoaderCircle className="spin" size={18} />正在读取书架…</>
        : query.trim() ? "没有符合条件的文章"
        : "书架还没有文章"}
    </div>}
    {error && <div className="quick-open-error" role="status">
      <span>{library ? "无法更新书架" : "无法读取书架"}</span>
      <button type="button" className="text-button" onClick={onRefresh}>重新加载</button>
    </div>}
    <footer className="quick-open-footer">
      <button type="button" className="text-button" onClick={() => onLibrary(query)}>
        {matches.length > visible.length ? `查看全部 ${matches.length} 篇` : "查看书架"}
      </button>
    </footer>
  </dialog>
}
