import { ChevronDown, Monitor, Moon, Sun } from "lucide-react"
import { resolveLocale, tr, type InterfaceLanguage, type LanguageSettings, type PageLanguage } from "./language.ts"
import type { ThemeMode } from "./themeState"

export function LanguageSettingsCard({ value, onChange, theme, onThemeChange, disabled = false, error }: {
  value: LanguageSettings
  onChange: (value: LanguageSettings) => void
  theme: ThemeMode
  onThemeChange: (theme: ThemeMode) => void
  disabled?: boolean
  error: string
}) {
  const system = resolveLocale("system") === "zh-CN" ? "简体中文" : "English"
  const themeOptions: ReadonlyArray<{ id: ThemeMode; label: string; icon: typeof Monitor }> = [
    { id: "system", label: tr("跟随系统"), icon: Monitor },
    { id: "light", label: tr("浅色"), icon: Sun },
    { id: "dark", label: tr("深色"), icon: Moon },
  ]
  return <section className="settings-card" aria-labelledby="settings-language-heading">
    <header className="settings-card-header"><h2 id="settings-language-heading">{tr("语言与外观")}</h2></header>
    <div className="settings-card-body">
      <div className="settings-field">
        <span className="settings-label" id="settings-theme-label">{tr("界面外观")}</span>
        <div className="settings-field-control">
          <div className="settings-segmented" role="radiogroup" aria-labelledby="settings-theme-label" id="settings-theme-mode">
            {themeOptions.map(option => {
              const Icon = option.icon
              const selected = theme === option.id
              return (
                <label key={option.id} className={[selected ? "is-selected" : "", disabled ? "is-disabled" : ""].filter(Boolean).join(" ")}>
                  <input type="radio" name="settings-theme" value={option.id} checked={selected} disabled={disabled}
                    onChange={() => onThemeChange(option.id)} />
                  <Icon size={14} aria-hidden="true" />
                  <span>{option.label}</span>
                </label>
              )
            })}
          </div>
        </div>
      </div>
      <label className="settings-field">
        <span className="settings-label">{tr("界面语言")}</span>
        <span className="settings-select settings-field-control">
          <select value={value.interfaceLanguage} disabled={disabled}
            onChange={event => onChange({ ...value, interfaceLanguage: event.target.value as InterfaceLanguage })}>
            <option value="system">{tr("跟随系统（{0}）", system)}</option>
            <option value="zh-CN" lang="zh-CN">简体中文</option>
            <option value="en" lang="en">English</option>
          </select>
          <ChevronDown size={15} aria-hidden="true" />
        </span>
      </label>
      <label className="settings-field">
        <span className="settings-label">{tr("生成网页语言")}</span>
        <span className="settings-select settings-field-control">
          <select value={value.pageLanguage} disabled={disabled}
            onChange={event => onChange({ ...value, pageLanguage: event.target.value as PageLanguage })}>
            <option value="original">{tr("原字幕语言")}</option>
            <option value="zh-CN" lang="zh-CN">简体中文</option>
            <option value="en" lang="en">English</option>
          </select>
          <ChevronDown size={15} aria-hidden="true" />
        </span>
      </label>
      {error && <p className="error-line" role="alert">{error}</p>}
    </div>
  </section>
}
