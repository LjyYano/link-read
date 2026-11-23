import { ChevronDown } from "lucide-react"
import { resolveLocale, tr, type InterfaceLanguage, type LanguageSettings, type PageLanguage } from "./language.ts"

export function LanguageSettingsCard({ value, onChange, error }: {
  value: LanguageSettings
  onChange: (value: LanguageSettings) => void
  error: string
}) {
  const system = resolveLocale("system") === "zh-CN" ? "简体中文" : "English"
  return <section className="settings-card" aria-labelledby="settings-language-heading">
    <header className="settings-card-header"><h2 id="settings-language-heading">{tr("语言")}</h2></header>
    <div className="settings-card-body">
      <label className="settings-field">
        <span className="settings-label">{tr("界面语言")}</span>
        <span className="settings-select settings-field-control">
          <select value={value.interfaceLanguage}
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
          <select value={value.pageLanguage}
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
