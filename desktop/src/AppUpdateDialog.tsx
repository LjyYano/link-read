import { useId } from "react"
import { LoaderCircle } from "lucide-react"
import { useArticleDialog } from "./articleDialog"
import { tr } from "./language"
import type { UpdatePrompt } from "./useAppUpdates"
import "./article-dialog.css"

export function AppUpdateDialog({ prompt, busy, onClose, onCheck, onInstall }: {
  prompt: UpdatePrompt; busy: boolean; onClose: () => void; onCheck: () => void; onInstall: () => void
}) {
  const titleId = useId()
  const dialog = useArticleDialog()
  const installing = prompt.status === "installing"
  const available = prompt.status === "available"
  let message: string
  switch (prompt.status) {
    case "checking": message = tr("正在检查更新…"); break
    case "checkFailed": message = tr("无法检查更新，请检查网络连接后重试。"); break
    case "unconfigured": message = tr("此安装包未启用自动更新。请安装带更新配置的正式发行版。"); break
    case "current": message = tr("LinkRead {0} 已是最新版本。", prompt.currentVersion); break
    case "available": message = tr("当前版本 {0}。安装更新后，LinkRead 将自动重新打开。", prompt.currentVersion); break
    case "installing": message = tr("正在下载、验证并安装更新…"); break
  }

  return <dialog ref={dialog} className="article-dialog" aria-labelledby={titleId}
    onCancel={event => { event.preventDefault(); event.stopPropagation(); if (!installing) onClose() }}>
    <header>
      <h2 id={titleId}>{available || installing ? tr("LinkRead {0} 可用", prompt.version) : tr("检查更新")}</h2>
    </header>
    <div className="article-dialog-body">
      <div className="article-source-feedback" role="status">
        {(installing || prompt.status === "checking") && <LoaderCircle className="spin" size={16} aria-hidden="true" />}
        {message}
      </div>
      {available && busy && <p className="article-dialog-subtitle">{tr("请等当前任务结束后再安装更新。")}</p>}
      {available && prompt.error && <p className="error-line" role="alert">{prompt.error}</p>}
    </div>
    {!installing && <footer>
      <button type="button" className={available || prompt.status === "checkFailed" ? "secondary" : "primary"} onClick={onClose}>
        {available ? tr("稍后") : tr("关闭")}
      </button>
      {prompt.status === "checkFailed" && <button type="button" className="primary" onClick={onCheck}>{tr("重试")}</button>}
      {available && <button type="button" className="primary" disabled={busy} onClick={onInstall}>{tr("下载并安装")}</button>}
    </footer>}
  </dialog>
}
