import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type RefObject } from "react"
import { ChevronLeft, FolderOpen, LoaderCircle } from "lucide-react"
import {
  checkTool, deleteApiToken, deleteAsrToken, listCliModels, loadApiToken, loadAsrToken, pickExecutable, providerNames,
  saveApiToken, saveAsrToken, testApiConnection, type Provider, type ToolStatus,
} from "./api"
import { AsrSettingsPanel } from "./AsrSettingsPanel"
import { CliModelPicker } from "./CliModelPicker"
import { builtinCliCatalog, defaultCliPreferences, type CliModelCatalog } from "./cliModels"
import { ConfirmDialog } from "./ConfirmDialog"
import { ConnectionRevision } from "./connectionState"
import { tr, type LanguageSettings } from "./language.ts"
import { LanguageSettingsCard } from "./LanguageSettingsCard"
import { SettingsHelp } from "./SettingsHelp"
import { SettingsSecretField } from "./SettingsSecretField"
import type { GenerationSettingsGate } from "./generationSettings"
import {
  isLoopbackBaseUrl, saveSettingsDraft, settingsDraftEqual, SettingsSaveError, SettingsValidationError,
  validateApiConfiguration, type Preferences, type SettingsDraft,
} from "./settingsState"
import type { ThemeMode } from "./themeState"
import { YoutubeCookiesPanel } from "./YoutubeCookiesPanel"
import { defaultYoutubeCookies } from "./youtubeCookies"
import "./settings.css"

export type SettingsTarget = "ai" | "asr" | "youtube"
export type SettingsLeaveGuard = {
  confirm: (proceed: () => void) => boolean
  isDirty: () => boolean
}

