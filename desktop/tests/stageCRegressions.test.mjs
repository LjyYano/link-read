import assert from "node:assert/strict"
import test from "node:test"
import { readdirSync, readFileSync } from "node:fs"
import { join } from "node:path"
import { fileURLToPath } from "node:url"
import { defaultAsr } from "../src/asrSettings.ts"
import {
  isLoopbackBaseUrl, SettingsValidationError,
  validateApiConfiguration, validateSettingsDraft,
} from "../src/settingsState.ts"
import { loadThemeMode, nextThemeMode, resolveTheme, saveThemeMode, themeStorageKey } from "../src/themeState.ts"

const srcDir = fileURLToPath(new URL("../src", import.meta.url))

function makeStorage(initial = {}) {
  const map = new Map(Object.entries(initial))
  return {
    getItem: key => map.get(key) ?? null,
    setItem: (key, value) => { map.set(key, value) },
    map,
  }
}

test("themeState validates stored modes and resolves system/explicit themes", () => {
  const storage = makeStorage()
  assert.equal(loadThemeMode(storage), "system")
  saveThemeMode("dark", storage)
  assert.equal(storage.getItem(themeStorageKey), "dark")
  assert.equal(loadThemeMode(storage), "dark")
  storage.setItem(themeStorageKey, "unexpected")
  assert.equal(loadThemeMode(storage), "system")

  assert.equal(resolveTheme("system", false), "light")
  assert.equal(resolveTheme("system", true), "dark")
  assert.equal(resolveTheme("light", true), "light")
  assert.equal(resolveTheme("dark", false), "dark")

  assert.equal(nextThemeMode("system"), "light")
  assert.equal(nextThemeMode("light"), "dark")
  assert.equal(nextThemeMode("dark"), "system")
})

test("settingsState allows omitted API tokens only for HTTP loopback providers", () => {
  for (const baseUrl of [
    "http://localhost:11434/v1",
    "http://127.0.0.1:8000/v1",
    "http://[::1]:8080/v1",
  ]) {
    assert.equal(isLoopbackBaseUrl(baseUrl), true)
    assert.deepEqual(validateApiConfiguration({ baseUrl, model: "qwen" }, ""), { baseUrl, model: "qwen" })
    const validDraft = validateSettingsDraft({
      preferences: {
        provider: "api",
        paths: { codex: "", claude: "" },
        api: { baseUrl, model: "qwen" },
        asr: { ...defaultAsr },
      },
      apiToken: "",
      asrToken: "",
    })
    assert.equal(validDraft.apiToken, "")
  }

  for (const baseUrl of ["https://api.example.com/v1", "https://localhost:8443/v1"]) {
    assert.equal(isLoopbackBaseUrl(baseUrl), false)
    assert.throws(
      () => validateApiConfiguration({ baseUrl, model: "gpt" }, ""),
      error => error instanceof SettingsValidationError && error.field === "api-token",
    )
  }

  assert.throws(
    () => validateSettingsDraft({
      preferences: {
        provider: "api",
        paths: { codex: "", claude: "" },
        api: { baseUrl: "http://localhost:11434/v1", model: "qwen" },
        asr: { ...defaultAsr, enabled: true, reuseToken: true },
      },
      apiToken: "",
      asrToken: "",
    }),
    error => error instanceof SettingsValidationError && error.field === "api-token",
  )
})

test("Stage C static guards: no blocking confirm, Safari 15 incompatible CSS, or dark media queries", () => {
  const files = readdirSync(srcDir)
  for (const name of files) {
    if (!/\.(ts|tsx|css)$/.test(name)) continue
    const content = readFileSync(join(srcDir, name), "utf8")
    assert.doesNotMatch(content, /\bwindow\.confirm\b/, `${name} still calls window.confirm`)
    assert.doesNotMatch(content, /\bObject\.hasOwn\(/, `${name} uses Object.hasOwn`)
    if (name.endsWith(".css")) {
      assert.doesNotMatch(content, /\bcolor-mix\(/, `${name} uses color-mix()`)
      assert.doesNotMatch(content, /\blight-dark\(/, `${name} uses light-dark()`)
      assert.doesNotMatch(content, /@media\s*\(\s*prefers-color-scheme:\s*dark\s*\)/, `${name} relies on media dark mode instead of [data-theme="dark"]`)
    }
  }

  const bookshelf = readFileSync(join(srcDir, "BookshelfSection.tsx"), "utf8")
  assert.match(bookshelf, /className="shelf-list"/)
  assert.match(bookshelf, /className="shelf-row"/)
  assert.match(bookshelf, /className="shelf-media"><Cover \{\.\.\.article\} withBackdrop \/>/)

  const homeCss = readFileSync(join(srcDir, "home.css"), "utf8")
  assert.doesNotMatch(homeCss, /\.shelf-media\s*>\s*\.shelf-cover\[data-platform="apple_podcasts"\]\s*\{\s*width:\s*auto/)
  assert.match(homeCss, /\.shelf-media\s*>\s*\.shelf-cover\[data-platform="apple_podcasts"\][^}]*aspect-ratio:\s*auto/)
  assert.match(homeCss, /\.shelf-media\s+\.shelf-cover\[data-platform="apple_podcasts"\]\s*>\s*img\s*\{\s*background:\s*transparent/)
  assert.match(homeCss, /\.shelf-cover\[data-platform="apple_podcasts"\]\s*>\s*img\.cover-backdrop\s*\{[^}]*object-fit:\s*cover/)

  const reader = readFileSync(join(srcDir, "Reader.tsx"), "utf8")
  assert.match(reader, /isAllowedReaderOrigin\(event\.origin\)/)

  const settings = readFileSync(join(srcDir, "Settings.tsx"), "utf8")
  assert.match(settings, /pickExecutable/)
  assert.match(settings, /<ConfirmDialog/)

  const jobCard = readFileSync(join(srcDir, "JobCard.tsx"), "utf8")
  const home = readFileSync(join(srcDir, "Home.tsx"), "utf8")
  const taskCenter = readFileSync(join(srcDir, "TaskCenter.tsx"), "utf8")
  const main = readFileSync(join(srcDir, "main.tsx"), "utf8")
  assert.doesNotMatch(jobCard, /先读原文|task-read-transcript|onPreview/)
  assert.match(jobCard, /重试未完成部分/)
  assert.doesNotMatch(home, /\bonPreview\b/)
  assert.doesNotMatch(taskCenter, /\bonPreview\b/)
  assert.doesNotMatch(main, /\breadPreview\b|\bpreviewTaskId\b/)
  assert.doesNotMatch(reader, /\bPreviewTask\b|View finished article|Original transcript · AI/)
  assert.match(reader, /standalonePreview/)
})
