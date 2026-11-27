import { tr } from "./language.ts"
import { useState } from "react"
import { ChevronDown, FolderOpen, X } from "lucide-react"
import { pickCookiesFile } from "./api"
import { SettingsHelp } from "./SettingsHelp"
import { youtubeBrowsers, type YoutubeBrowser, type YoutubeCookieMode, type YoutubeCookieOptions } from "./youtubeCookies"

export function YoutubeCookiesPanel({ value, onChange, disabled }: {
  value: YoutubeCookieOptions
  onChange: (value: YoutubeCookieOptions) => void
  disabled: boolean
}) {
  const [picking, setPicking] = useState(false)
  const [pickError, setPickError] = useState("")

  async function chooseFile() {
    if (disabled || picking) return
    setPicking(true)
    setPickError("")
    try {
      const selected = await pickCookiesFile()
      if (selected) onChange({ ...value, mode: "file", filePath: selected })
    } catch (error) {
      setPickError(String(error))
    } finally {
      setPicking(false)
    }
  }

  return <section className="settings-card" aria-labelledby="youtube-cookies-heading">
    <header className="settings-card-header">
      <div className="settings-card-title">
        <h2 id="youtube-cookies-heading">{tr("YouTube 认证")}</h2>
        <SettingsHelp label={tr("YouTube 认证说明")}>
          <p>{tr("默认不使用任何浏览器会话。仅当 YouTube 要求登录验证或触发反机器人风控时，可在此显式启用 Cookie 认证。")}</p>
          <p>{tr("应用不会保存、上传或打印 Cookie 内容；程序仅在下载 YouTube 字幕时按你的选择读取认证信息。")}</p>
        </SettingsHelp>
      </div>
    </header>
    <div className="settings-card-body">
      <div className="settings-field">
        <label className="settings-label" htmlFor="youtube-cookie-mode">{tr("认证方式")}</label>
        <div className="settings-field-control">
          <span className="settings-select">
            <select id="youtube-cookie-mode" value={value.mode} disabled={disabled}
              onChange={event => {
                setPickError("")
                onChange({ ...value, mode: event.target.value as YoutubeCookieMode })
              }}>
              <option value="none">{tr("不使用（默认）")}</option>
              <option value="browser">{tr("从本机浏览器读取")}</option>
              <option value="file">{tr("使用 cookies.txt 文件")}</option>
            </select>
            <ChevronDown size={15} aria-hidden="true" />
          </span>
        </div>
      </div>

      {value.mode === "browser" && <>
        <div className="settings-field">
          <label className="settings-label" htmlFor="youtube-cookie-browser">{tr("浏览器")}</label>
          <div className="settings-field-control">
            <span className="settings-select">
              <select id="youtube-cookie-browser" value={value.browser} disabled={disabled}
                onChange={event => onChange({ ...value, browser: event.target.value as YoutubeBrowser })}>
                {youtubeBrowsers.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
              </select>
              <ChevronDown size={15} aria-hidden="true" />
            </span>
            <p className="settings-field-hint">
              {tr("请确保该浏览器已登录 YouTube；首次读取时 macOS 可能会弹窗请求钥匙串或磁盘访问权限。")}
            </p>
          </div>
        </div>
      </>}

      {value.mode === "file" && <>
        <div className="settings-field">
          <label className="settings-label" htmlFor="youtube-cookie-file">{tr("Cookie 文件")}</label>
          <div className="settings-field-control">
            <input id="youtube-cookie-file" type="text" value={value.filePath} disabled={disabled}
              placeholder={tr("选择 Netscape 格式的 cookies.txt 文件")} autoComplete="off" spellCheck={false}
              onChange={event => {
                setPickError("")
                onChange({ ...value, filePath: event.target.value })
              }} />
            <div className="environment-actions" style={{ marginTop: 8 }}>
              <button type="button" className="secondary" disabled={disabled || picking} onClick={() => void chooseFile()}>
                <FolderOpen size={15} aria-hidden="true" /> {tr("选择 cookies.txt…")}
              </button>
              {value.filePath && <button type="button" className="text-button" disabled={disabled || picking}
                onClick={() => onChange({ ...value, filePath: "" })}>
                <X size={14} aria-hidden="true" /> {tr("清除")}
              </button>}
            </div>
            <p className="settings-field-hint">
              {tr("仅保存本机文件路径，不复制或上传文件内容。")}
            </p>
          </div>
        </div>
      </>}
      {pickError && <p className="settings-test-error" role="alert">{pickError}</p>}
    </div>
  </section>
}
