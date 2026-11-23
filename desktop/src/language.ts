import { english } from "./languageEnglish.ts"
import { chinese } from "./languageChinese.ts"

export type Locale = "zh-CN" | "en"
export type InterfaceLanguage = Locale | "system"
export type PageLanguage = Locale | "original"
export type LanguageSettings = { interfaceLanguage: InterfaceLanguage; pageLanguage: PageLanguage }

export const languageKey = "linkread-language-settings"
let currentLocale: Locale = "zh-CN"

export function normalizeLanguageSettings(value: unknown): LanguageSettings {
  const saved = value as Partial<LanguageSettings> | null
  const ui = saved?.interfaceLanguage, page = saved?.pageLanguage
  return {
    interfaceLanguage: ui === "en" || ui === "zh-CN" ? ui : "system",
    pageLanguage: page === "en" || page === "zh-CN" ? page : "original",
  }
}

export function loadLanguageSettings(): LanguageSettings {
  try { return normalizeLanguageSettings(JSON.parse(localStorage.getItem(languageKey) ?? "null")) }
  catch { return normalizeLanguageSettings(null) }
}

export function resolveLocale(setting: InterfaceLanguage, primary = globalThis.navigator?.language ?? ""): Locale {
  return setting === "system" ? /^zh(?:[-_]|$)/i.test(primary) ? "zh-CN" : "en" : setting
}

export function setLocale(locale: Locale) {
  currentLocale = locale
  if (typeof document !== "undefined" && document.documentElement.lang !== locale) document.documentElement.lang = locale
}

export const locale = () => currentLocale

export function tr(message: string, ...values: (string | number | null | undefined)[]): string {
  const dictionary = currentLocale === "en" ? english : chinese
  const template = Object.hasOwn(dictionary, message) ? dictionary[message] : message
  return values.length ? template.replace(/\{(\d+)\}/g, (match, index) => String(values[Number(index)] ?? match)) : template
}

export function localizedDetail(message: string, fallback: string): string {
  const localized = tr(message)
  if (currentLocale === "en" && localized === message && /\p{Script=Han}/u.test(message)) return fallback
  return localized
}
