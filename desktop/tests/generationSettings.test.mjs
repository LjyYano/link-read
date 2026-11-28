import assert from "node:assert/strict"
import test from "node:test"
import { defaultAsr } from "../src/asrSettings.ts"
import { GenerationSettingsGate, taskSettingsForGeneration } from "../src/generationSettings.ts"
import { parseTaskJournal, taskSnapshot } from "../src/taskJournal.ts"
import { saveSettingsDraft, SettingsSaveError } from "../src/settingsState.ts"
import { SerialTaskQueue } from "../src/taskQueue.ts"

function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}
const tick = () => new Promise(resolve => setImmediate(resolve))

function setup() {
  const previous = {
    preferences: {
      provider: "api", paths: { codex: "", claude: "" },
      api: { baseUrl: "https://old.example.com/v1", model: "old-chat" },
      asr: { ...defaultAsr, enabled: true },
    },
    apiToken: "old-api-token", asrToken: "old-asr-token",
  }
  const saved = structuredClone(previous)
  const snapshot = { provider: previous.preferences.provider, ...previous.preferences.api, asr: previous.preferences.asr }
  let pendingWrite = false
  const reload = () => new GenerationSettingsGate({
    readPreferences: () => saved.preferences,
    hasPendingWrite: () => pendingWrite,
    setPendingWrite: pending => { pendingWrite = pending },
  })
  return { previous, saved, snapshot, gate: reload(), reload, isPending: () => pendingWrite }
}

function enqueue(queue, gate, snapshot, generateArticle) {
  const done = deferred()
  let release, status = "queued"
  queue.enqueue({
    id: "queued",
    beforeStart: async () => { release = await gate.admit(snapshot) },
    onStart: () => { status = "running" },
    run: () => generateArticle(() => release()),
    onSuccess: article => done.resolve({ status: "done", article }),
    onFailure: failure => done.resolve({ status: "error", failure }),
    onFinish: () => release?.(),
  })
  return { status: () => status, done: done.promise }
}

for (const [field, change] of Object.entries({
  "AI provider": settings => { settings.provider = "claude" },
  "API address": settings => { settings.api.baseUrl = "https://new.example.com/v1" },
  "API model": settings => { settings.api.model = "new-chat" },
  "ASR enabled": settings => { settings.asr.enabled = false },
  "ASR address": settings => { settings.asr.baseUrl = "https://dashscope-intl.aliyuncs.com/api/v1" },
  "ASR model": settings => { settings.asr.model = "new-speech" },
  "ASR credential source": settings => { settings.asr.reuseToken = !settings.asr.reuseToken },
})) {
  test(`a queued task fails before native generation when the saved ${field} changes`, async () => {
    const { saved, snapshot, gate } = setup()
    const queue = new SerialTaskQueue()
    const active = deferred()
    queue.enqueue({ id: "active", onStart() {}, run: () => active.promise, onSuccess() {}, onFailure: assert.fail })
    const generateArticle = () => assert.fail("a stale task must not invoke native generation")
    const task = enqueue(queue, gate, snapshot, generateArticle)
    change(saved.preferences)
    active.resolve()
    const result = await task.done
    assert.equal(task.status(), "queued", "the task never entered the running state")
    assert.equal(result.status, "error")
    assert.deepEqual(result.failure, {
      message: "设置已变更，请重试以使用当前设置。", code: "settings_changed", stage: null, retryable: true, action: "retry",
    })
    assert.equal(queue.activeId, null)
  })
}

test("a task with matching saved API and ASR settings starts generation", async () => {
  const { gate, snapshot } = setup()
  let calls = 0
  const generateArticle = async acknowledge => { calls++; acknowledge(); return "article" }
  const task = enqueue(new SerialTaskQueue(), gate, snapshot, generateArticle)
  assert.deepEqual(await task.done, { status: "done", article: "article" })
  assert.equal(calls, 1)
})

