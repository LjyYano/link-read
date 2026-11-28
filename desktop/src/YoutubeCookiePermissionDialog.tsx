import { useEffect, useId, useState } from "react"
import { ExternalLink, FileText, KeyRound, LoaderCircle, RotateCw, ShieldAlert, X } from "lucide-react"
import { openPrivacySettings } from "./api"
import { useArticleDialog } from "./articleDialog"
import { tr } from "./language"
import type { YoutubeCookiePermissionKind } from "./youtubeGuidance"
import "./article-dialog.css"

export function YoutubeCookiePermissionDialog({
  kind,
  browserLabel,
  isDev,
  testing,
  onRetry,
  onUseCookiesFile,
  onClose,
}: {
  kind: YoutubeCookiePermissionKind
  browserLabel: string
  isDev: boolean
  testing: boolean
  onRetry: () => void
  onUseCookiesFile: () => void
  onClose: () => void
}) {
  const id = useId()
  const dialog = useArticleDialog()
  const [openingSettings, setOpeningSettings] = useState(false)
  const [openedSettings, setOpenedSettings] = useState(false)
  const [openError, setOpenError] = useState("")

  useEffect(() => {
    setOpenedSettings(false)
    setOpenError("")
  }, [kind, browserLabel])

  const isKeychain = kind === "keychain"
  const isSafari = kind === "safari_full_disk_access"

  const title = isKeychain
    ? tr("需要允许访问 macOS 钥匙串")
    : isSafari
    ? tr("需要授权读取 Safari Cookie")
    : tr("需要授权读取 {0} 的 Cookie", browserLabel)

  const summary = isKeychain
    ? tr("读取 {0} 的加密 Cookie 时，macOS 钥匙串拒绝了访问请求。重新测试时系统会再次弹出钥匙串密码窗口。", browserLabel)
    : isSafari
    ? tr("macOS 默认禁止其他应用访问 Safari 的容器目录，需要在系统设置中开启完全磁盘访问权限，或改用其他浏览器 / cookies.txt。")
    : tr("macOS 限制应用直接读取 {0} 的本地 Cookie 数据，无法由应用内直接弹窗授权，需要在系统设置中手动开启权限。", browserLabel)

  const steps = isKeychain
    ? [
        tr("点击下方「重新测试」，等待 macOS 弹出钥匙串访问请求窗口。"),
        tr("输入你的 Mac 开机登录密码（非 Apple ID 密码）。"),
        tr("点击「始终允许」（推荐）或「允许」，之后无需每次重复确认。"),
      ]
    : [
        tr("点击下方「打开系统设置」，进入「隐私与安全性 → 完全磁盘访问权限」。"),
        isDev
          ? tr("开启当前运行 dev.sh 的终端或 IDE（如 Terminal、iTerm、Cursor）开关；开启后需重启该终端。")
          : tr("在列表中开启 LinkRead（若未出现可点左下角「+」从「应用程序」中添加），并在提示时重新打开应用。"),
        tr("授权完成后，回到此处点击「重新测试」。"),
      ]

  const alternativeNote = isSafari
    ? tr("不想修改系统权限？也可以改用 Chrome 等浏览器，或直接导入 cookies.txt 文件。")
    : tr("不想修改系统权限？也可以改用导出的 cookies.txt 文件，无需磁盘或钥匙串授权。")

  async function handleOpenSettings() {
    if (openingSettings) return
    setOpeningSettings(true)
    setOpenError("")
    try {
      await openPrivacySettings("full_disk_access")
      setOpenedSettings(true)
    } catch (error) {
      setOpenError(error instanceof Error ? error.message : String(error))
    } finally {
      setOpeningSettings(false)
    }
  }

  return (
    <dialog
      ref={dialog}
      className="article-dialog cookie-permission-dialog"
      aria-labelledby={`${id}-title`}
      onCancel={event => {
        event.preventDefault()
        event.stopPropagation()
        if (!testing) onClose()
      }}
      onKeyDown={event => {
        if (event.key === "Escape") {
          event.stopPropagation()
          if (event.repeat || event.nativeEvent.isComposing || testing) event.preventDefault()
          else onClose()
        }
      }}
      onClick={event => {
        if (testing || event.target !== event.currentTarget) return
        const bounds = event.currentTarget.getBoundingClientRect()
        if (event.clientX < bounds.left || event.clientX > bounds.right
          || event.clientY < bounds.top || event.clientY > bounds.bottom) {
          onClose()
        }
      }}
    >
      <header>
        <div className="cookie-permission-heading">
          <span className="cookie-permission-badge" aria-hidden="true">
            {isKeychain ? <KeyRound size={18} /> : <ShieldAlert size={18} />}
          </span>
          <div>
            <h2 id={`${id}-title`}>{title}</h2>
            <p className="article-dialog-subtitle">{summary}</p>
          </div>
        </div>
        <button className="icon-button" type="button" aria-label={tr("关闭")} disabled={testing} onClick={onClose}>
          <X size={18} aria-hidden="true" />
        </button>
      </header>
      <div className="article-dialog-body">
        <ol className="permission-steps">
          {steps.map((step, index) => (
            <li key={index}>
              <span className="permission-step-index" aria-hidden="true">{index + 1}</span>
              <span>{step}</span>
            </li>
          ))}
        </ol>
        <p className="permission-note">{alternativeNote}</p>
        {openedSettings && !openError && (
          <p className="permission-opened-hint" role="status">
            {tr("已打开系统设置，授权完成后请点击「重新测试」。")}
          </p>
        )}
        {openError && <p className="error-line" role="alert">{openError}</p>}
      </div>
      <footer className="cookie-permission-footer">
        <button type="button" className="text-button cookie-permission-alt" disabled={testing} onClick={onUseCookiesFile}>
          <FileText size={14} aria-hidden="true" />
          {tr("改用 cookies.txt")}
        </button>
        <div className="cookie-permission-actions">
          {isKeychain ? (
            <>
              <button type="button" className="secondary" disabled={testing} onClick={onClose}>
                {tr("关闭")}
              </button>
              <button type="button" className="primary" disabled={testing} onClick={onRetry}>
                {testing
                  ? <LoaderCircle size={14} className="spin" aria-hidden="true" />
                  : <RotateCw size={14} aria-hidden="true" />}
                {testing ? tr("读取中…") : tr("重新测试")}
              </button>
            </>
          ) : (
            <>
              <button type="button" className="secondary" disabled={testing} onClick={onRetry}>
                {testing
                  ? <LoaderCircle size={14} className="spin" aria-hidden="true" />
                  : <RotateCw size={14} aria-hidden="true" />}
                {testing ? tr("读取中…") : tr("重新测试")}
              </button>
              <button type="button" className="primary" disabled={openingSettings || testing}
                onClick={() => void handleOpenSettings()}>
                {openingSettings
                  ? <LoaderCircle size={14} className="spin" aria-hidden="true" />
                  : <ExternalLink size={14} aria-hidden="true" />}
                {openingSettings ? tr("正在打开…") : tr("打开系统设置")}
              </button>
            </>
          )}
        </div>
      </footer>
    </dialog>
  )
}
