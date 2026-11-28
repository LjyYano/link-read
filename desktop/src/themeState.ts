export type ThemeMode = "system" | "light" | "dark"
export type ResolvedTheme = "light" | "dark"

export const themeStorageKey = "linkread-theme"

export function loadThemeMode(storage?: Pick<Storage, "getItem">): ThemeMode {
  try {
    const raw = (storage ?? localStorage).getItem(themeStorageKey)
    return raw === "light" || raw === "dark" || raw === "system" ? raw : "system"
  } catch {
    return "system"
  }
}

export function saveThemeMode(mode: ThemeMode, storage?: Pick<Storage, "setItem">): void {
  ;(storage ?? localStorage).setItem(themeStorageKey, mode)
}

export function resolveTheme(mode: ThemeMode, systemDark: boolean): ResolvedTheme {
  if (mode === "light" || mode === "dark") return mode
  return systemDark ? "dark" : "light"
}

export function nextThemeMode(mode: ThemeMode): ThemeMode {
  if (mode === "system") return "light"
  if (mode === "light") return "dark"
  return "system"
}
