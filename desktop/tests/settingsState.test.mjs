import assert from "node:assert/strict"
import test from "node:test"
import { defaultAsr } from "../src/asrSettings.ts"
import { setLocale } from "../src/language.ts"
import {
  SettingsSaveError, SettingsValidationError, normalizeSettingsDraft, settingsDraftEqual,
  validateApiConfiguration, validateAsrConfiguration, validateSettingsDraft, saveSettingsDraft,
} from "../src/settingsState.ts"

function draft() {
  return {
    preferences: {
      provider: "api", paths: { codex: "", claude: "" },
      api: { baseUrl: "https://api.example.com/v1", model: "example-model" },
      asr: { ...defaultAsr, enabled: true },
    },
    apiToken: "original-chat-key", asrToken: "original-asr-key",
  }
}

function storage(initial, fail = () => false) {
  const value = structuredClone(initial), calls = []
  const write = (operation, apply) => async argument => {
    calls.push(operation)
    if (fail(operation, argument)) throw new Error("Storage failure with an untrusted secret value")
    apply(argument)
  }
  return {
    value, calls,
    ports: {
      saveApiToken: write("save-api", token => { value.apiToken = token }),
      deleteApiToken: write("delete-api", () => { value.apiToken = "" }),
      saveAsrToken: write("save-asr", token => { value.asrToken = token }),
      deleteAsrToken: write("delete-asr", () => { value.asrToken = "" }),
      savePreferences: write("save-preferences", preferences => { value.preferences = structuredClone(preferences) }),
    },
  }
}

function fieldError(field) {
  return error => error instanceof SettingsValidationError && error.field === field
}

test("API configuration allows HTTP for IPv6 loopback only", () => {
  const baseUrl = "http://[::1]:8000/v1"
  assert.deepEqual(validateApiConfiguration({ baseUrl, model: "model" }, "test-token"), { baseUrl, model: "model" })
  for (const invalid of ["http://[::2]/v1", "http://example.com/v1"]) {
    assert.throws(() => validateApiConfiguration({ baseUrl: invalid, model: "model" }, "test-token"), fieldError("api-url"))
  }
})

test("normalization copies the draft, trims paste spacing and preserves invalid control characters", () => {
  const value = draft()
  value.preferences.api.baseUrl += "/ "
  value.preferences.api.model = " example-model "
  value.apiToken = " original-chat-key "
  value.preferences.paths.codex = " /Users/yano/Tools/codex "
  const normalized = normalizeSettingsDraft(value)
  assert.equal(normalized.preferences.api.baseUrl, "https://api.example.com/v1")
  assert.equal(normalized.preferences.api.model, "example-model")
  assert.equal(normalized.apiToken, "original-chat-key")
  assert.equal(normalized.preferences.paths.codex, "/Users/yano/Tools/codex")
  assert.equal(value.apiToken, " original-chat-key ")
  const controls = draft()
  controls.apiToken += "\n"
  assert.equal(normalizeSettingsDraft(controls).apiToken, "original-chat-key\n")
  assert.equal(settingsDraftEqual(controls, draft()), false)
  assert.throws(() => validateSettingsDraft(controls), fieldError("api-token"))
})

test("dirty comparison includes both credentials, explicit reuse, and all retained provider paths", () => {
  assert.equal(settingsDraftEqual(draft(), structuredClone(draft())), true)
  const spaced = draft(); spaced.apiToken = " original-chat-key "
  assert.equal(settingsDraftEqual(draft(), spaced), true)
  for (const update of [
    next => { next.apiToken = "replacement" },
    next => { next.asrToken = "replacement" },
    next => { next.preferences.asr.reuseToken = true },
    next => { next.preferences.paths.claude = "/tmp/claude" },
    next => { next.preferences.asr.ffmpegPath = "/tmp/ffmpeg" },
  ]) {
    const next = draft(); update(next)
    assert.equal(settingsDraftEqual(draft(), next), false)
  }
})

