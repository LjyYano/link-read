import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react"
import { Check, ChevronDown, Search } from "lucide-react"
import {
  effortDescription, effortShortLabel, effortsForSelection, filterCliModels, isValidCliModel,
  reconcileEffortForModel,
  type CliModelCatalog, type CliModelSettings, type CliProvider, type CliReasoningEffort,
} from "./cliModels"
import { tr } from "./language.ts"

type PickerItem = {
  id: string
  label: string
  meta?: string
  title?: string
  isCustom?: boolean
}

export function CliModelPicker({
  id,
  provider,
  catalog,
  value,
  disabled = false,
  onChange,
}: {
  id: string
  provider: CliProvider
  catalog: CliModelCatalog
  value: CliModelSettings
  disabled?: boolean
  onChange: (next: CliModelSettings) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [activeIndex, setActiveIndex] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const listboxId = useId()

  // Close menu if provider changes or component becomes disabled.
  useEffect(() => {
    setOpen(false)
    setQuery("")
  }, [provider, disabled])

  const defaultModelOption = useMemo(
    () => (catalog.defaultModel ? catalog.models.find(item => item.id === catalog.defaultModel) : undefined),
    [catalog.defaultModel, catalog.models],
  )
  const defaultModelHint = defaultModelOption?.label ?? catalog.defaultModel ?? undefined

  const items = useMemo<PickerItem[]>(() => {
    const trimmed = query.trim()
    const normalized = trimmed.toLowerCase()
    const list: PickerItem[] = []
    const defaultLabel = tr("CLI 默认")
    const matchesDefault = !normalized
      || defaultLabel.toLowerCase().includes(normalized)
      || "default".includes(normalized)
      || "cli".includes(normalized)
      || (defaultModelHint?.toLowerCase().includes(normalized) ?? false)
    if (matchesDefault) {
      list.push({
        id: "",
        label: defaultLabel,
        meta: defaultModelHint,
        title: defaultModelHint ? tr("跟随 CLI 默认模型（{0}）", defaultModelHint) : tr("跟随 CLI 默认模型"),
      })
    }
    const filtered = filterCliModels(catalog.models, query, value.model)
    for (const model of filtered) {
      list.push({
        id: model.id,
        label: model.label,
        title: model.description ? `${model.id} — ${model.description}` : model.id,
      })
    }
    if (trimmed && isValidCliModel(trimmed) && !list.some(item => item.id === trimmed)) {
      list.push({
        id: trimmed,
        label: tr("使用模型 “{0}”", trimmed),
        title: trimmed,
        isCustom: true,
      })
    }
    return list
  }, [catalog.models, defaultModelHint, query, value.model])

  const efforts = useMemo(
    () => effortsForSelection(provider, catalog, value.model),
    [provider, catalog, value.model],
  )

  useEffect(() => {
    if (!open) return
    const selectedIndex = items.findIndex(item => item.id === value.model)
    setActiveIndex(selectedIndex >= 0 ? selectedIndex : 0)
  }, [open, query])

  useEffect(() => {
    if (activeIndex >= items.length) {
      setActiveIndex(Math.max(0, items.length - 1))
    }
  }, [activeIndex, items.length])

  useEffect(() => {
    if (!open) return
    searchRef.current?.focus({ preventScroll: true })
    const onPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && !containerRef.current?.contains(event.target)) {
        setOpen(false)
        setQuery("")
      }
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented || event.isComposing || event.keyCode === 229) return
      event.preventDefault()
      event.stopPropagation()
      setOpen(false)
      setQuery("")
      triggerRef.current?.focus({ preventScroll: true })
    }
    document.addEventListener("pointerdown", onPointerDown)
    document.addEventListener("keydown", onKeyDown, true)
    return () => {
      document.removeEventListener("pointerdown", onPointerDown)
      document.removeEventListener("keydown", onKeyDown, true)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const active = listRef.current?.querySelector<HTMLElement>(`[data-option-index="${activeIndex}"]`)
    active?.scrollIntoView({ block: "nearest" })
  }, [open, activeIndex])

  function selectModel(modelId: string) {
    const nextEffort = reconcileEffortForModel(provider, catalog, modelId, value.reasoningEffort)
    onChange({ model: modelId, reasoningEffort: nextEffort })
    setOpen(false)
    setQuery("")
    triggerRef.current?.focus({ preventScroll: true })
  }

  function selectEffort(effort: CliReasoningEffort) {
    if (disabled) return
    onChange({ ...value, reasoningEffort: effort })
  }

  function onSearchKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.nativeEvent.isComposing) return
    if (event.key === "ArrowDown") {
      event.preventDefault()
      if (items.length) setActiveIndex(current => (current + 1) % items.length)
    } else if (event.key === "ArrowUp") {
      event.preventDefault()
      if (items.length) setActiveIndex(current => (current - 1 + items.length) % items.length)
    } else if (event.key === "Enter") {
      event.preventDefault()
      const chosen = items[activeIndex]
      if (chosen) selectModel(chosen.id)
    }
  }

  const selectedOption = value.model
    ? catalog.models.find(item => item.id === value.model)
    : undefined
  const triggerLabel = value.model
    ? (selectedOption?.label ?? value.model)
    : tr("CLI 默认")
  const triggerMeta = !value.model ? defaultModelHint : undefined
  const effortOptions: CliReasoningEffort[] = ["", ...efforts]

  return (
    <div ref={containerRef} className="cli-model-picker">
      <button
        ref={triggerRef}
        id={id}
        type="button"
        className={`cli-model-trigger${open ? " is-open" : ""}`}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        onClick={() => {
          if (disabled) return
          setOpen(current => !current)
          if (open) setQuery("")
        }}
      >
        <span className="cli-model-trigger-main">
          <span className="cli-model-trigger-name">{triggerLabel}</span>
          {triggerMeta && <span className="cli-model-trigger-meta">{triggerMeta}</span>}
        </span>
        <span className="cli-model-trigger-right">
          {value.reasoningEffort && (
            <span className="cli-model-trigger-effort" title={effortDescription(value.reasoningEffort)}>
              {effortShortLabel(value.reasoningEffort)}
            </span>
          )}
          <ChevronDown size={15} className="cli-model-chevron" aria-hidden="true" />
        </span>
      </button>

      {open && (
        <div className="cli-model-popover" role="presentation">
          <div className="cli-model-search">
            <Search size={16} className="cli-model-search-icon" aria-hidden="true" />
            <input
              ref={searchRef}
              type="text"
              value={query}
              autoComplete="off"
              spellCheck={false}
              placeholder={tr("搜索模型…")}
              aria-label={tr("搜索模型")}
              onChange={event => setQuery(event.target.value)}
              onKeyDown={onSearchKeyDown}
            />
          </div>

          <div ref={listRef} id={listboxId} className="cli-model-list" role="listbox" aria-label={tr("选择模型")}>
            {items.length > 0 ? items.map((item, index) => {
              const selected = item.id === value.model
              const active = index === activeIndex
              return (
                <button
                  key={item.id || "__default__"}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  data-option-index={index}
                  title={item.title}
                  className={[
                    "cli-model-option",
                    active ? "is-active" : "",
                    selected ? "is-selected" : "",
                    item.isCustom ? "is-custom" : "",
                  ].filter(Boolean).join(" ")}
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => selectModel(item.id)}
                >
                  <span className="cli-model-option-main">
                    <span className="cli-model-option-label">{item.label}</span>
                    {item.meta && <span className="cli-model-option-meta">{item.meta}</span>}
                  </span>
                  {selected && <Check size={16} className="cli-model-check" aria-hidden="true" />}
                </button>
              )
            }) : (
              <div className="cli-model-empty">{tr("没有匹配的模型")}</div>
            )}
          </div>

          <div className="cli-model-effort">
            <span className="cli-model-effort-label">{tr("思考深度")}</span>
            <div className="cli-model-effort-options" role="radiogroup" aria-label={tr("思考深度")}>
              {effortOptions.map(level => {
                const selected = value.reasoningEffort === level
                return (
                  <button
                    key={level || "__default__"}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    title={effortDescription(level)}
                    className={`cli-model-effort-button${selected ? " is-selected" : ""}`}
                    onClick={() => selectEffort(level)}
                  >
                    {effortShortLabel(level)}
                  </button>
                )
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
