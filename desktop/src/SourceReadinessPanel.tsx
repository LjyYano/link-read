import { locale, localizedDetail, tr } from "./language.ts"
import { useState } from "react"
import { BookOpen, CircleAlert, CircleCheck, Download, ExternalLink, FileUp, Info, KeyRound, LoaderCircle, LockKeyhole, RefreshCw } from "lucide-react"
import type { DownloadedSubtitles, SubtitleExportFormat } from "./api"
import type { SourceReadiness } from "./useSourceReadiness"
import type { SubtitleDownloadState } from "./useSubtitleDownload"
import { resolveYoutubeSubtitlePrompt } from "./youtubeGuidance"
import "./source-readiness.css"

export function SourceReadinessPanel({
  state, onImport, onOpenSource, onSettings, onYoutubeSettings, asrEnabled = false,
  isYoutube = false, youtubeCookiesConfigured = false, subtitleDownload, onPreviewDownloaded, onExportDownloaded,
}: {
  state: SourceReadiness
  onImport: () => void
  onOpenSource: () => void
  onSettings?: () => void
  onYoutubeSettings?: () => void
  asrEnabled?: boolean
  isYoutube?: boolean
  youtubeCookiesConfigured?: boolean
  subtitleDownload?: SubtitleDownloadState
  onPreviewDownloaded?: (downloaded: DownloadedSubtitles) => void
  onExportDownloaded?: (previewId: string, format: SubtitleExportFormat) => Promise<void>
}) {
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState("")
  if (!state.eligible) return null
  const result = state.result
  const checking = state.phase === "checking"
  const downloading = Boolean(subtitleDownload?.downloading)
  const downloaded = subtitleDownload?.downloaded
  const downloadError = !downloaded ? subtitleDownload?.error : undefined
  const downloadFailure = !downloaded ? subtitleDownload?.failure : undefined
  const busy = checking || downloading
  const status = downloaded ? "captions" : result?.status ?? "idle"
  const subtitleLogin = !downloaded && result?.code === "subtitle_login_required"
  const youtubePrompt = !downloaded ? resolveYoutubeSubtitlePrompt({
    source: state.source,
    isYoutube,
    cookiesConfigured: youtubeCookiesConfigured,
    asrEnabled,
    code: downloadFailure?.code ?? result?.code,
    status: downloadFailure ? undefined : result?.status,
    message: downloadFailure?.message ?? result?.message,
  }) : undefined
  const needsYoutubeAuth = youtubePrompt?.kind === "configure_cookies" || youtubePrompt?.kind === "fix_cookies"
  const canSuggestAsr = !needsYoutubeAuth && !asrEnabled && (
    youtubePrompt?.kind === "offer_asr" || (!isYoutube && status === "needs_asr")
  )
  const Icon = busy ? LoaderCircle : downloadError ? CircleAlert : status === "blocked" ? LockKeyhole : status === "captions" ? CircleCheck : Info
  const title = downloading ? tr("正在下载字幕…")
    : checking ? tr("正在检查公开字幕…")
    : downloaded ? tr("已下载字幕（{0} 条片段）", downloaded.segmentCount)
    : needsYoutubeAuth ? tr(youtubeCookiesConfigured ? "YouTube Cookie 验证未通过" : "请先配置 YouTube Cookie")
    : downloadError ? tr("下载字幕失败")
    : status === "captions" ? tr("公开字幕可读取")
    : subtitleLogin ? tr(asrEnabled ? "将使用语音转写" : "字幕需要登录查看")
    : status === "needs_asr" ? tr("未找到公开字幕")
    : status === "blocked" ? tr("暂不支持此音源")
    : status === "unknown" ? tr("尚未确认内容可用性") : tr("内容可用性尚未检查")
  const description = !busy && !downloaded
    ? (needsYoutubeAuth && !youtubeCookiesConfigured
      ? tr("YouTube 将优先下载字幕。请先在设置中配置 YouTube Cookie；配置后若仍无法下载字幕，再启用语音转写。")
      : downloadError ? localizedDetail(downloadError, title)
      : result ? localizedDetail(result.message, title) : undefined)
    : undefined

  async function handleExport(format: SubtitleExportFormat) {
    if (!downloaded || !onExportDownloaded || exporting) return
    setExporting(true)
    setExportError("")
    try {
      await onExportDownloaded(downloaded.previewId, format)
    } catch (error) {
      setExportError(String(error))
    } finally {
      setExporting(false)
    }
  }

  return <section className="source-readiness" data-state={busy ? "checking" : downloadError ? "unknown" : status} aria-label={tr("来源检查")}>
    <div className="source-readiness-copy" role="status" aria-live="polite">
      <div className="source-readiness-title"><Icon size={15} aria-hidden="true" className={busy ? "spin" : undefined} />
        <span>{title}</span></div>
      {!busy && downloaded && <p>{tr("可先预览原文或导出 SRT / VTT，点击「生成文章」时将直接复用已下载字幕。")}</p>}
      {!busy && !downloaded && description && <p>{description}</p>}
      {exportError && <p role="alert">{exportError}</p>}
    </div>
    <div className="source-readiness-actions">
      {!downloaded && result && !busy && <time dateTime={new Date(result.checkedAt).toISOString()}>
        {result.origin === "generation" ? tr("上次处理 ") : tr("检查于 ")}
        {new Date(result.checkedAt).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" })}
      </time>}
      {downloaded && !busy && <>
        {onPreviewDownloaded && <button className="text-button" type="button" onClick={() => onPreviewDownloaded(downloaded)}>
          <BookOpen size={14} aria-hidden="true" />{tr("预览原文")}
        </button>}
        {onExportDownloaded && <>
          <button className="text-button" type="button" disabled={exporting} onClick={() => void handleExport("srt")}>
            <Download size={14} aria-hidden="true" />{tr("导出 SRT")}
          </button>
          <button className="text-button" type="button" disabled={exporting} onClick={() => void handleExport("vtt")}>
            <Download size={14} aria-hidden="true" />{tr("导出 VTT")}
          </button>
        </>}
        {subtitleDownload && <button className="text-button" type="button" onClick={subtitleDownload.download}
          title={tr("重新下载该来源的字幕，不调用 AI 或下载音视频")}>
          <RefreshCw size={13} aria-hidden="true" />{tr("重新下载")}
        </button>}
      </>}
      {!downloaded && <>
        {needsYoutubeAuth && onYoutubeSettings && !busy && <button className="text-button" type="button" onClick={onYoutubeSettings}>
          <KeyRound size={14} aria-hidden="true" />{tr("配置 YouTube 认证")}
        </button>}
        {(status === "blocked" || (subtitleLogin && !asrEnabled)) && !busy && <>
          <button className="text-button" type="button" onClick={onImport}><FileUp size={14} aria-hidden="true" />{tr("导入字幕")}</button>
          <button className="text-button" type="button" onClick={onOpenSource}><ExternalLink size={14} aria-hidden="true" />{tr("查看来源")}</button>
        </>}
        {canSuggestAsr && onSettings && !busy &&
          <button className="text-button" type="button" onClick={onSettings}>{tr("设置语音识别")}</button>}
        {(status === "unknown" || Boolean(downloadError)) && !busy && <button className="text-button" type="button" onClick={onOpenSource}>{tr("查看来源")}</button>}
        {subtitleDownload && status !== "blocked" && <button className="text-button" type="button" onClick={subtitleDownload.download} disabled={busy}
          title={tr("仅下载并校验字幕，不调用 AI、语音识别或下载音视频")}>
          <Download size={13} aria-hidden="true" />{downloading ? tr("下载中") : tr("下载字幕")}
        </button>}
        <button className="text-button" type="button" onClick={state.check} disabled={busy}
          title={tr("检查公开字幕和来源限制，不调用 AI、语音识别或下载音频")}>
          {result && <RefreshCw size={13} aria-hidden="true" />}{checking ? tr("检查中") : result ? tr("重新检查") : tr("检查来源")}
        </button>
      </>}
    </div>
  </section>
}
