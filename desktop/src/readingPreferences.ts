import type { ResolvedTheme } from "./themeState"

export type ReaderThemePreference = "auto" | "light" | "sepia" | "dark"
export type ReaderFontFamily = "sans" | "serif" | "custom"
export type ReaderLineWidth = "narrow" | "standard" | "wide"
export type ReaderLineSpacing = "compact" | "standard" | "relaxed"
export type ReaderDefaultTab = "transcript" | "summary"

export type ReadingPreferences = {
  theme: ReaderThemePreference
  fontFamily: ReaderFontFamily
  customFont: string
  fontSize: number
  lineWidth: ReaderLineWidth
  lineSpacing: ReaderLineSpacing
  defaultTab: ReaderDefaultTab
  showCover: boolean
  showHighlights: boolean
}

export const readingPreferencesStorageKey = "linkread-reading-preferences-host-v1"
export const minReaderFontSize = 14
export const maxReaderFontSize = 26

const forbiddenFontChars = /[\u0000-\u001f\u007f-\u009f<>{};:"'\\/()*]/
const utf8Bytes = (value: string) => new TextEncoder().encode(value).byteLength

export const recommendedSystemFonts: ReadonlyArray<string> = [
  "PingFang SC",
  "Hiragino Sans GB",
  "Songti SC",
  "Kaiti SC",
  "STSong",
  "LXGW WenKai",
  "Source Han Sans SC",
  "Source Han Serif SC",
  "New York",
  "Charter",
  "Iowan Old Style",
  "Georgia",
  "Baskerville",
  "Palatino",
  "Avenir Next",
  "Helvetica Neue",
]

export function defaultReadingPreferences(): ReadingPreferences {
  return {
    theme: "auto",
    fontFamily: "sans",
    customFont: "",
    fontSize: 16,
    lineWidth: "standard",
    lineSpacing: "relaxed",
    defaultTab: "transcript",
    showCover: true,
    showHighlights: true,
  }
}

export function isValidCustomFont(name: unknown): name is string {
  if (typeof name !== "string") return false
  const trimmed = name.trim()
  if (!trimmed || utf8Bytes(trimmed) > 128) return false
  return !forbiddenFontChars.test(trimmed)
}

export function normalizeReadingPreferences(raw: unknown): ReadingPreferences {
  const result = defaultReadingPreferences()
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return result
  const obj = raw as Record<string, unknown>
  if (obj.theme === "auto" || obj.theme === "light" || obj.theme === "sepia" || obj.theme === "dark") {
    result.theme = obj.theme
  }
  if (typeof obj.customFont === "string") {
    const trimmed = obj.customFont.trim()
    if (trimmed && isValidCustomFont(trimmed)) result.customFont = trimmed
  }
  if (obj.fontFamily === "sans" || obj.fontFamily === "serif" || obj.fontFamily === "custom") {
    result.fontFamily = obj.fontFamily
  }
  if (result.fontFamily === "custom" && !result.customFont) {
    result.fontFamily = "sans"
  }
  if (typeof obj.fontSize === "number" && Number.isInteger(obj.fontSize)
    && obj.fontSize >= minReaderFontSize && obj.fontSize <= maxReaderFontSize) {
    result.fontSize = obj.fontSize
  }
  if (obj.lineWidth === "narrow" || obj.lineWidth === "standard" || obj.lineWidth === "wide") {
    result.lineWidth = obj.lineWidth
  }
  if (obj.lineSpacing === "compact" || obj.lineSpacing === "standard" || obj.lineSpacing === "relaxed") {
    result.lineSpacing = obj.lineSpacing
  }
  if (obj.defaultTab === "transcript" || obj.defaultTab === "summary") {
    result.defaultTab = obj.defaultTab
  }
  if (typeof obj.showCover === "boolean") result.showCover = obj.showCover
  if (typeof obj.showHighlights === "boolean") result.showHighlights = obj.showHighlights
  return result
}

export function loadReadingPreferences(storage?: Pick<Storage, "getItem">): ReadingPreferences {
  try {
    const raw = (storage ?? localStorage).getItem(readingPreferencesStorageKey)
    return raw ? normalizeReadingPreferences(JSON.parse(raw)) : defaultReadingPreferences()
  } catch {
    return defaultReadingPreferences()
  }
}

export function saveReadingPreferencesLocal(value: ReadingPreferences, storage?: Pick<Storage, "setItem">): void {
  ;(storage ?? localStorage).setItem(readingPreferencesStorageKey, JSON.stringify(normalizeReadingPreferences(value)))
}

export function readingPreferencesEqual(a: ReadingPreferences, b: ReadingPreferences): boolean {
  return JSON.stringify(normalizeReadingPreferences(a)) === JSON.stringify(normalizeReadingPreferences(b))
}

export function resolveEffectiveReaderTheme(
  preference: ReaderThemePreference,
  appResolvedTheme: ResolvedTheme,
): "light" | "sepia" | "dark" {
  return preference === "auto" ? appResolvedTheme : preference
}
