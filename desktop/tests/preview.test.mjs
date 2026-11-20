import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import { Script } from "node:vm"

const preview = readFileSync(new URL("../src-tauri/src/preview.html", import.meta.url), "utf8")
const reader = readFileSync(new URL("../../src/linkread_desktop/renderers/reader.html", import.meta.url), "utf8")

test("embedded reading page preserves the web reader's horizontal tab bar", () => {
  assert.match(reader, /class="tabs"/)
  assert.match(reader, /class="tabs-scroll"/)
  assert.match(preview, /linkread-desktop \.tabs \{ top: 0; \}/)
  assert.doesNotMatch(preview, /\.tabs[^}]*display:\s*none/)
  assert.doesNotMatch(preview, /sidebar\.replaceChildren|desktop-nav|desktop-story/)
})

test("desktop preview script is syntactically valid and keeps native integration", () => {
  const match = preview.match(/<script>([\s\S]*?)<\/script>/)
  assert.ok(match)
  assert.doesNotThrow(() => new Script(match[1]))
  assert.match(preview, /linkReadCommand/)
  assert.match(preview, /linkReadExternal/)
  assert.match(preview, /linkReadGoBack/)
})