test("startup stays queued while new API and ASR keys are saved ahead of the preferences", async () => {
  const { gate, previous, saved, snapshot } = setup()
  const next = structuredClone(previous)
  next.preferences.api.baseUrl = "https://new.example.com/v1"
  next.preferences.asr.baseUrl = "https://dashscope-intl.aliyuncs.com/api/v1"
  next.apiToken = "new-api-token"
  next.asrToken = "new-asr-token"
  const writePreferences = deferred(), reachedPreferences = deferred()
  const save = gate.save(() => saveSettingsDraft(next, previous, {
    saveApiToken: async token => { saved.apiToken = token },
    saveAsrToken: async token => { saved.asrToken = token },
    savePreferences: async preferences => {
      reachedPreferences.resolve()
      await writePreferences.promise
      saved.preferences = preferences
    },
  }))
  await reachedPreferences.promise
  assert.equal(saved.apiToken, next.apiToken)
  assert.equal(saved.asrToken, next.asrToken)
  assert.deepEqual(saved.preferences, previous.preferences)
  const task = enqueue(new SerialTaskQueue(), gate, snapshot, () => assert.fail("native generation must not start"))
  await tick()
  assert.equal(task.status(), "queued")
  writePreferences.resolve()
  await save
  assert.equal((await task.done).failure.code, "settings_changed")
})

test("settings can save after native credential capture while generation is still running", async () => {
  const { gate, snapshot } = setup()
  const nativeStarted = deferred(), generation = deferred()
  const task = enqueue(new SerialTaskQueue(), gate, snapshot, acknowledge => {
    nativeStarted.resolve(acknowledge)
    return generation.promise
  })
  const acknowledge = await nativeStarted.promise
  let written = false
  const saved = gate.save(async () => { written = true })
  await tick()
  assert.equal(written, false)
  acknowledge()
  await saved
  assert.equal(written, true)
  assert.equal(task.status(), "running")
  generation.resolve("article")
  await task.done
})

test("an incomplete rollback blocks generation across reloads until settings are saved", async () => {
  const { gate, snapshot, reload, isPending } = setup()
  await assert.rejects(gate.save(async () => { throw new SettingsSaveError("rollback incomplete", true) }), /rollback incomplete/)
  assert.equal(isPending(), true)
  const reopened = reload()
  await assert.rejects(reopened.admit(snapshot), { code: "settings_pending" })
  await assert.rejects(reopened.save(async () => { throw new SettingsSaveError("validation/write failed") }), /validation\/write failed/)
  assert.equal(isPending(), true)
  await reopened.save(async () => {})
  assert.equal(isPending(), false)
  const release = await reopened.admit(snapshot)
  release()
})

test("YouTube task with ASR enabled and unconfigured cookies is admitted without infinite settings_changed loop", async () => {
  const { saved, gate } = setup()
  saved.preferences.asr.enabled = true
  saved.preferences.youtubeCookies = { mode: "none", browser: "chrome", filePath: "" }
  const ytUrl = "https://www.youtube.com/watch?v=1-Fo8McCD9w"
  const settings = taskSettingsForGeneration(saved.preferences, { isYoutube: true, pageLanguage: "original" })
  assert.equal(settings.asr.enabled, false, "effective ASR stays disabled on YouTube without cookies")
  const spec = {
    ...settings,
    input: ytUrl,
    localFile: false,
    youtubeCookies: { ...saved.preferences.youtubeCookies },
  }
  const release = await gate.admit(spec)
  release()

  // Once the user configures YouTube cookies while queued, admission detects the change.
  saved.preferences.youtubeCookies = { mode: "browser", browser: "chrome", filePath: "" }
  await assert.rejects(gate.admit(spec), { code: "settings_changed" })
})

test("dormant invalid API or ASR settings do not poison task journal creation or gate admission", async () => {
  const { saved, gate } = setup()
  saved.preferences.provider = "codex"
  saved.preferences.api = { baseUrl: "https://incomplete?bad=1", model: "bad\nmodel" }
  saved.preferences.asr = { ...defaultAsr, enabled: false, baseUrl: "not-a-url", maxMinutes: 0, chunkMinutes: 99 }
  const settings = taskSettingsForGeneration(saved.preferences, { isYoutube: false, pageLanguage: "original" })
  assert.equal(settings.baseUrl, "")
  assert.equal(settings.model, "")
  assert.equal(settings.asr.enabled, false)
  assert.equal(settings.asr.maxMinutes, 120)
  assert.equal(settings.asr.chunkMinutes, 5)
  const spec = {
    ...settings,
    input: "https://www.bilibili.com/video/BV1rxqmBhE91?p=1",
    localFile: false,
    sourceKey: "bilibili-BV1rxqmBhE91-p1",
    replace: false,
    identity: {},
  }
  const snap = taskSnapshot("task_dormant", spec, { status: "queued", message: "waiting" }, 1000, 1000)
  assert.equal(parseTaskJournal({ version: 1, tasks: [snap] }).tasks.length, 1)
  const release = await gate.admit(spec)
  release()
})
