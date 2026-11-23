import { locale, localizedDetail, tr } from "./language.ts"
import { CircleCheck, ExternalLink, FileUp, Info, LoaderCircle, LockKeyhole, RefreshCw } from "lucide-react"
import type { SourceReadiness } from "./useSourceReadiness"
import "./source-readiness.css"

export function SourceReadinessPanel({ state, onImport, onOpenSource, onSettings, asrEnabled = false }: {
  state: SourceReadiness
  onImport: () => void
  onOpenSource: () => void
  onSettings?: () => void
  asrEnabled?: boolean
}) {
  if (!state.eligible) return null
  const result = state.result
  const checking = state.phase === "checking"
  const status = result?.status ?? "idle"
  const Icon = checking ? LoaderCircle : status === "blocked" ? LockKeyhole : status === "captions" ? CircleCheck : Info
  const title = checking ? tr("正在检查公开字幕…") : status === "captions" ? tr("公开字幕可读取")
    : status === "needs_asr" ? tr("未找到公开字幕") : status === "blocked" ? tr("暂不支持此音源")
    : status === "unknown" ? tr("尚未确认内容可用性") : tr("内容可用性尚未检查")
  return <section className="source-readiness" data-state={checking ? "checking" : status} aria-label={tr("来源检查")}>
    <div className="source-readiness-copy" role="status" aria-live="polite">
      <div className="source-readiness-title"><Icon size={15} aria-hidden="true" className={checking ? "spin" : undefined} />
        <span>{title}</span></div>
      {!checking && result && <p>{localizedDetail(result.message, title)}</p>}
    </div>
    <div className="source-readiness-actions">
      {result && !checking && <time dateTime={new Date(result.checkedAt).toISOString()}>
        {result.origin === "generation" ? tr("上次处理 ") : tr("检查于 ")}
        {new Date(result.checkedAt).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" })}
      </time>}
      {status === "blocked" && !checking && <>
        <button className="text-button" type="button" onClick={onImport}><FileUp size={14} aria-hidden="true" />{tr("导入字幕")}</button>
        <button className="text-button" type="button" onClick={onOpenSource}><ExternalLink size={14} aria-hidden="true" />{tr("查看来源")}</button>
      </>}
      {status === "needs_asr" && !asrEnabled && onSettings && !checking &&
        <button className="text-button" type="button" onClick={onSettings}>{tr("设置语音识别")}</button>}
      {status === "unknown" && !checking && <button className="text-button" type="button" onClick={onOpenSource}>{tr("查看来源")}</button>}
      <button className="text-button" type="button" onClick={state.check} disabled={checking}
        title={tr("检查公开字幕和来源限制，不调用 AI、语音识别或下载音频")}>
        {result && <RefreshCw size={13} aria-hidden="true" />}{checking ? tr("检查中") : result ? tr("重新检查") : tr("检查来源")}
      </button>
    </div>
  </section>
}
