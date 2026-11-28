import { useEffect, useMemo, useState } from "react"
import { ChevronDown, RotateCcw } from "lucide-react"
import { listSystemFonts } from "./api"
import { tr } from "./language.ts"
import {
  defaultReadingPreferences, isValidCustomFont, maxReaderFontSize, minReaderFontSize,
  readingPreferencesEqual, recommendedSystemFonts, resolveEffectiveReaderTheme,
  type ReaderDefaultTab, type ReaderFontFamily, type ReaderLineSpacing, type ReaderLineWidth,
  type ReaderThemePreference, type ReadingPreferences,
} from "./readingPreferences"
import type { ResolvedTheme } from "./themeState"

const sansStack = '-apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Helvetica Neue", "Noto Sans CJK SC", sans-serif'
const serifStack = '"New York", "Songti SC", "STSong", "Noto Serif CJK SC", Georgia, "Times New Roman", serif'
const lineHeightValues: Record<ReaderLineSpacing, number> = { compact: 1.7, standard: 1.9, relaxed: 2.05 }

export function ReadingSettingsCard({
  value, onChange, appResolvedTheme, disabled = false, error, saveStatus,
}: {
  value: ReadingPreferences
  onChange: (next: ReadingPreferences) => void
  appResolvedTheme: ResolvedTheme
  disabled?: boolean
  error: string
  saveStatus?: { text: string; saved: boolean }
}) {
  const [systemFonts, setSystemFonts] = useState<string[]>([])
  const [fontsLoaded, setFontsLoaded] = useState(false)

  useEffect(() => {
    let active = true
    listSystemFonts().then(
      fonts => {
        if (!active) return
        setSystemFonts(fonts.filter(isValidCustomFont))
        setFontsLoaded(true)
      },
      () => { if (active) setFontsLoaded(true) },
    )
    return () => { active = false }
  }, [])

  const { recommended, others } = useMemo(() => {
    const installed = new Set(systemFonts)
    const rec = systemFonts.length
      ? recommendedSystemFonts.filter(name => installed.has(name))
      : [...recommendedSystemFonts]
    const recSet = new Set(rec)
    const rest = systemFonts.filter(name => !recSet.has(name))
    return { recommended: rec, others: rest }
  }, [systemFonts])

  const effectiveTheme = resolveEffectiveReaderTheme(value.theme, appResolvedTheme)
  const previewFont = value.fontFamily === "serif" ? serifStack
    : value.fontFamily === "custom" && isValidCustomFont(value.customFont)
      ? `"${value.customFont}", ${sansStack}`
    : sansStack
  const isDefault = readingPreferencesEqual(value, defaultReadingPreferences())

  function patch(partial: Partial<ReadingPreferences>) {
    if (disabled) return
    const next = { ...value, ...partial }
    if (next.fontFamily === "custom" && !next.customFont) {
      next.customFont = recommended[0] ?? others[0] ?? "PingFang SC"
    }
    onChange(next)
  }

  const themeOptions: ReadonlyArray<{ id: ReaderThemePreference; label: string }> = [
    { id: "auto", label: tr("跟随应用") },
    { id: "light", label: tr("浅色") },
    { id: "sepia", label: tr("暖纸") },
    { id: "dark", label: tr("深色") },
  ]
  const fontOptions: ReadonlyArray<{ id: ReaderFontFamily; label: string }> = [
    { id: "sans", label: tr("黑体") },
    { id: "serif", label: tr("宋体") },
    { id: "custom", label: tr("系统字体") },
  ]
  const widthOptions: ReadonlyArray<{ id: ReaderLineWidth; label: string }> = [
    { id: "narrow", label: tr("窄") },
    { id: "standard", label: tr("标准") },
    { id: "wide", label: tr("宽") },
  ]
  const spacingOptions: ReadonlyArray<{ id: ReaderLineSpacing; label: string }> = [
    { id: "compact", label: tr("紧凑") },
    { id: "standard", label: tr("标准") },
    { id: "relaxed", label: tr("宽松") },
  ]
  const tabOptions: ReadonlyArray<{ id: ReaderDefaultTab; label: string }> = [
    { id: "transcript", label: tr("正文") },
    { id: "summary", label: tr("摘要") },
  ]

  const currentCustomFont = value.customFont || recommended[0] || others[0] || "PingFang SC"
  const customFontInList = recommended.includes(currentCustomFont) || others.includes(currentCustomFont)

  return <section className="settings-card" aria-labelledby="settings-reading-heading">
    <header className="settings-card-header">
      <h2 id="settings-reading-heading">{tr("阅读与排版")}</h2>
      <div className="settings-card-header-actions">
        {saveStatus?.text && (
          <span className="settings-auto-save" role="status" data-state={saveStatus.saved ? "saved" : undefined}>
            {saveStatus.text}
          </span>
        )}
        <button className="text-button" type="button" disabled={disabled || isDefault}
          onClick={() => onChange(defaultReadingPreferences())}>
          <RotateCcw size={13} aria-hidden="true" />{tr("恢复默认排版")}
        </button>
      </div>
    </header>
    <div className="settings-card-body">
      <div className="reading-preview-card" data-preview-theme={effectiveTheme} data-preview-width={value.lineWidth} aria-label={tr("排版效果预览")}>
        <div className="reading-preview-inner">
          <div className={`reading-preview-hero${value.showCover ? " has-cover" : ""}`}>
            {value.showCover && <span className="reading-preview-cover" aria-hidden="true" />}
            <span className="reading-preview-title">{tr("文章阅读效果预览")}</span>
          </div>
          <p className="reading-preview-sample" style={{
            fontFamily: previewFont,
            fontSize: `${value.fontSize}px`,
            lineHeight: lineHeightValues[value.lineSpacing],
          }}>
            {tr("归档仅保留纯数据 JSON，打开阅读、在浏览器查看和导出 HTML 时都会按此设置实时渲染。")}
            <strong className={value.showHighlights ? "reading-preview-highlight" : "reading-preview-plain"}>
              {tr("重点原话会在此处高亮展示。")}
            </strong>
          </p>
        </div>
      </div>

      <div className="settings-field">
        <span className="settings-label" id="reading-theme-label">{tr("阅读主题")}</span>
        <div className="settings-field-control">
          <div className="settings-segmented reading-segmented-wide" role="radiogroup" aria-labelledby="reading-theme-label" id="reading-theme">
            {themeOptions.map(option => {
              const selected = value.theme === option.id
              return <label key={option.id} className={[selected ? "is-selected" : "", disabled ? "is-disabled" : ""].filter(Boolean).join(" ")}>
                <input type="radio" name="reading-theme" value={option.id} checked={selected} disabled={disabled}
                  onChange={() => patch({ theme: option.id })} />
                <span>{option.label}</span>
              </label>
            })}
          </div>
        </div>
      </div>

      <div className="settings-field">
        <span className="settings-label" id="reading-font-label">{tr("正文字体")}</span>
        <div className="settings-field-control">
          <div className="settings-segmented" role="radiogroup" aria-labelledby="reading-font-label" id="reading-font-family">
            {fontOptions.map(option => {
              const selected = value.fontFamily === option.id
              return <label key={option.id} className={[selected ? "is-selected" : "", disabled ? "is-disabled" : ""].filter(Boolean).join(" ")}>
                <input type="radio" name="reading-font-family" value={option.id} checked={selected} disabled={disabled}
                  onChange={() => patch({ fontFamily: option.id })} />
                <span>{option.label}</span>
              </label>
            })}
          </div>
        </div>
      </div>

      {value.fontFamily === "custom" && (
        <label className="settings-field">
          <span className="settings-label">{tr("系统字体")}</span>
          <span className="settings-select settings-field-control">
            <select id="reading-custom-font" value={currentCustomFont} disabled={disabled}
              onChange={event => patch({ fontFamily: "custom", customFont: event.target.value })}>
              {!customFontInList && <option value={currentCustomFont}>{currentCustomFont}</option>}
              {recommended.length > 0 && <optgroup label={tr("推荐阅读字体")}>
                {recommended.map(font => <option key={`rec-${font}`} value={font}>{font}</option>)}
              </optgroup>}
              {others.length > 0 && <optgroup label={tr("全部已安装字体")}>
                {others.map(font => <option key={font} value={font}>{font}</option>)}
              </optgroup>}
            </select>
            <ChevronDown size={15} aria-hidden="true" />
          </span>
          <small className="settings-field-hint">
            {fontsLoaded
              ? tr("仅使用本机已安装字体，完全离线；导出 HTML 在其他设备缺少该字体时会自动回退到系统字体。")
              : tr("正在读取本机已安装字体…")}
          </small>
        </label>
      )}

      <div className="settings-field">
        <span className="settings-label" id="reading-size-label">{tr("正文字号")}</span>
        <div className="settings-field-control reading-size-row" role="group" aria-labelledby="reading-size-label">
          <button className="secondary reading-size-step" type="button"
            disabled={disabled || value.fontSize <= minReaderFontSize}
            aria-label={tr("减小字号")}
            onClick={() => patch({ fontSize: Math.max(minReaderFontSize, value.fontSize - 1) })}>A−</button>
          <input className="reading-size-slider" type="range" min={minReaderFontSize} max={maxReaderFontSize} step={1}
            value={value.fontSize} disabled={disabled} aria-label={tr("正文字号")}
            onChange={event => patch({ fontSize: Number(event.target.value) })} />
          <button className="secondary reading-size-step" type="button"
            disabled={disabled || value.fontSize >= maxReaderFontSize}
            aria-label={tr("增大字号")}
            onClick={() => patch({ fontSize: Math.min(maxReaderFontSize, value.fontSize + 1) })}>A+</button>
          <span className="reading-size-value" aria-live="polite">{value.fontSize} px</span>
        </div>
      </div>

      <div className="settings-field">
        <span className="settings-label" id="reading-width-label">{tr("内容栏宽度")}</span>
        <div className="settings-field-control">
          <div className="settings-segmented" role="radiogroup" aria-labelledby="reading-width-label" id="reading-line-width">
            {widthOptions.map(option => {
              const selected = value.lineWidth === option.id
              return <label key={option.id} className={[selected ? "is-selected" : "", disabled ? "is-disabled" : ""].filter(Boolean).join(" ")}>
                <input type="radio" name="reading-line-width" value={option.id} checked={selected} disabled={disabled}
                  onChange={() => patch({ lineWidth: option.id })} />
                <span>{option.label}</span>
              </label>
            })}
          </div>
        </div>
      </div>

      <div className="settings-field">
        <span className="settings-label" id="reading-spacing-label">{tr("行距")}</span>
        <div className="settings-field-control">
          <div className="settings-segmented" role="radiogroup" aria-labelledby="reading-spacing-label" id="reading-line-spacing">
            {spacingOptions.map(option => {
              const selected = value.lineSpacing === option.id
              return <label key={option.id} className={[selected ? "is-selected" : "", disabled ? "is-disabled" : ""].filter(Boolean).join(" ")}>
                <input type="radio" name="reading-line-spacing" value={option.id} checked={selected} disabled={disabled}
                  onChange={() => patch({ lineSpacing: option.id })} />
                <span>{option.label}</span>
              </label>
            })}
          </div>
        </div>
      </div>

      <div className="settings-field">
        <span className="settings-label" id="reading-tab-label">{tr("默认打开页")}</span>
        <div className="settings-field-control">
          <div className="settings-segmented" role="radiogroup" aria-labelledby="reading-tab-label" id="reading-default-tab">
            {tabOptions.map(option => {
              const selected = value.defaultTab === option.id
              return <label key={option.id} className={[selected ? "is-selected" : "", disabled ? "is-disabled" : ""].filter(Boolean).join(" ")}>
                <input type="radio" name="reading-default-tab" value={option.id} checked={selected} disabled={disabled}
                  onChange={() => patch({ defaultTab: option.id })} />
                <span>{option.label}</span>
              </label>
            })}
          </div>
        </div>
        <small className="settings-field-hint">{tr("首次打开未读文章时生效；已有阅读进度的文章仍会回到上次位置。")}</small>
      </div>

      <label className="settings-field reading-toggle-field">
        <span className="settings-label">{tr("显示文章封面")}</span>
        <div className="settings-field-control reading-toggle-control">
          <input className="settings-toggle" id="reading-show-cover" type="checkbox" checked={value.showCover} disabled={disabled}
            onChange={event => patch({ showCover: event.target.checked })} />
          <span>{tr("在阅读页标题旁显示来源封面图")}</span>
        </div>
      </label>

      <label className="settings-field reading-toggle-field">
        <span className="settings-label">{tr("正文重点标红")}</span>
        <div className="settings-field-control reading-toggle-control">
          <input className="settings-toggle" id="reading-show-highlights" type="checkbox" checked={value.showHighlights} disabled={disabled}
            onChange={event => patch({ showHighlights: event.target.checked })} />
          <span>{tr("保留正文中的加粗与重点标红标注")}</span>
        </div>
      </label>

      {error && <p className="error-line" role="alert">{error}</p>}
    </div>
  </section>
}