test("a dormant legacy CLI path does not block saving another provider", () => {
  const value = draft()
  value.preferences.paths.codex = "codex"
  assert.doesNotThrow(() => validateSettingsDraft(value))
  value.preferences.provider = "claude"
  assert.doesNotThrow(() => validateSettingsDraft(value))
  value.preferences.provider = "codex"
  assert.throws(() => validateSettingsDraft(value), fieldError("codex-path"))
  value.preferences.paths.codex = ""
  assert.doesNotThrow(() => validateSettingsDraft(value))
})

test("invalid ASR settings or dormant malformed credentials cannot partially save an AI key", async () => {
  for (const [update, field] of [
    [next => { next.preferences.asr.maxMinutes = 0 }, "asr-max-minutes"],
    [next => { next.preferences.asr.baseUrl = "https://api.example.com/v1" }, "asr-url"],
    [next => { next.preferences.asr.chunkMinutes = 7 }, "asr-chunk-minutes"],
    [next => { next.preferences.asr.ffmpegPath = "relative/ffmpeg" }, "ffmpeg-path"],
    [next => { next.preferences.asr.enabled = false; next.asrToken = "bad\u0085key" }, "asr-token"],
  ]) {
    const previous = draft(), next = draft(), target = storage(previous)
    next.apiToken = "replacement-chat-key"; update(next)
    await assert.rejects(saveSettingsDraft(next, previous, target.ports), fieldError(field))
    assert.deepEqual(target.calls, [])
    assert.deepEqual(target.value, previous)
  }
})

test("API validation accepts HTTPS and local HTTP roots and rejects unsafe or endpoint URLs", () => {
  for (const baseUrl of ["https://api.example.com/v1/", "http://localhost:8000/v1", "http://127.0.0.1:1234", "http://[::1]:8888/v1"]) {
    assert.doesNotThrow(() => validateApiConfiguration({ baseUrl, model: "model" }, "key"))
  }
  for (const baseUrl of ["", "http://remote.example/v1", "https://user:password@example.com/v1", "https://example.com/v1?", "https://example.com/v1#", "https://example.com/v1/chat/completions/", "https://example.com/\nv1"]) {
    assert.throws(() => validateApiConfiguration({ baseUrl, model: "model" }, "key"), fieldError("api-url"))
  }
  for (const model of ["", "a\nb", "汉".repeat(86)]) {
    assert.throws(() => validateApiConfiguration({ baseUrl: "https://example.com/v1", model }, "key"), fieldError("api-model"))
  }
  assert.throws(() => validateApiConfiguration({ baseUrl: "https://example.com/v1", model: "model" }, "汉".repeat(2731)), fieldError("api-token"))
})

test("ASR validates the allowlisted native endpoint, model and duration before a paid test", () => {
  for (const baseUrl of ["https://dashscope.aliyuncs.com/api/v1", "https://dashscope-intl.aliyuncs.com/api/v1/", "https://tenant.cn-beijing.maas.aliyuncs.com/api/v1"]) {
    assert.doesNotThrow(() => validateAsrConfiguration({ ...defaultAsr, baseUrl }, "speech-key"))
  }
  for (const baseUrl of ["http://dashscope.aliyuncs.com/api/v1", "https://dashscope.aliyuncs.com:1234/api/v1", "https://dashscope.aliyuncs.com/compatible-mode/v1", "https://evil.cn-beijing.maas.aliyuncs.com.example.com/api/v1", "https://dashscope.aliyuncs.com/api/v1#"]) {
    assert.throws(() => validateAsrConfiguration({ ...defaultAsr, baseUrl }, "speech-key"), fieldError("asr-url"))
  }
  assert.throws(() => validateAsrConfiguration({ ...defaultAsr, model: "other" }, "key"), fieldError("asr-model"))
  assert.throws(() => validateAsrConfiguration({ ...defaultAsr, maxMinutes: 1.5 }, "key"), fieldError("asr-max-minutes"))
  assert.throws(() => validateAsrConfiguration(defaultAsr, ""), fieldError("asr-token"))
})

