import { localizedDetail, tr } from "./language.ts"
import { useEffect, useRef, useState, type ReactNode } from "react"
import { ChevronDown, CircleAlert, ExternalLink, FolderOpen, LoaderCircle, RefreshCw } from "lucide-react"
import { checkFfmpeg, locateFfmpeg, openExternal, testAsrConnection, type FfmpegStatus } from "./api"
import { ConnectionRevision } from "./connectionState"
import { SettingsHelp } from "./SettingsHelp"
import { SettingsSecretField } from "./SettingsSecretField"
import { SettingsValidationError, validateAsrConfiguration } from "./settingsState"
import type { AsrOptions } from "./asrSettings"

type ConnectionResult = { revision: number; ok: boolean; message: string }

export function AsrSettingsPanel({ value, onChange, token, onToken, apiToken, savedToken, disabled, onTestingChange, onClearToken, footer }: {
  value: AsrOptions; onChange: (value: AsrOptions) => void
  token: string; onToken: (token: string) => void; apiToken: string; savedToken: string
  disabled: boolean; onTestingChange?: (busy: boolean) => void; onClearToken?: () => void
  footer?: ReactNode
}) {
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<ConnectionResult>()
  const revision = useRef(new ConnectionRevision())
  const dependencyRevision = useRef(new ConnectionRevision())
  const mounted = useRef(true)
  const testRunning = useRef(false)
  const currentValue = useRef(value)
  currentValue.current = value
  const currentDisabled = useRef(disabled)
  currentDisabled.current = disabled
  const editingEpoch = useRef({ disabled, value: 0 })
  if (editingEpoch.current.disabled !== disabled) {
    editingEpoch.current = { disabled, value: editingEpoch.current.value + 1 }
  }
  const testingChanged = useRef(onTestingChange)
  testingChanged.current = onTestingChange

  // A response racing an external draft update must never mark new values connected.
  const configuration = JSON.stringify(value)
  const testedValues = useRef({ configuration, token, apiToken })
  if (testedValues.current.configuration !== configuration || testedValues.current.token !== token
      || testedValues.current.apiToken !== apiToken) {
    testedValues.current = { configuration, token, apiToken }
    revision.current.invalidate()
  }
  const activeResult = result && revision.current.accepts(result.revision) ? result : undefined
  const effectiveToken = value.reuseToken ? apiToken : token

  const [inspection, setInspection] = useState(0)
  const [ffmpeg, setFfmpeg] = useState<{ configuredPath: string; status: FfmpegStatus }>()
  const [ffmpegOpen, setFfmpegOpen] = useState(false)
  const [checking, setChecking] = useState(false)
  const [locating, setLocating] = useState(false)
  const [dependencyError, setDependencyError] = useState<{ configuredPath: string; message: string }>()
  const [maxMinutesInput, setMaxMinutesInput] = useState(() =>
    Number.isFinite(value.maxMinutes) ? String(value.maxMinutes) : "",
  )
  useEffect(() => {
    if (!Number.isFinite(value.maxMinutes)) return
    const nextText = String(value.maxMinutes)
    setMaxMinutesInput(current => {
      const parsed = current.trim() === "" ? Number.NaN : Number(current.trim())
      return parsed === value.maxMinutes ? current : nextText
    })
  }, [value.maxMinutes])
  const environment = ffmpeg?.configuredPath === value.ffmpegPath ? ffmpeg.status : undefined
  const environmentError = dependencyError?.configuredPath === value.ffmpegPath ? dependencyError.message : ""
  const environmentReady = !checking && environment?.state === "ready"

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      revision.current.invalidate()
      dependencyRevision.current.invalidate()
      testingChanged.current?.(false)
    }
  }, [])

  useEffect(() => {
    dependencyRevision.current.invalidate()
    if (!value.enabled) {
      setChecking(false)
      return
    }
    const current = dependencyRevision.current.snapshot()
    const configuredPath = value.ffmpegPath
    setChecking(true)
    setFfmpeg(undefined)
    setDependencyError(undefined)
    checkFfmpeg(configuredPath).then(status => {
      if (!mounted.current || !dependencyRevision.current.accepts(current)) return
      setFfmpeg({ configuredPath, status })
      if (status.state !== "ready") setFfmpegOpen(true)
    }, error => {
      if (!mounted.current || !dependencyRevision.current.accepts(current)) return
      setDependencyError({ configuredPath, message: String(error) })
      setFfmpegOpen(true)
    }).finally(() => {
      if (mounted.current && dependencyRevision.current.accepts(current)) setChecking(false)
    })
    return () => { dependencyRevision.current.invalidate() }
  }, [value.enabled, value.ffmpegPath, inspection])

  function update(next: AsrOptions) {
    revision.current.invalidate()
    onChange(next)
  }

  function updateToken(next: string) {
    revision.current.invalidate()
    onToken(next)
  }

  async function locate() {
    if (disabled || locating || checking) return
    const current = dependencyRevision.current.snapshot()
    const editableRevision = editingEpoch.current.value
    const draftAtSelection = currentValue.current
    setLocating(true)
    setDependencyError(undefined)
    try {
      const selected = await locateFfmpeg()
      if (selected?.path && mounted.current && !currentDisabled.current
          && currentValue.current.enabled && dependencyRevision.current.accepts(current)
          && editingEpoch.current.value === editableRevision && currentValue.current === draftAtSelection) {
        update({ ...currentValue.current, ffmpegPath: selected.path })
      }
    } catch (error) {
      if (mounted.current && dependencyRevision.current.accepts(current)) {
        setDependencyError({ configuredPath: currentValue.current.ffmpegPath, message: String(error) })
        setFfmpegOpen(true)
      }
    } finally {
      if (mounted.current) setLocating(false)
    }
  }

  async function test() {
    if (disabled || testRunning.current || !value.enabled || !effectiveToken.trim()) return
    const current = revision.current.snapshot()
    testRunning.current = true
    setTesting(true)
    setResult(undefined)
    testingChanged.current?.(true)
    try {
      const options = validateAsrConfiguration(value, effectiveToken)
      const response = await testAsrConnection(options, effectiveToken.trim())
      if (mounted.current && revision.current.accepts(current)) setResult({ ...response, revision: current })
    } catch (error) {
      if (mounted.current && revision.current.accepts(current)) {
        setResult({ ok: false, message: String(error), revision: current })
        if (error instanceof SettingsValidationError) {
          requestAnimationFrame(() => {
            if (!mounted.current || !revision.current.accepts(current)) return
            const fieldId = error.field === "asr-token" && value.reuseToken ? "api-token" : error.field
            const field = document.getElementById(fieldId) || document.getElementById(`${fieldId}-change`)
              || document.getElementById("asr-credential-source")
            if (!field) return
            let disclosure = field.closest("details")
            while (disclosure) {
              disclosure.open = true
              disclosure = disclosure.parentElement?.closest("details") ?? null
            }
            field.focus()
            field.scrollIntoView({ block: "nearest" })
          })
        }
      }
    } finally {
      testRunning.current = false
      if (mounted.current) {
        setTesting(false)
        testingChanged.current?.(false)
      }
    }
  }

  return <section className="settings-card asr-settings" aria-labelledby="asr-heading">
    <div className="settings-card-header">
      <h2 id="asr-heading">{tr("无字幕时转写")}</h2>
      <input id="asr-enabled" className="settings-toggle" type="checkbox" role="switch" aria-labelledby="asr-heading"
        aria-controls={value.enabled ? "asr-settings-body" : undefined} checked={value.enabled} disabled={disabled || testing}
        onChange={event => update({ ...value, enabled: event.target.checked })} />
    </div>
    {value.enabled && <div id="asr-settings-body" className="settings-card-body asr-settings-body">
      <div className="settings-field settings-field-static">
        <span className="settings-label">{tr("语音服务")}</span>
        <div className="settings-field-control">{tr("阿里云百炼 · paraformer-v2")}</div>
      </div>
      <div className="settings-field">
        <label className="settings-label" htmlFor="asr-credential-source">{tr("API 密钥")}</label>
        <div className="settings-field-control">
          <span className="settings-select">
            <select id="asr-credential-source" value={value.reuseToken ? "reuse" : "own"} disabled={disabled}
              aria-describedby={value.reuseToken && !apiToken.trim() ? "asr-key-hint" : undefined}
              onChange={event => update({ ...value, reuseToken: event.target.value === "reuse" })}>
              <option value="own">{tr("单独配置百炼密钥")}</option>
              <option value="reuse">{tr("使用自定义 API 密钥")}</option>
            </select>
            <ChevronDown size={15} aria-hidden="true" />
          </span>
          {value.reuseToken && !apiToken.trim() && <p id="asr-key-hint" className="settings-field-hint">
            {tr("请先在「自定义 API」中填写密钥，或选择单独配置百炼密钥。")}
          </p>}
        </div>
      </div>
      {!value.reuseToken && <SettingsSecretField id="asr-token" label={tr("百炼密钥")} value={token}
        savedValue={savedToken} onChange={updateToken} disabled={disabled} clearDisabled={testing}
        onClear={onClearToken ? () => { revision.current.invalidate(); onClearToken() } : undefined} />}

      <details className="settings-disclosure environment-details" open={ffmpegOpen}
        onToggle={event => setFfmpegOpen(event.currentTarget.open)}>
        <summary className="environment-summary">
          <span>FFmpeg</span>
          <span className={`environment-status${environmentReady ? " ready" : ""}`} role="status" aria-live="polite">
            {checking || locating ? <LoaderCircle size={14} className="spin" aria-hidden="true" />
              : environmentReady ? <span className="settings-green-dot" aria-hidden="true" />
              : <CircleAlert size={14} aria-hidden="true" />}
            {locating ? tr("选择并检查中…") : checking ? tr("检查中…") : environmentReady ? tr("已就绪{0}", environment.version ? ` · ${environment.version}` : "")
              : environment?.state === "missing" ? tr("未检测到") : tr("需要检查")}
          </span>
        </summary>
        <div className="settings-details-body">
          {environment?.path && <p className="environment-path" title={environment.path}>{environment.path}</p>}
          {environment && environment.state !== "ready" && <p className="environment-help">
            {localizedDetail(environment.message, tr("无法检查 FFmpeg，请重新选择可执行程序。"))}</p>}
          <div className="environment-actions">
            <button id="ffmpeg-path" type="button" className="secondary" disabled={disabled || checking || locating} onClick={() => void locate()}>
              <FolderOpen size={15} aria-hidden="true" /> {tr("选择 FFmpeg…")}
            </button>
            <button type="button" className="text-button" disabled={disabled || checking || locating}
              onClick={() => setInspection(current => current + 1)}>
              <RefreshCw size={14} aria-hidden="true" /> {tr("重新检查")}
            </button>
            {value.ffmpegPath && <button type="button" className="text-button" disabled={disabled || checking || locating}
              onClick={() => update({ ...value, ffmpegPath: "" })}>{tr("自动查找")}</button>}
          </div>
          <details className="environment-guide">
            <summary>{tr("没有安装 FFmpeg？")}</summary>
            <p>{tr("从 FFmpeg 官方下载页选择 macOS 可执行版本，解压到固定目录，再选择程序。无需安装 Homebrew；不要选择压缩包、源码文件夹或 ffprobe。")}</p>
            <button type="button" className="text-button" disabled={disabled}
              onClick={() => void openExternal("https://ffmpeg.org/download.html").catch(error => {
                if (mounted.current) setDependencyError({ configuredPath: currentValue.current.ffmpegPath, message: String(error) })
              })}>
              {tr("打开官方下载页")} <ExternalLink size={13} aria-hidden="true" />
            </button>
            <p>{tr("FFmpeg 用于本地音频分片。版本检查不会上传音频或产生费用，所选路径随设置保存。")}</p>
          </details>
          {environmentError && <p className="settings-test-error" role="alert">{environmentError}</p>}
        </div>
      </details>

      <details className="settings-disclosure asr-advanced">
        <summary>{tr("高级设置")}</summary>
        <div className="settings-details-body">
          <div className="settings-field">
            <label className="settings-label" htmlFor="asr-url">{tr("API 地址")}</label>
            <div className="settings-field-control">
              <input id="asr-url" type="url" aria-label={tr("百炼原生 API 地址")} value={value.baseUrl} disabled={disabled}
                autoComplete="off" spellCheck={false} onChange={event => update({ ...value, baseUrl: event.target.value })} />
            </div>
          </div>
          <div className="settings-field">
            <label className="settings-label" htmlFor="asr-chunk-minutes">{tr("音频分片")}</label>
            <div className="settings-field-control">
              <span className="settings-select">
                <select id="asr-chunk-minutes" value={value.chunkMinutes} disabled={disabled}
                  onChange={event => update({ ...value, chunkMinutes: Number(event.target.value) })}>
                  <option value={5}>{tr("每 5 分钟（推荐）")}</option><option value={10}>{tr("每 10 分钟")}</option>
                  <option value={15}>{tr("每 15 分钟")}</option><option value={0}>{tr("整段转写")}</option>
                </select>
                <ChevronDown size={15} aria-hidden="true" />
              </span>
            </div>
          </div>
          <div className="settings-field">
            <label className="settings-label" htmlFor="asr-max-minutes">{tr("时长上限")}</label>
            <div className="settings-field-control">
              <input id="asr-max-minutes" type="number" min={1} max={720} step={1} aria-label={tr("单次转写上限（分钟）")}
                value={maxMinutesInput} disabled={disabled}
                onChange={event => {
                  const raw = event.target.value
                  setMaxMinutesInput(raw)
                  const trimmed = raw.trim()
                  update({ ...value, maxMinutes: trimmed === "" ? Number.NaN : Number(trimmed) })
                }} />
            </div>
          </div>
        </div>
      </details>

      <div className="settings-card-footer">
        <div className="settings-notice">
          <span>{tr("音频会上传至百炼，转写与测试会产生费用。")}</span>
          <SettingsHelp label={tr("语音转写说明")}>
            <p>{tr("找不到公开字幕，或 B 站仅限制字幕登录查看时，使用公开音轨转写。网络错误和音轨访问受限会停止处理。")}</p>
            <p>{tr("百炼临时上传用于个人测试，文件保留 48 小时后自动清理，不适合直接用于生产服务。")}</p>
            <p>{tr("测试使用官方约 4 秒语音样本。选择复用后，使用本页当前填写的自定义 API 密钥，无需先保存；该密钥须有百炼语音服务权限，与 Codex / Claude Code 登录无关。")}</p>
            <p>{tr("离开设置页不会停止已提交的云端转写，请勿连续重复测试。")}</p>
          </SettingsHelp>
        </div>
        <div className="settings-connect-row">
          <button type="button" className={activeResult?.ok ? "secondary connection-ok" : "secondary"}
            disabled={disabled || testing || !effectiveToken.trim()}
            title={activeResult?.ok ? tr("重新测试转写，会产生少量费用") : tr("使用官方约 4 秒语音样本验证转写，会产生少量费用")}
            onClick={() => void test()}>
            {testing ? <LoaderCircle size={15} className="spin" aria-hidden="true" />
              : activeResult?.ok ? <span className="settings-green-dot" aria-hidden="true" /> : null}
            {testing ? tr("测试中…") : activeResult?.ok ? tr("已连接") : tr("测试转写")}
          </button>
          <span className="sr-only" role="status">{activeResult?.ok ? tr("语音转写已连接") : ""}</span>
        </div>
      </div>
      {activeResult && !activeResult.ok && <p className="settings-test-error" role="alert">{activeResult.message}</p>}
    </div>}
    {footer}
  </section>
}
