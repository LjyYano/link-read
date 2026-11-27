/** Explicit, opt-in YouTube cookie configuration (disabled by default). */

export type YoutubeCookieMode = "none" | "browser" | "file"
export type YoutubeBrowser =
  | "chrome" | "safari" | "edge" | "firefox" | "brave"
  | "chromium" | "opera" | "vivaldi" | "whale"

export type YoutubeCookieOptions = {
  mode: YoutubeCookieMode
  browser: YoutubeBrowser
  filePath: string
}

export const youtubeBrowsers: ReadonlyArray<{ id: YoutubeBrowser; label: string }> = [
  { id: "chrome", label: "Google Chrome" },
  { id: "safari", label: "Safari" },
  { id: "edge", label: "Microsoft Edge" },
  { id: "firefox", label: "Firefox" },
  { id: "brave", label: "Brave" },
  { id: "chromium", label: "Chromium" },
  { id: "opera", label: "Opera" },
  { id: "vivaldi", label: "Vivaldi" },
  { id: "whale", label: "Whale" },
]

const browserSet = new Set<string>(youtubeBrowsers.map(item => item.id))

export const defaultYoutubeCookies: YoutubeCookieOptions = {
  mode: "none",
  browser: "chrome",
  filePath: "",
}

export function loadYoutubeCookies(value: unknown): YoutubeCookieOptions {
  if (!value || typeof value !== "object") return { ...defaultYoutubeCookies }
  const saved = value as Record<string, unknown>
  const mode: YoutubeCookieMode = saved.mode === "browser" || saved.mode === "file" ? saved.mode : "none"
  const rawBrowser = typeof saved.browser === "string" ? saved.browser.trim().toLowerCase() : ""
  const browser: YoutubeBrowser = browserSet.has(rawBrowser) ? rawBrowser as YoutubeBrowser : "chrome"
  const filePath = typeof saved.filePath === "string" ? saved.filePath : ""
  return { mode, browser, filePath }
}

export function isSupportedYoutubeBrowser(value: string): value is YoutubeBrowser {
  return browserSet.has(value.trim().toLowerCase())
}