test("explicit ASR key reuse requires the current AI draft even when a CLI provider is active", async () => {
  const previous = draft(), next = draft(), target = storage(previous)
  next.preferences.provider = "codex"
  next.preferences.asr.reuseToken = true
  next.apiToken = ""
  await assert.rejects(saveSettingsDraft(next, previous, target.ports), fieldError("api-token"))
  assert.deepEqual(target.calls, [])
  next.apiToken = "current-chat-key"; next.asrToken = ""
  await saveSettingsDraft(next, previous, target.ports)
  assert.equal(target.value.apiToken, "current-chat-key")
  assert.equal(target.value.preferences.asr.reuseToken, true)
})

test("an independent speech key is required until reuse is explicitly selected", () => {
  const value = draft(); value.asrToken = ""
  assert.throws(() => validateSettingsDraft(value), fieldError("asr-token"))
  value.preferences.asr.reuseToken = true
  assert.doesNotThrow(() => validateSettingsDraft(value))
})

test("a successful save writes changed keys then preferences and keeps secrets out of preferences", async () => {
  const previous = draft(), next = draft(), target = storage(previous)
  next.apiToken = "new-chat-key"; next.asrToken = "new-speech-key"
  next.preferences.api.model = "new-model"
  const saved = await saveSettingsDraft(next, previous, target.ports)
  assert.deepEqual(target.calls, ["save-api", "save-asr", "save-preferences"])
  assert.deepEqual(target.value, saved)
  assert.deepEqual(saved, next)
  assert.equal(JSON.stringify(target.value.preferences).includes("new-chat-key"), false)
  assert.equal(JSON.stringify(target.value.preferences).includes("new-speech-key"), false)
})

test("a failed second credential write restores the first key and does not apply preferences", async () => {
  const previous = draft(), next = draft(), target = storage(previous, operation => operation === "save-asr")
  next.apiToken = "new-chat-key"; next.asrToken = "new-speech-key"
  await assert.rejects(saveSettingsDraft(next, previous, target.ports), error => {
    assert.equal(error instanceof SettingsSaveError, true)
    assert.equal(error.rollbackFailed, false)
    assert.match(error.message, /已恢复/)
    assert.equal(error.message.includes("untrusted secret"), false)
    return true
  })
  assert.deepEqual(target.calls, ["save-api", "save-asr", "save-api"])
  assert.deepEqual(target.value, previous)
})

test("a preference persistence failure restores both keys in reverse order", async () => {
  const previous = draft(), next = draft(), target = storage(previous, operation => operation === "save-preferences")
  next.apiToken = "new-chat-key"; next.asrToken = "new-speech-key"
  await assert.rejects(saveSettingsDraft(next, previous, target.ports), SettingsSaveError)
  assert.deepEqual(target.calls, ["save-api", "save-asr", "save-preferences", "save-asr", "save-api"])
  assert.deepEqual(target.value, previous)
})

test("recovery restores the exact previously loaded credential bytes", async () => {
  const previous = draft(), next = draft()
  previous.apiToken = " original-chat-key "
  next.apiToken = "replacement-key"
  const target = storage(previous, operation => operation === "save-preferences")
  await assert.rejects(saveSettingsDraft(next, previous, target.ports), SettingsSaveError)
  assert.equal(target.value.apiToken, previous.apiToken)
})

test("rollback failure is explicit and does not prevent restoration of the remaining key", async () => {
  const previous = draft(), next = draft()
  const target = storage(previous, (operation, value) => operation === "save-preferences"
    || operation === "save-asr" && value === "original-asr-key")
  next.apiToken = "new-chat-key"; next.asrToken = "new-speech-key"
  await assert.rejects(saveSettingsDraft(next, previous, target.ports), error => {
    assert.equal(error instanceof SettingsSaveError, true)
    assert.equal(error.rollbackFailed, true)
    assert.match(error.message, /重新读取/)
    return true
  })
  assert.deepEqual(target.calls, ["save-api", "save-asr", "save-preferences", "save-asr", "save-api"])
  assert.equal(target.value.apiToken, previous.apiToken)
  assert.equal(target.value.asrToken, next.asrToken)
})