export function Settings({ preferences, onSave, onBack, onTokenChanged, settingsGate, settingsRecovery,
  leaveGuard, target, language, onLanguageChange, languageError, theme, onThemeChange, unifiedHeader = false }: {
  preferences: Preferences
  onSave: (preferences: Preferences) => Promise<void> | void
  onBack: () => void
  onTokenChanged: () => void
  settingsGate: GenerationSettingsGate
  settingsRecovery: boolean
  leaveGuard: RefObject<SettingsLeaveGuard | null>
  target?: SettingsTarget
  language: LanguageSettings
  onLanguageChange: (language: LanguageSettings) => void | Promise<void>
  languageError: string
  theme: ThemeMode
  onThemeChange: (theme: ThemeMode) => void | Promise<void>
  unifiedHeader?: boolean
}) {
  const [draft, setDraft] = useState<SettingsDraft>(() => ({ preferences, apiToken: "", asrToken: "" }))
  const [baseline, setBaseline] = useState(draft)
  const [draftLanguage, setDraftLanguage] = useState<LanguageSettings>(language)
  const [baselineLanguage, setBaselineLanguage] = useState<LanguageSettings>(language)
  const [draftTheme, setDraftTheme] = useState<ThemeMode>(theme)
  const [baselineTheme, setBaselineTheme] = useState<ThemeMode>(theme)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState("")
  const [saving, setSaving] = useState(false)
  const [pickingTool, setPickingTool] = useState(false)
  const [testing, setTesting] = useState(false)
  const [asrTesting, setAsrTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string }>()
  const [toolInfo, setToolInfo] = useState<ToolStatus>()
  const [toolError, setToolError] = useState("")
  const [cliCatalogs, setCliCatalogs] = useState<{ codex: CliModelCatalog; claude: CliModelCatalog }>(() => ({
    codex: builtinCliCatalog("codex"),
    claude: builtinCliCatalog("claude"),
  }))
  const [error, setError] = useState("")
  const [savedNotice, setSavedNotice] = useState(false)
  const [confirmClearKind, setConfirmClearKind] = useState<"apiToken" | "asrToken">()
  const [pendingLeaveAction, setPendingLeaveAction] = useState<() => void>()
  const keyLoadRevision = useRef(0)
  const toolPickRevision = useRef(0)
  const preserveLoadDraft = useRef(false)
  const connectionRevision = useRef(new ConnectionRevision())
  const connectionIdentity = useRef("")
  const mounted = useRef(true)
  const savingNow = useRef(false)
  const testingNow = useRef(false)
  const dirty = !settingsDraftEqual(draft, baseline)
    || draftLanguage.interfaceLanguage !== baselineLanguage.interfaceLanguage
    || draftLanguage.pageLanguage !== baselineLanguage.pageLanguage
    || draftTheme !== baselineTheme
  const latest = useRef({ dirty, saving, asrTesting, onBack })
  latest.current = { dirty, saving, asrTesting, onBack }
  const { provider, paths, api, asr } = draft.preferences
  const cliSettings = draft.preferences.cli ?? defaultCliPreferences()
  const youtubeCookies = draft.preferences.youtubeCookies ?? defaultYoutubeCookies
  const disabled = loading || saving || Boolean(loadError)
  const pendingTarget = useRef(target)
  const currentDraft = useRef(draft)
  currentDraft.current = draft
  const currentDisabled = useRef(disabled)
  currentDisabled.current = disabled

  useLayoutEffect(() => {
    const destination = pendingTarget.current
    if (disabled || !destination) return
    let field = destination === "youtube" ? "youtube-cookie-mode" : "asr-enabled"
    if (destination === "ai") {
      if (provider === "api") {
        field = "api-token"
        try { validateApiConfiguration(api, draft.apiToken) } catch (failure) {
          if (failure instanceof SettingsValidationError) field = failure.field
        }
      } else {
        if (provider === "claude" && !toolInfo && !toolError) return
        field = provider === "claude" && toolInfo?.executable && !toolInfo.keyAvailable
          ? "claude-auth-status" : `${provider}-path`
      }
    }
    const element = document.getElementById(field) ?? document.getElementById(`${field}-change`)
    if (element) {
      pendingTarget.current = undefined
      element.focus({ preventScroll: true })
      element.scrollIntoView({ block: "nearest" })
    }
  }, [disabled, provider, api, draft.apiToken, toolInfo, toolError])

  // Bind results to the exact configuration at render time as well as at edit
  // time. A late native response must never certify a different draft.
  const identity = JSON.stringify([provider, api.baseUrl, api.model, draft.apiToken])
  if (connectionIdentity.current !== identity) {
    connectionIdentity.current = identity
    connectionRevision.current.invalidate()
  }
  const testIdentity = useRef("")
  const currentTestResult = testIdentity.current === identity ? testResult : undefined

  function requestLeave(proceed: () => void): boolean {
    if (savingNow.current || latest.current.saving) return false
    if (!latest.current.dirty && !latest.current.asrTesting) return true
    setPendingLeaveAction(() => proceed)
    return false
  }

  useLayoutEffect(() => {
    leaveGuard.current = {
      confirm: requestLeave,
      isDirty: () => savingNow.current || latest.current.saving || latest.current.dirty || latest.current.asrTesting,
    }
    return () => { leaveGuard.current = null }
  }, [leaveGuard])

  useEffect(() => {
    mounted.current = true
    void readKeys(false)
    return () => {
      mounted.current = false
      keyLoadRevision.current++
      toolPickRevision.current++
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
      if (!latest.current.dirty && !savingNow.current && !latest.current.asrTesting) return
      event.preventDefault()
      event.returnValue = ""
    }
    window.addEventListener("keydown", onKeyDown)
    window.addEventListener("beforeunload", beforeUnload)
    return () => {
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
        () => { if (active) setToolError(tr("暂时无法检查工具路径，请确认程序可用后重试。")) },
      )
    }, 200)
    return () => { active = false; window.clearTimeout(timer) }
  }, [provider, paths.codex, paths.claude])

  useEffect(() => {
    if (provider === "api") return
    let active = true
    listCliModels(provider).then(
      catalog => {
        if (!active || !catalog?.models?.length) return
        setCliCatalogs(current => ({ ...current, [provider]: catalog }))
      },
      () => { /* Keep builtin fallback catalog when native cache lookup is unavailable. */ },
    )
    return () => { active = false }
  }, [provider])

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
        setLoadError(tr("无法读取已保存的密钥，请重试。读取成功前不会覆盖配置。"))
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

  async function chooseExecutable(tool: "codex" | "claude") {
    if (disabled || pickingTool) return
    const rev = ++toolPickRevision.current
    const draftAtSelection = currentDraft.current
    setPickingTool(true)
    setError("")
    try {
      const selected = await pickExecutable(tr("选择 {0} 可执行文件", providerNames[tool]))
      if (selected && mounted.current && !currentDisabled.current
        && toolPickRevision.current === rev && currentDraft.current === draftAtSelection
        && currentDraft.current.preferences.provider === tool) {
        update(current => ({
          ...current,
          preferences: { ...current.preferences, paths: { ...current.preferences.paths, [tool]: selected } },
        }))
      }
    } catch (failure) {
      if (mounted.current && toolPickRevision.current === rev) setError(String(failure))
    } finally {
      if (mounted.current) setPickingTool(false)
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

  async function executeClearCredential(kind: "apiToken" | "asrToken") {
    if (disabled || savingNow.current || testing || asrTesting) return
    const label = kind === "apiToken" ? tr("AI API 密钥") : tr("语音 API 密钥")
    savingNow.current = true
    setSaving(true)
    setError("")
    const original = baseline[kind]
    try {
      await settingsGate.withLock(kind === "apiToken" ? deleteApiToken : deleteAsrToken)
      if (!mounted.current) return
      setBaseline(current => ({ ...current, [kind]: "" }))
      setDraft(current => ({ ...current, [kind]: current[kind] === original ? "" : current[kind] }))
      connectionRevision.current.invalidate()
      setTestResult(undefined)
      setSavedNotice(false)
      setConfirmClearKind(undefined)
      if (kind === "apiToken") onTokenChanged()
    } catch {
      const message = tr("删除{0}失败，原有密钥仍保留。请重试。", label)
      if (mounted.current) setError(message)
      throw new Error(message)
    } finally {
      savingNow.current = false
      if (mounted.current) setSaving(false)
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    if (disabled || savingNow.current || testing || asrTesting || (!dirty && !settingsRecovery)) return
    savingNow.current = true
    setSaving(true)
    setError("")
    setSavedNotice(false)
    try {
      const saved = await settingsGate.save(() => saveSettingsDraft(draft, baseline, {
        saveApiToken, deleteApiToken, saveAsrToken, deleteAsrToken, savePreferences: onSave,
      }))
      if (!mounted.current) return
      if (draftLanguage.interfaceLanguage !== baselineLanguage.interfaceLanguage
        || draftLanguage.pageLanguage !== baselineLanguage.pageLanguage) {
        await onLanguageChange(draftLanguage)
      }
      if (draftTheme !== baselineTheme) {
        await onThemeChange(draftTheme)
      }
      if (!mounted.current) return
      setDraft(saved)
      setBaseline(saved)
      setBaselineLanguage(draftLanguage)
      setBaselineTheme(draftTheme)
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

  const canTestConnection = Boolean(draft.apiToken.trim()) || isLoopbackBaseUrl(api.baseUrl)
  const leaveNotices = [
    ...(dirty ? [tr("有未保存的修改，离开后将放弃这些修改。")] : []),
    ...(asrTesting ? [tr("语音测试仍在进行，离开不会取消已提交的云端测试。")] : []),
  ]
  const clearLabel = confirmClearKind === "apiToken" ? tr("AI API 密钥") : tr("语音 API 密钥")

  return <section className={`settings-page${unifiedHeader ? " settings-page-unified" : ""}`} aria-labelledby="settings-heading">
    {unifiedHeader ? (
      <h1 id="settings-heading" className="sr-only">{tr("设置")}</h1>
    ) : (
      <header className="settings-heading">
        <button className="icon-button settings-back" type="button" aria-label={tr("返回")} title={tr("返回")} disabled={saving} onClick={onBack}>
          <ChevronLeft size={18} strokeWidth={2.1} aria-hidden="true" />
        </button>
        <h1 id="settings-heading">{tr("设置")}</h1>
      </header>
    )}
    <form className="settings-form" onSubmit={event => { void save(event) }} noValidate aria-busy={saving || loading}>
      {loadError && <div className="settings-load-error" role="alert">
        <p>{loadError}</p>
        <button type="button" className="text-button" disabled={loading || saving}
          onClick={() => void readKeys(preserveLoadDraft.current)}>{tr("重新读取")}</button>
      </div>}
      <LanguageSettingsCard
        value={draftLanguage}
        onChange={next => { setSavedNotice(false); setError(""); setDraftLanguage(next) }}
        theme={draftTheme}
        onThemeChange={next => { setSavedNotice(false); setError(""); setDraftTheme(next) }}
        disabled={disabled}
        error={languageError}
      />
      <section className="settings-card" aria-labelledby="settings-ai-heading">
        <header className="settings-card-header settings-card-header-provider">
          <div className="settings-card-title">
            <h2 id="settings-ai-heading">{tr("AI 生成")}</h2>
            <SettingsHelp label={tr("AI 工具说明")}>
              {tr("自定义 API 使用 OpenAI 兼容接口；地址填写 API 根地址，模型填写服务商提供的模型 ID。本机 localhost/127.0.0.1 服务可留空密钥。 Codex 和 Claude Code 使用本机已安装的工具，绿点仅表示找到程序。 密钥保存在本机私有文件中。")}
            </SettingsHelp>
          </div>
          <div className="settings-provider" role="radiogroup" aria-label={tr("默认 AI 工具")} id="provider">
            {(["api", "codex", "claude"] as Provider[]).map(value => (
              <label key={value} className={[provider === value ? "is-selected" : "", disabled ? "is-disabled" : ""].filter(Boolean).join(" ")}>
                <input type="radio" name="ai-provider" value={value} checked={provider === value} disabled={disabled}
                  onChange={() => update(current => ({ ...current, preferences: { ...current.preferences, provider: value } }), true)} />
                <span>{providerNames[value]}</span>
              </label>
            ))}
          </div>
        </header>
        <div className="settings-card-body">
          {provider === "api" ? <>
            <label className="settings-field" htmlFor="api-url">
              <span className="settings-label">{tr("API 地址")}</span>
              <input id="api-url" type="url" aria-label={tr("API 地址")} value={api.baseUrl} disabled={disabled}
                placeholder="https://api.example.com/v1" autoComplete="off" spellCheck={false}
                onChange={event => update(current => ({ ...current, preferences: {
                  ...current.preferences, api: { ...current.preferences.api, baseUrl: event.target.value },
                } }), true)} />
            </label>
            <label className="settings-field" htmlFor="api-model">
              <span className="settings-label">{tr("模型")}</span>
              <input id="api-model" type="text" aria-label={tr("API 模型名称")} value={api.model} disabled={disabled}
                placeholder={tr("填写模型 ID")} autoComplete="off" spellCheck={false}
                onChange={event => update(current => ({ ...current, preferences: {
                  ...current.preferences, api: { ...current.preferences.api, model: event.target.value },
                } }), true)} />
            </label>
            <SettingsSecretField id="api-token" label={tr("API 密钥")} value={draft.apiToken} savedValue={baseline.apiToken}
              onChange={apiToken => update(current => ({ ...current, apiToken }), true)}
              onClear={() => setConfirmClearKind("apiToken")} disabled={disabled} clearDisabled={testing || asrTesting} />
            <div className="settings-card-footer settings-connect-row">
              <button className={currentTestResult?.ok ? "secondary connection-ok" : "secondary"} type="button"
                disabled={disabled || testing || !canTestConnection}
                title={currentTestResult?.ok ? tr("重新测试当前配置") : tr("测试当前地址、模型和密钥")}
                onClick={() => void tryConnection()}>
                {testing ? <LoaderCircle size={15} className="spin" aria-hidden="true" />
                  : currentTestResult?.ok ? <span className="settings-green-dot" aria-hidden="true" /> : null}
                {testing ? tr("连接中…") : currentTestResult?.ok ? tr("已连接") : tr("测试连接")}
              </button>
            </div>
            <span className="sr-only" role="status">{currentTestResult?.ok ? tr("已连接") : ""}</span>
            {currentTestResult && !currentTestResult.ok && <p className="settings-test-error" role="alert">{currentTestResult.message}</p>}
          </> : <>
            <div className="settings-field">
              <label className="settings-label" htmlFor={`${provider}-model`}>{tr("模型")}</label>
              <div className="settings-field-control">
                <CliModelPicker
                  id={`${provider}-model`}
                  provider={provider}
                  catalog={cliCatalogs[provider]}
                  value={cliSettings[provider]}
                  disabled={disabled}
                  onChange={next => update(current => {
                    const existingCli = current.preferences.cli ?? defaultCliPreferences()
                    return {
                      ...current,
                      preferences: {
                        ...current.preferences,
                        cli: { ...existingCli, [provider]: next },
                      },
                    }
                  })}
                />
              </div>
            </div>
            <div className="settings-field">
              <label className="settings-label" htmlFor={`${provider}-path`}>{tr("程序路径")}</label>
              <div className="settings-field-control">
                <div className="settings-path-row">
                  <input id={`${provider}-path`} type="text" aria-label={tr("{0} 路径", providerNames[provider])} disabled={disabled}
                    placeholder={toolInfo?.executable || tr("留空自动查找")} value={paths[provider]} autoComplete="off" spellCheck={false}
                    onChange={event => update(current => ({ ...current, preferences: {
                      ...current.preferences, paths: { ...current.preferences.paths, [provider]: event.target.value },
                    } }))} />
                  <button type="button" className="secondary" disabled={disabled || pickingTool}
                    onClick={() => void chooseExecutable(provider)}>
                    <FolderOpen size={15} aria-hidden="true" /> {tr("选择…")}
                  </button>
                </div>
              </div>
            </div>
            <div className="settings-tool-status" role="status">
              {toolInfo?.executable ? <span className="settings-recognized" title={tr("仅表示已找到可执行文件，不代表已登录")}>
                <span className="settings-green-dot" aria-hidden="true" />{tr("已检测到")} {providerNames[provider]}
              </span> : toolError || (toolInfo ? tr("未检测到 {0}，请填写程序路径", providerNames[provider]) : tr("正在查找本机工具…"))}
              {provider === "claude" && toolInfo?.executable && !toolInfo.keyAvailable &&
                <p id="claude-auth-status" tabIndex={-1}>{tr("请为本机 Claude Code 配置 ANTHROPIC_API_KEY。")}</p>}
            </div>
          </>}
        </div>
      </section>
      <AsrSettingsPanel value={asr} onChange={asr => update(current => ({ ...current, preferences: { ...current.preferences, asr } }))}
        token={draft.asrToken} apiToken={draft.apiToken} savedToken={baseline.asrToken}
        onToken={asrToken => update(current => ({ ...current, asrToken }))}
        onClearToken={testing || asrTesting ? undefined : () => setConfirmClearKind("asrToken")}
        disabled={disabled} onTestingChange={setAsrTesting} />
      <YoutubeCookiesPanel value={youtubeCookies}
        onChange={next => update(current => ({ ...current, preferences: { ...current.preferences, youtubeCookies: next } }))}
        disabled={disabled} />
      {error && <p id="settings-error" className="settings-error" role="alert" tabIndex={-1}>{error}</p>}
      {settingsRecovery && !saving && <p className="settings-error" role="alert">
        {tr("上次未能完成设置保存。请核对服务地址和密钥，然后重新保存设置。")}
      </p>}
      <footer className="settings-actions">
        <span className="settings-save-status" role="status" data-state={savedNotice && !dirty ? "saved" : undefined}>
          {loading ? tr("正在读取配置…") : saving ? tr("正在保存…") : asrTesting ? tr("正在测试转写…") : testing ? tr("正在测试连接…")
            : dirty ? tr("有未保存的修改") : savedNotice ? tr("设置已保存") : ""}
        </span>
        <div className="settings-action-buttons">
          <button className="plain" type="button" disabled={saving} onClick={onBack}>{tr("取消")}</button>
          <button className="primary" type="submit" disabled={disabled || testing || asrTesting || (!dirty && !settingsRecovery)}
            aria-label={tr("保存设置")} title={tr("保存设置")}>
            {saving && <LoaderCircle size={15} className="spin" aria-hidden="true" />}{saving ? tr("保存中…") : tr("保存设置")}
          </button>
        </div>
      </footer>
    </form>
    {confirmClearKind && (
      <ConfirmDialog
        title={tr("删除已保存密钥")}
        message={tr("立即删除本机保存的{0}？\n取消设置不会恢复已删除的密钥。", clearLabel)}
        confirmLabel={tr("删除密钥")}
        danger
        onConfirm={() => executeClearCredential(confirmClearKind)}
        onCancel={() => setConfirmClearKind(undefined)}
      />
    )}
    {pendingLeaveAction && (
      <ConfirmDialog
        title={tr("离开设置")}
        message={leaveNotices.join("\n") + tr("\n仍要离开设置？")}
        confirmLabel={tr("放弃并离开")}
        danger={dirty}
        onConfirm={() => {
          const next = pendingLeaveAction
          setPendingLeaveAction(undefined)
          next()
        }}
        onCancel={() => setPendingLeaveAction(undefined)}
      />
    )}
  </section>
}
