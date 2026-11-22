import assert from "node:assert/strict"
import test from "node:test"
import { readdirSync, readFileSync, mkdtempSync, rmSync } from "node:fs"
import { dirname, join, relative, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { tmpdir } from "node:os"
import { createLogger, createServer, loadConfigFromFile } from "vite"

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..")
function files(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name)
    return entry.isDirectory() ? files(path) : [path]
  })
}
function ambiguousNames(paths) {
  const byStem = new Map()
  for (const path of paths) {
    if (!/\.(?:tsx?|jsx?|mts|cts|mjs|cjs)$/.test(path) || /\.d\.(?:ts|mts|cts)$/.test(path)) continue
    const stem = path.replace(/\.(?:tsx?|jsx?|mts|cts|mjs|cjs)$/, "").toLowerCase()
    const group = byStem.get(stem) ?? []
    group.push(path)
    byStem.set(stem, group)
  }
  return [...byStem.values()].filter(group => group.length > 1)
}

test("case-insensitive module guard catches the original ASR collision", () => {
  assert.equal(ambiguousNames(["src/AsrSettings.tsx", "src/asrSettings.ts"]).length, 1)
  assert.deepEqual(ambiguousNames(["src/AsrSettingsPanel.tsx", "src/asrSettings.ts"]), [])
})
test("all source module stems are unambiguous on macOS", () => {
  const paths = files(join(root, "src")).map(path => relative(root, path))
  assert.deepEqual(ambiguousNames(paths), [], "Do not distinguish importable modules by case or extension alone")
  const main = readFileSync(join(root, "src/main.tsx"), "utf8")
  assert.match(main, /import \{ Settings\b[^}]*\} from "\.\/Settings"/)
  const settings = readFileSync(join(root, "src/Settings.tsx"), "utf8")
  assert.match(settings, /import \{ AsrSettingsPanel \} from "\.\/AsrSettingsPanel"/)
  assert.match(settings, /<AsrSettingsPanel\s/)
})
test("Vite dependency scan excludes native and sidecar HTML artifacts", async () => {
  const loaded = await loadConfigFromFile({ command: "serve", mode: "development" }, join(root, "vite.config.ts"), root)
  assert.ok(loaded)
  assert.deepEqual(loaded.config.optimizeDeps.entries, ["index.html"])
  assert.deepEqual(loaded.config.server.watch.ignored, ["**/src-tauri/**", "**/build/**"])
})
test("Vite dev server resolves the ASR panel with a fresh dependency cache", { timeout: 30000 }, async () => {
  const cache = mkdtempSync(join(tmpdir(), "linkread-vite-test-"))
  const errors = []
  const logger = createLogger("silent")
  logger.error = message => { errors.push(String(message)) }
  let server
  try {
    server = await createServer({
      root,
      configFile: join(root, "vite.config.ts"),
      cacheDir: cache,
      customLogger: logger,
      server: { host: "127.0.0.1", port: 0, strictPort: false, watch: null },
      optimizeDeps: { force: true },
    })
    await server.listen()
    const address = server.httpServer.address()
    const base = `http://127.0.0.1:${address.port}`
    for (const path of ["/", "/src/main.tsx", "/src/Settings.tsx", "/src/settingsState.ts", "/src/AsrSettingsPanel.tsx", "/src/asrSettings.ts"]) {
      const response = await fetch(base + path)
      assert.equal(response.status, 200, path)
      const body = await response.text()
      if (path === "/src/AsrSettingsPanel.tsx") assert.match(body, /function AsrSettingsPanel\(/)
    }
    // Wait for optimizer results instead of sleeping or assuming that listen() is enough.
    const { optimizeDeps, resolveConfig } = await import("vite")
    const config = await resolveConfig({ root, configFile: join(root, "vite.config.ts"), cacheDir: join(cache, "explicit"), customLogger: logger }, "serve")
    const optimized = await optimizeDeps(config, true)
    assert.ok(optimized.optimized["react"])
    assert.deepEqual(errors, [])
  } finally {
    if (server) await server.close()
    rmSync(cache, { recursive: true, force: true })
  }
})