test("staged deletion applies only on save, is recoverable, and cannot remove an active required key", async () => {
  const previous = draft(), next = draft()
  next.preferences.provider = "codex"; next.preferences.asr.enabled = false
  next.apiToken = ""; next.asrToken = ""
  const failed = storage(previous, operation => operation === "save-preferences")
  await assert.rejects(saveSettingsDraft(next, previous, failed.ports), SettingsSaveError)
  assert.deepEqual(failed.calls, ["delete-api", "delete-asr", "save-preferences", "save-asr", "save-api"])
  assert.deepEqual(failed.value, previous)
  const target = storage(previous)
  await saveSettingsDraft(next, previous, target.ports)
  assert.equal(target.value.apiToken, "")
  assert.equal(target.value.asrToken, "")
  next.preferences.provider = "api"
  assert.throws(() => validateSettingsDraft(next), fieldError("api-token"))
})

test("no-op and preference-only saves do not rewrite credentials", async () => {
  const previous = draft(), next = draft(), target = storage(previous)
  await saveSettingsDraft(next, previous, target.ports)
  assert.deepEqual(target.calls, [])
  next.preferences.api.model = "changed-model"
  await saveSettingsDraft(next, previous, target.ports)
  assert.deepEqual(target.calls, ["save-preferences"])
})

test("the save operates on a snapshot rather than later form mutations", async () => {
  const previous = draft(), next = draft(), target = storage(previous)
  next.apiToken = "new-chat-key"; next.asrToken = "new-speech-key"
  const saveApiToken = target.ports.saveApiToken
  target.ports.saveApiToken = async token => {
    next.asrToken = "late-edit"
    next.preferences.api.model = "late-model"
    await saveApiToken(token)
  }
  const saved = await saveSettingsDraft(next, previous, target.ports)
  assert.equal(saved.asrToken, "new-speech-key")
  assert.equal(saved.preferences.api.model, "example-model")
  assert.deepEqual(target.value, saved)
})

test("youtube cookie settings validate browser and absolute file paths explicitly", () => {
  const value = draft()
  value.preferences.youtubeCookies = { mode: "none", browser: "chrome", filePath: "" }
  assert.equal(validateSettingsDraft(value).preferences.youtubeCookies.mode, "none")

  value.preferences.youtubeCookies = { mode: "browser", browser: " Safari ", filePath: "" }
  assert.equal(validateSettingsDraft(value).preferences.youtubeCookies.browser, "safari")

  value.preferences.youtubeCookies = { mode: "browser", browser: "unknown", filePath: "" }
  assert.throws(() => validateSettingsDraft(value), fieldError("youtube-cookie-browser"))

  value.preferences.youtubeCookies = { mode: "file", browser: "chrome", filePath: "" }
  assert.throws(() => validateSettingsDraft(value), fieldError("youtube-cookie-file"))

  value.preferences.youtubeCookies = { mode: "file", browser: "chrome", filePath: "relative/cookies.txt" }
  assert.throws(() => validateSettingsDraft(value), fieldError("youtube-cookie-file"))

  value.preferences.youtubeCookies = { mode: "file", browser: "chrome", filePath: " /Users/yano/cookies.txt " }
  assert.equal(validateSettingsDraft(value).preferences.youtubeCookies.filePath, "/Users/yano/cookies.txt")
})

test("settings save failure message localizes cleanly in English without mixed Chinese text", async () => {
  const previous = draft(), next = draft()
  next.apiToken = "new-chat-key"
  const target = storage(previous, operation => operation === "save-preferences")
  setLocale("en")
  try {
    await assert.rejects(saveSettingsDraft(next, previous, target.ports), error => {
      assert.equal(error instanceof SettingsSaveError, true)
      assert.equal(error.rollbackFailed, false)
      assert.doesNotMatch(error.message, /\p{Script=Han}/u)
      assert.match(error.message, /Could not save/)
      return true
    })
  } finally {
    setLocale("zh-CN")
  }
})
