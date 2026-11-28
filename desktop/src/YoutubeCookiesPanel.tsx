import { tr } from "./language.ts"
import { useEffect, useRef, useState, type ReactNode } from "react"
import { ChevronDown, ExternalLink, FolderOpen, LoaderCircle, ShieldAlert, X } from "lucide-react"
import { openPrivacySettings, pickCookiesFile, testYoutubeCookies, type YoutubeCookieTestResult } from "./api"
import { ConnectionRevision } from "./connectionState"
import { SettingsHelp } from "./SettingsHelp"
import { YoutubeCookiePermissionDialog } from "./YoutubeCookiePermissionDialog"
import { youtubeBrowsers, type YoutubeBrowser, type YoutubeCookieMode, type YoutubeCookieOptions } from "./youtubeCookies"
import { resolveYoutubeCookiePermissionKind } from "./youtubeGuidance"

export function YoutubeCookiesPanel({ value, onChange, disabled, footer }: {
  value: YoutubeCookieOptions
  onChange: (value: YoutubeCookieOptions) => void
  disabled: boolean
  footer?: ReactNode
}) {
  const [picking, setPicking] = useState(false)
  const [pickError, setPickError] = useState("")
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<YoutubeCookieTestResult>()
  const [permissionDialogOpen, setPermissionDialogOpen] = useState(false)
  const [openingSettings, setOpeningSettings] = useState(false)
  const [settingsOpenError, setSettingsOpenError] = useState("")
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

  useEffect(() => {
    setPermissionDialogOpen(false)
    setSettingsOpenError("")
  }, [identity])

  if (identityRef.current !== identity) {
    identityRef.current = identity
    revision.current.invalidate()
  }
  const currentResult = testIdentity.current === identity ? testResult : undefined
  const permissionKind = currentResult && !currentResult.ok
    ? resolveYoutubeCookiePermissionKind(currentResult.code)
    : null
  const browserLabel = youtubeBrowsers.find(item => item.id === value.browser)?.label ?? tr("所选浏览器")
  const canTest = !disabled && !testing && (
    value.mode === "browser" || (value.mode === "file" && Boolean(value.filePath.trim()))
  )

  async function chooseFile(nextBase?: YoutubeCookieOptions) {
    if (disabled || picking) return
    const snapshot = nextBase ?? currentValue.current
    const rev = ++pickRevision.current
    setPicking(true)
    setPickError("")
    try {
      const selected = await pickCookiesFile()
      if (selected && mounted.current && !currentDisabled.current
        && pickRevision.current === rev
        && (nextBase !== undefined || currentValue.current === snapshot)) {
        onChange({ ...snapshot, mode: "file", filePath: selected })
      }
    } catch (error) {
      if (mounted.current && pickRevision.current === rev) setPickError(String(error))
    } finally {
      if (mounted.current) setPicking(false)
    }
  }

  function handleUseCookiesFile() {
    setPermissionDialogOpen(false)
    setSettingsOpenError("")
    const nextValue: YoutubeCookieOptions = { ...currentValue.current, mode: "file" }
    onChange(nextValue)
    void chooseFile(nextValue)
  }

  async function handleOpenPrivacySettings() {
    if (openingSettings) return
    setOpeningSettings(true)
    setSettingsOpenError("")
    try {
      await openPrivacySettings("full_disk_access")
    } catch (error) {
      if (mounted.current) setSettingsOpenError(error instanceof Error ? error.message : String(error))
    } finally {
      if (mounted.current) setOpeningSettings(false)
    }
  }

  async function runTest() {
    if (!canTest) return
    setTesting(true)
    setSettingsOpenError("")
    const token = revision.current.snapshot()
    testIdentity.current = identity
    try {
      const result = await testYoutubeCookies({
        ...value,
        filePath: value.filePath.trim(),
      })
      if (mounted.current && revision.current.accepts(token)) {
        setTestResult(result)
        const nextPermissionKind = !result.ok ? resolveYoutubeCookiePermissionKind(result.code) : null
        setPermissionDialogOpen(Boolean(nextPermissionKind))
      }
    } catch (error) {
      if (mounted.current && revision.current.accepts(token)) {
        setTestResult({ ok: false, message: error instanceof Error ? error.message : String(error) })
        setPermissionDialogOpen(false)
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
        <span className="settings-label" id="youtube-cookie-mode-label">{tr("认证方式")}</span>
        <div className="settings-field-control">
          <div className="settings-segmented" role="radiogroup" aria-labelledby="youtube-cookie-mode-label" id="youtube-cookie-mode" tabIndex={-1}>
            {([
              { id: "none", label: tr("不使用") },
              { id: "browser", label: tr("本机浏览器") },
              { id: "file", label: "cookies.txt" },
            ] as ReadonlyArray<{ id: YoutubeCookieMode; label: string }>).map(option => {
              const selected = value.mode === option.id
              return (
                <label key={option.id} className={[selected ? "is-selected" : "", disabled ? "is-disabled" : ""].filter(Boolean).join(" ")}>
                  <input type="radio" name="youtube-cookie-mode" value={option.id} checked={selected} disabled={disabled}
                    onChange={() => {
                      setPickError("")
                      onChange({ ...value, mode: option.id })
                    }} />
                  <span>{option.label}</span>
                </label>
              )
            })}
          </div>
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
            <div className="settings-path-row">
              <input id="youtube-cookie-file" type="text" value={value.filePath} disabled={disabled}
                placeholder={tr("选择 Netscape 格式的 cookies.txt 文件")} autoComplete="off" spellCheck={false}
                onChange={event => {
                  setPickError("")
                  onChange({ ...value, filePath: event.target.value })
                }} />
              <button type="button" className="secondary" disabled={disabled || picking} onClick={() => void chooseFile()}>
                <FolderOpen size={15} aria-hidden="true" /> {tr("选择…")}
              </button>
              {value.filePath && <button type="button" className="icon-button" disabled={disabled || picking}
                aria-label={tr("清除")} title={tr("清除")}
                onClick={() => onChange({ ...value, filePath: "" })}>
                <X size={15} aria-hidden="true" />
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
        {currentResult && !currentResult.ok && (
          <div className="settings-test-error-block" role="alert">
            <p className="settings-test-error">{currentResult.message}</p>
            {permissionKind && (
              <div className="settings-test-error-actions">
                {permissionKind !== "keychain" && (
                  <button type="button" className="secondary" disabled={openingSettings || testing}
                    onClick={() => void handleOpenPrivacySettings()}>
                    {openingSettings
                      ? <LoaderCircle size={13} className="spin" aria-hidden="true" />
                      : <ExternalLink size={13} aria-hidden="true" />}
                    {openingSettings ? tr("正在打开…") : tr("打开系统设置")}
                  </button>
                )}
                <button type="button" className="text-button" disabled={testing}
                  onClick={() => setPermissionDialogOpen(true)}>
                  <ShieldAlert size={13} aria-hidden="true" />
                  {tr("查看授权指引…")}
                </button>
              </div>
            )}
            {settingsOpenError && <p className="settings-test-error">{settingsOpenError}</p>}
          </div>
        )}
      </>}
    </div>
    {footer}
    {permissionDialogOpen && permissionKind && (
      <YoutubeCookiePermissionDialog
        kind={permissionKind}
        browserLabel={browserLabel}
        isDev={Boolean(currentResult?.isDev)}
        testing={testing}
        onRetry={() => void runTest()}
        onUseCookiesFile={handleUseCookiesFile}
        onClose={() => setPermissionDialogOpen(false)}
      />
    )}
  </section>
}
