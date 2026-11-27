import { useId } from "react"
import { AudioLines, FileUp, KeyRound, X } from "lucide-react"
import { useArticleDialog } from "./articleDialog"
import { localizedDetail, tr } from "./language"
import type { YoutubeSubtitlePrompt } from "./youtubeGuidance"
import "./article-dialog.css"

export function YoutubeSubtitleDialog({
  prompt,
  onConfigureCookies,
  onConfigureAsr,
  onImportSubtitles,
  onClose,
}: {
  prompt: YoutubeSubtitlePrompt
  onConfigureCookies: () => void
  onConfigureAsr: () => void
  onImportSubtitles: () => void
  onClose: () => void
}) {
  const id = useId()
  const dialog = useArticleDialog()
  const isAsrOffer = prompt.kind === "offer_asr"
  const title = prompt.kind === "configure_cookies"
    ? tr("请先在设置中配置 YouTube Cookie")
    : prompt.kind === "fix_cookies"
    ? tr("YouTube Cookie 验证未通过")
    : tr("未找到可下载的 YouTube 字幕")
  const body = prompt.kind === "configure_cookies"
    ? tr("LinkRead 会优先下载 YouTube 字幕。当前尚未配置 YouTube Cookie，无法继续获取该视频的字幕。请先前往设置选择已登录 YouTube 的浏览器或导入 cookies.txt；如果配置好 Cookie 后仍无法下载字幕，再使用语音转写。")
    : prompt.kind === "fix_cookies"
    ? tr("当前配置的 YouTube Cookie 无法读取或已失效，请前往设置检查浏览器权限、确认已登录 YouTube，或重新选择 cookies.txt 文件。")
    : tr("已使用你配置的 YouTube Cookie 检查该视频，但仍未找到可下载的字幕。现在可以在设置中开启语音识别转写音频，或导入本地字幕文件。")

  return <dialog ref={dialog} className="article-dialog" aria-labelledby={`${id}-title`}
    onCancel={event => { event.preventDefault(); event.stopPropagation(); onClose() }}
    onKeyDown={event => {
      if (event.key === "Escape") {
        event.stopPropagation()
        if (event.repeat || event.nativeEvent.isComposing) event.preventDefault()
      }
    }}>
    <header>
      <h2 id={`${id}-title`}>{title}</h2>
      <button className="icon-button" type="button" aria-label={tr("关闭")} onClick={onClose}>
        <X size={18} aria-hidden="true" />
      </button>
    </header>
    <div className="article-dialog-body">
      <p className="article-dialog-subtitle">{body}</p>
      {prompt.detail && <p className="article-source-feedback" role="status">
        {localizedDetail(prompt.detail, title)}
      </p>}
    </div>
    <footer>
      <button type="button" className="secondary" onClick={onClose}>{tr("稍后再说")}</button>
      <button type="button" className="secondary" onClick={onImportSubtitles}>
        <FileUp size={14} aria-hidden="true" />{tr("导入字幕")}
      </button>
      {isAsrOffer ? (
        <button type="button" className="primary" onClick={onConfigureAsr}>
          <AudioLines size={14} aria-hidden="true" />{tr("设置语音识别")}
        </button>
      ) : (
        <button type="button" className="primary" onClick={onConfigureCookies}>
          <KeyRound size={14} aria-hidden="true" />{tr("前往设置配置 Cookie")}
        </button>
      )}
    </footer>
  </dialog>
}
