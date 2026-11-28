import { tr } from "./language.ts"
import { useEffect, useRef, useState } from "react"
import { ChevronDown, FolderOpen, LoaderCircle, X } from "lucide-react"
import { pickCookiesFile, testYoutubeCookies } from "./api"
import { ConnectionRevision } from "./connectionState"
import { SettingsHelp } from "./SettingsHelp"
import { youtubeBrowsers, type YoutubeBrowser, type YoutubeCookieMode, type YoutubeCookieOptions } from "./youtubeCookies"

export function YoutubeCookiesPanel({ value, onChange, disabled }: {
  value: YoutubeCookieOptions
  onChange: (value: YoutubeCookieOptions) => void
  disabled: boolean
}) {
  const [picking, setPicking] = useState(false)
  const [pickError, setPickError] = useState("")
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string }>()
  const revision = useRef(new ConnectionRevision())
  const identityRef = useRef("")
  const testIdentity = useRef("")
  const mounted = useRef(true)
  const currentValue = useRef(value)
  currentValue.current = value
  const currentDisabled = useRef(disabled)
  currentDisabled.current = disabled
  const pickRevision = useRef(0)
  const identity = JSON.stringify([value.mode, value.browser, value.filePath.trim()])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      pickRevision.current++
      revision.current.invalidate()
    }
  }, [])

  if (identityRef.current !== identity) {
    identityRef.current = identity
    revision.current.invalidate()
  }
  const currentResult = testIdentity.current === identity ? testResult : undefined
  const canTest = !disabled && !testing && (
    value.mode === "browser" || (value.mode === "file" && Boolean(value.filePath.trim()))
  )

  async function chooseFile() {
    if (disabled || picking) return
    const snapshot = currentValue.current
    const rev = ++pickRevision.current
    setPicking(true)
    setPickError("")
    try {
      const selected = await pickCookiesFile()
      if (selected && mounted.current && !currentDisabled.current
        && pickRevision.current === rev && currentValue.current === snapshot) {
        onChange({ ...snapshot, mode: "file", filePath: selected })
      }
    } catch (error) {
      if (mounted.current && pickRevision.current === rev) setPickError(String(error))
    } finally {
      if (mounted.current) setPicking(false)
    }
  }

  async function runTest() {
    if (!canTest) return
    setTesting(true)
    setTestResult(undefined)
    const token = revision.current.snapshot()
    testIdentity.current = identity
    try {
      const result = await testYoutubeCookies({
        ...value,
        filePath: value.filePath.trim(),
      })
      if (mounted.current && revision.current.accepts(token)) setTestResult(result)
    } catch (error) {
      if (mounted.current && revision.current.accepts(token)) {
        setTestResult({ ok: false, message: error instanceof Error ? error.message : String(error) })
      }
    } finally {
      if (mounted.current) setTesting(false)
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
              {value.browser === "safari"
                ? tr("macOS 默认禁止读取 Safari Cookie，需在「系统设置 → 隐私与安全性 → 完全磁盘访问权限」授权应用（或运行 dev.sh 的终端）；推荐优先使用 Chrome 或 cookies.txt。")
                : tr("请确保该浏览器已登录 YouTube；首次读取时 macOS 可能会弹窗请求钥匙串或磁盘访问权限。")}
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
      {value.mode !== "none" && <>
        <div className="settings-card-footer settings-connect-row">
          <button className={currentResult?.ok ? "secondary connection-ok" : "secondary"} type="button"
            disabled={!canTest} onClick={() => void runTest()}
            title={tr("仅在本地检查是否能读取到 YouTube Cookie，不上传或打印任何 Cookie 内容")}>
            {testing ? <LoaderCircle size={15} className="spin" aria-hidden="true" />
              : currentResult?.ok ? <span className="settings-green-dot" aria-hidden="true" /> : null}
            {testing ? tr("读取中…") : currentResult?.ok ? tr("已读取 YouTube Cookie") : tr("测试读取 Cookie")}
          </button>
        </div>
        {currentResult && !currentResult.ok && <p className="settings-test-error" role="alert">{currentResult.message}</p>}
      </>}
    </div>
  </section>
}
