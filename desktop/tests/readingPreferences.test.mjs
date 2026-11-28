import assert from "node:assert/strict"
import test from "node:test"
import {
  defaultReadingPreferences,
  isValidCustomFont,
  loadReadingPreferences,
  normalizeReadingPreferences,
  readingPreferencesEqual,
  readingPreferencesStorageKey,
  resolveEffectiveReaderTheme,
  saveReadingPreferencesLocal,
} from "../src/readingPreferences.ts"

function makeStorage(initial = {}) {
  const map = new Map(Object.entries(initial))
  return {
    getItem: key => map.get(key) ?? null,
    setItem: (key, value) => { map.set(key, value) },
  }
}

test("isValidCustomFont accepts local font names and rejects CSS/HTML metacharacters", () => {
  for (const ok of ["PingFang SC", "LXGW WenKai", "Iowan Old Style", "Noto Serif CJK SC", "SF Pro Text"]) {
    assert.equal(isValidCustomFont(ok), true, ok)
  }
  for (const bad of [
    "",
    "   ",
    "Font; } body { display: none",
    'Font" onclick="alert(1)',
    "Font</style><script>",
    "Font\ncolor:red",
    "a".repeat(129),
    null,
    123,
  ]) {
    assert.equal(isValidCustomFont(bad), false, String(bad))
  }
})

test("normalizeReadingPreferences sanitizes fields and falls back custom font safely", () => {
  const defaults = defaultReadingPreferences()
  assert.deepEqual(normalizeReadingPreferences(null), defaults)
  assert.deepEqual(normalizeReadingPreferences({
    theme: "sepia",
    fontFamily: "custom",
    customFont: "PingFang SC",
    fontSize: 20,
    lineWidth: "narrow",
    lineSpacing: "standard",
    defaultTab: "summary",
    showCover: false,
    showHighlights: false,
  }), {
    theme: "sepia",
    fontFamily: "custom",
    customFont: "PingFang SC",
    fontSize: 20,
    lineWidth: "narrow",
    lineSpacing: "standard",
    defaultTab: "summary",
    showCover: false,
    showHighlights: false,
  })

  const fallback = normalizeReadingPreferences({
    theme: "neon",
    fontFamily: "custom",
    customFont: "Evil</style>",
    fontSize: 99,
    lineWidth: "ultra",
    lineSpacing: "huge",
    defaultTab: "mindmap",
    showCover: "yes",
    showHighlights: 0,
  })
  assert.deepEqual(fallback, defaults)
})

test("reading preferences round-trip through storage and resolve effective reader theme", () => {
  const storage = makeStorage()
  assert.deepEqual(loadReadingPreferences(storage), defaultReadingPreferences())
  const custom = normalizeReadingPreferences({
    theme: "sepia",
    fontFamily: "serif",
    fontSize: 18,
    lineWidth: "wide",
    lineSpacing: "compact",
    defaultTab: "summary",
    showCover: false,
    showHighlights: true,
  })
  saveReadingPreferencesLocal(custom, storage)
  assert.ok(storage.getItem(readingPreferencesStorageKey))
  assert.equal(readingPreferencesEqual(loadReadingPreferences(storage), custom), true)

  assert.equal(resolveEffectiveReaderTheme("auto", "light"), "light")
  assert.equal(resolveEffectiveReaderTheme("auto", "dark"), "dark")
  assert.equal(resolveEffectiveReaderTheme("sepia", "dark"), "sepia")
  assert.equal(resolveEffectiveReaderTheme("light", "dark"), "light")
  assert.equal(resolveEffectiveReaderTheme("dark", "light"), "dark")
})
