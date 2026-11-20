import { isTauri } from "@tauri-apps/api/core"
import { getCurrentWindow } from "@tauri-apps/api/window"
import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type RefObject } from "react"
import { ArrowLeft, LoaderCircle } from "lucide-react"
import {
  checkTool, deleteApiToken, deleteAsrToken, loadApiToken, loadAsrToken, providerNames,
  saveApiToken, saveAsrToken, testApiConnection, type Provider, type ToolStatus,
} from "./api"
import { AsrSettingsPanel } from "./AsrSettingsPanel"
import { ConnectionRevision } from "./connectionState"
import { SettingsHelp } from "./SettingsHelp"
import { SettingsSecretField } from "./SettingsSecretField"
import {
  saveSettingsDraft, settingsDraftEqual, SettingsSaveError, SettingsValidationError,
  validateApiConfiguration, type Preferences, type SettingsDraft,
} from "./settingsState"
import "./settings.css"

export function Settings({ preferences, onSave, onBack, onTokenChanged, leaveGuard }: {
  preferences: Preferences
  onSave: (preferences: Preferences) => void
  onBack: () => void
  onTokenChanged: () => void
  leaveGuard: RefObject<(() => boolean) | null>
}) {
  const [draft, setDraft] = useState<SettingsDraft>(() => ({ preferences, apiToken: "", asrToken: "" }))
  const [baseline, setBaseline] = useState(draft)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState("")
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [asrTesting, setAsrTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string }>()
  const [toolInfo, setToolInfo] = useState<ToolStatus>()
  const [toolError, setToolError] = useState("")
  const [error, setError] = useState("")
  const [savedNotice, setSavedNotice] = useState(false)
  const keyLoadRevision = useRef(0)
  const preserveLoadDraft = useRef(false)
  const connectionRevision = useRef(new ConnectionRevision())
  const connectionIdentity = useRef("")
  const mounted = useRef(true)
  const savingNow = useRef(false)
  const testingNow = useRef(false)
  const dirty = !settingsDraftEqual(draft, baseline)
  const latest = useRef({ dirty, saving, asrTesting, onBack })
  latest.current = { dirty, saving, asrTesting, onBack }
  const closing = useRef(false)
  const { provider, paths, api, asr } = draft.preferences
  const disabled = loading || saving || Boolean(loadError)

  // Bind results to the exact configuration at render time as well as at edit
  // time. A late native response must never certify a different draft.
  const identity = JSON.stringify([provider, api.baseUrl, api.model, draft.apiToken])
  if (connectionIdentity.current !== identity) {
    connectionIdentity.current = identity
    connectionRevision.current.invalidate()
  }
  const testIdentity = useRef("")
  const currentTestResult = testIdentity.current === identity ? testResult : undefined

  function requestLeave() {
    if (savingNow.current || latest.current.saving) return false
    const notices = []
    if (latest.current.dirty) notices.push("有未保存的修改，离开后将放弃这些修改。")
    if (latest.current.asrTesting) notices.push("语音测试仍在进行，离开不会取消已提交的云端测试。")
    return notices.length === 0 || window.confirm(notices.join("\n") + "\n仍要离开设置？")
  }

  useLayoutEffect(() => {
    leaveGuard.current = requestLeave
    return () => { leaveGuard.current = null }
  }, [leaveGuard])

  useEffect(() => {
    mounted.current = true
    void readKeys(false)
    return () => {
      mounted.current = false
      keyLoadRevision.current++
      connectionRevision.current.invalidate()
    }
  }, [])

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape" || event.defaultPrevented || event.repeat || event.isComposing
        || event.keyCode === 229 || event.altKey || event.ctrlKey || event.metaKey) return
      if (document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) return
      event.preventDefault()
      latest.current.onBack()
    }
    function beforeUnload(event: BeforeUnloadEvent) {
      if (closing.current || (!latest.current.dirty && !savingNow.current && !latest.current.asrTesting)) return
      event.preventDefault()
      event.returnValue = ""
    }
    window.addEventListener("keydown", onKeyDown)
    window.addEventListener("beforeunload", beforeUnload)
    let active = true
    let unlisten: (() => void) | undefined
    if (isTauri()) {
      void getCurrentWindow().onCloseRequested(event => {
        if (!requestLeave()) event.preventDefault()
        else closing.current = true
      }).then(cleanup => {
        if (active) unlisten = cleanup
        else cleanup()
      }).catch(() => { /* Browser navigation still retains beforeunload protection. */ })
    }
    return () => {
      active = false
      unlisten?.()
      window.removeEventListener("keydown", onKeyDown)
      window.removeEventListener("beforeunload", beforeUnload)
    }
  }, [])

  useEffect(() => {
    let active = true
    setToolInfo(undefined)
    setToolError("")
    if (provider === "api") return
    const timer = window.setTimeout(() => {
      checkTool(provider, paths[provider]).then(
        result => { if (active) setToolInfo(result) },
        () => { if (active) setToolError("暂时无法检查工具路径，请确认程序可用后重试。") },
      )
    }, 200)
    return () => { active = false; window.clearTimeout(timer) }
  }, [provider, paths.codex, paths.claude])

  async function readKeys(preserveDraft: boolean) {
    const revision = ++keyLoadRevision.current
    preserveLoadDraft.current = preserveDraft
    setLoading(true)
    setLoadError("")
    try {
      const [apiToken, asrToken] = await Promise.all([loadApiToken(), loadAsrToken()])
      if (!mounted.current || revision !== keyLoadRevision.current) return
      const keys = { apiToken: apiToken || "", asrToken: asrToken || "" }
      setBaseline(current => ({ ...current, ...keys }))
      if (!preserveDraft) setDraft(current => ({ ...current, ...keys }))
    } catch {
      if (mounted.current && revision === keyLoadRevision.current) {
        setLoadError("无法读取已保存的密钥，请重试。读取成功前不会覆盖配置。")
      }
    } finally {
      if (mounted.current && revision === keyLoadRevision.current) setLoading(false)
    }
  }

  function update(change: (current: SettingsDraft) => SettingsDraft, affectsApi = false) {
    if (disabled || savingNow.current) return
    if (affectsApi) {
      connectionRevision.current.invalidate()
      setTestResult(undefined)
    }
    setSavedNotice(false)
    setError("")
    setDraft(change)
  }

  function showFailure(failure: unknown) {
    setError(failure instanceof Error ? failure.message : String(failure))
    if (failure instanceof SettingsValidationError) {
      requestAnimationFrame(() => {
        const field = document.getElementById(failure.field)
          || document.getElementById(`${failure.field}-change`)
          || (failure.field === "api-token" ? document.getElementById("asr-credential-source") : null)
          || document.getElementById("settings-error")
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

  async function tryConnection() {
    if (disabled || testingNow.current || savingNow.current) return
    testingNow.current = true
    setTesting(true)
    setTestResult(undefined)
    const revision = connectionRevision.current.snapshot()
    testIdentity.current = identity
    try {
      const checked = validateApiConfiguration(api, draft.apiToken)
      const result = await testApiConnection(checked.baseUrl, checked.model, draft.apiToken.trim())
      if (mounted.current && connectionRevision.current.accepts(revision)) setTestResult(result)
    } catch (failure) {
      if (mounted.current && connectionRevision.current.accepts(revision)) {
        setTestResult({ ok: false, message: failure instanceof Error ? failure.message : String(failure) })
      }
    } finally {
      testingNow.current = false
      if (mounted.current) setTesting(false)
    }
  }

  async function clearCredential(kind: "apiToken" | "asrToken") {
    if (disabled || savingNow.current || testing || asrTesting) return
    const label = kind === "apiToken" ? "AI API 密钥" : "语音 API 密钥"
    if (!window.confirm(`立即删除本机保存的${label}？\n取消设置不会恢复已删除的密钥。`)) return
    savingNow.current = true
    setSaving(true)
    setError("")
    const original = baseline[kind]
    try {
      if (kind === "apiToken") await deleteApiToken()
      else await deleteAsrToken()
      if (!mounted.current) return
      setBaseline(current => ({ ...current, [kind]: "" }))
      setDraft(current => ({ ...current, [kind]: current[kind] === original ? "" : current[kind] }))
      connectionRevision.current.invalidate()
      setTestResult(undefined)
      setSavedNotice(false)
      if (kind === "apiToken") onTokenChanged()
    } catch {
      if (mounted.current) setError(`删除${label}失败，原有密钥仍保留。请重试。`)
    } finally {
      savingNow.current = false
      if (mounted.current) setSaving(false)
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    if (disabled || savingNow.current || testing || asrTesting || !dirty) return
    savingNow.current = true
    setSaving(true)
    setError("")
    setSavedNotice(false)
    try {
      const saved = await saveSettingsDraft(draft, baseline, {
        saveApiToken, deleteApiToken, saveAsrToken, deleteAsrToken, savePreferences: onSave,
      })
      if (!mounted.current) return
      setDraft(saved)
      setBaseline(saved)
      setSavedNotice(true)
      onTokenChanged()
    } catch (failure) {
      if (!mounted.current) return
      showFailure(failure)
      if (failure instanceof SettingsSaveError && failure.rollbackFailed) {
        await readKeys(true)
        if (mounted.current) onTokenChanged()
      }
    } finally {
      savingNow.current = false
      if (mounted.current) setSaving(false)
    }
  }

  return <section className="settings-page" aria-labelledby="settings-heading">
    <header className="settings-heading">
      <button className="icon-button settings-back" type="button" aria-label="返回" title="返回" disabled={saving} onClick={onBack}>
        <ArrowLeft size={20} aria-hidden="true" />
      </button>
      <h1 id="settings-heading">设置</h1>
    </header>
    <form className="settings-form" onSubmit={event => { void save(event) }} noValidate aria-busy={saving || loading}>
      {loadError && <div className="settings-load-error" role="alert">
        <p>{loadError}</p>
        <button type="button" className="text-button" disabled={loading || saving}
          onClick={() => void readKeys(preserveLoadDraft.current)}>重新读取</button>
      </div>}
      <section className="settings-card" aria-labelledby="settings-ai-heading">
        <header className="settings-card-header">
          <div className="settings-card-title">
            <h2 id="settings-ai-heading">AI 生成</h2>
            <SettingsHelp label="AI 工具说明">
              自定义 API 使用 OpenAI 兼容接口；地址填写 API 根地址，模型填写服务商提供的模型 ID。
              Codex 和 Claude Code 使用本机已安装的工具，绿点仅表示找到程序。
              密钥保存在本机私有文件中。
            </SettingsHelp>
          </div>
          <div className="settings-provider" role="radiogroup" aria-label="默认 AI 工具" id="provider">
            {(["api", "codex", "claude"] as Provider[]).map(value => <label key={value} className={provider === value ? "is-selected" : ""}>
              <input type="radio" name="ai-provider" value={value} checked={provider === value} disabled={disabled}
                onChange={() => update(current => ({ ...current, preferences: { ...current.preferences, provider: value } }), true)} />
              <span>{providerNames[value]}</span>
            </label>)}
          </div>
        </header>
        <div className="settings-card-body">
          {provider === "api" ? <>
            <label className="settings-field" htmlFor="api-url">
              <span className="settings-label">API 地址</span>
              <input id="api-url" type="url" aria-label="API 地址" value={api.baseUrl} disabled={disabled}
                placeholder="https://api.example.com/v1" autoComplete="off" spellCheck={false}
                onChange={event => update(current => ({ ...current, preferences: {
                  ...current.preferences, api: { ...current.preferences.api, baseUrl: event.target.value },
                } }), true)} />
            </label>
            <label className="settings-field" htmlFor="api-model">
              <span className="settings-label">模型</span>
              <input id="api-model" type="text" aria-label="API 模型名称" value={api.model} disabled={disabled}
                placeholder="填写模型 ID" autoComplete="off" spellCheck={false}
                onChange={event => update(current => ({ ...current, preferences: {
                  ...current.preferences, api: { ...current.preferences.api, model: event.target.value },
                } }), true)} />
            </label>
            <SettingsSecretField id="api-token" label="API 密钥" value={draft.apiToken} savedValue={baseline.apiToken}
              onChange={apiToken => update(current => ({ ...current, apiToken }), true)}
              onClear={() => void clearCredential("apiToken")} disabled={disabled} clearDisabled={testing || asrTesting} />
            <div className="settings-card-footer settings-connect-row">
              <button className={currentTestResult?.ok ? "secondary connection-ok" : "secondary"} type="button"
                disabled={disabled || testing || !draft.apiToken.trim()}
                title={currentTestResult?.ok ? "重新测试当前配置" : "测试当前地址、模型和密钥"}
                onClick={() => void tryConnection()}>
                {testing ? <LoaderCircle size={15} className="spin" aria-hidden="true" />
                  : currentTestResult?.ok ? <span className="settings-green-dot" aria-hidden="true" /> : null}
                {testing ? "连接中…" : currentTestResult?.ok ? "已连接" : "测试连接"}
              </button>
            </div>
            <span className="sr-only" role="status">{currentTestResult?.ok ? "已连接" : ""}</span>
            {currentTestResult && !currentTestResult.ok && <p className="settings-test-error" role="alert">{currentTestResult.message}</p>}
          </> : <>
            <label className="settings-field" htmlFor={`${provider}-path`}>
              <span className="settings-label">程序路径</span>
              <input id={`${provider}-path`} type="text" aria-label={`${providerNames[provider]} 路径`} disabled={disabled}
                placeholder={toolInfo?.executable || "留空自动查找"} value={paths[provider]} autoComplete="off" spellCheck={false}
                onChange={event => update(current => ({ ...current, preferences: {
                  ...current.preferences, paths: { ...current.preferences.paths, [provider]: event.target.value },
                } }))} />
            </label>
            <div className="settings-tool-status" role="status">
              {toolInfo?.executable ? <span className="settings-recognized" title="仅表示已找到可执行文件，不代表已登录">
                <span className="settings-green-dot" aria-hidden="true" />已检测到 {providerNames[provider]}
              </span> : toolError || (toolInfo ? `未检测到 ${providerNames[provider]}，请填写程序路径` : "正在查找本机工具…")}
            </div>
          </>}
        </div>
      </section>
      <AsrSettingsPanel value={asr} onChange={asr => update(current => ({ ...current, preferences: { ...current.preferences, asr } }))}
        token={draft.asrToken} apiToken={draft.apiToken} savedToken={baseline.asrToken}
        onToken={asrToken => update(current => ({ ...current, asrToken }))}
        onClearToken={testing || asrTesting ? undefined : () => void clearCredential("asrToken")}
        disabled={disabled} onTestingChange={setAsrTesting} />
      {error && <p id="settings-error" className="settings-error" role="alert" tabIndex={-1}>{error}</p>}
      <footer className="settings-actions">
        <span className="settings-save-status" role="status" data-state={savedNotice && !dirty ? "saved" : undefined}>
          {loading ? "正在读取配置…" : saving ? "正在保存…" : asrTesting ? "正在测试转写…" : testing ? "正在测试连接…"
            : dirty ? "有未保存的修改" : savedNotice ? "设置已保存" : "没有未保存的修改"}
        </span>
        <div className="settings-action-buttons">
          <button className="plain" type="button" disabled={saving} onClick={onBack}>取消</button>
          <button className="primary" type="submit" disabled={disabled || testing || asrTesting || !dirty}
            aria-label="保存设置" title="保存设置">
            {saving && <LoaderCircle size={15} className="spin" aria-hidden="true" />}{saving ? "保存中…" : "保存设置"}
          </button>
        </div>
      </footer>
    </form>
  </section>
}
