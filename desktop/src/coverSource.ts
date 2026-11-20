import type { ArticleMetadata } from "./api"

/** Public thumbnails only, in sync with Python's thumbnail_url allowlist. */
export function thumbnailSource(value?: string | null): string | undefined {
  if (!value || /[\\\u0000-\u001f]/.test(value)) return
  try {
    const text = value.trim().startsWith("//") ? "https:" + value.trim() : value.trim()
    const url = new URL(text)
    // URL() normalizes :443 away: also reject explicit ports and credentials.
    const authority = text.split("://")[1]?.split(/[/?#]/)[0] || ""
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password
        || authority.includes(":") || authority.includes("@")
        || !(url.hostname === "i.ytimg.com" || url.hostname.endsWith(".hdslb.com")
          || url.hostname.endsWith(".mzstatic.com"))) return
    url.protocol = "https:"
    url.hash = ""
    return url.href
  } catch { /* Malformed external metadata should use the typographic fallback. */ }
}

/** Metadata events are partial: a missing cover must not erase the preview. */
export function mergeArticleMetadata<T extends object>(current: T, incoming?: Partial<ArticleMetadata> | null): T & Partial<ArticleMetadata> {
  return { ...current, ...Object.fromEntries(
    Object.entries(incoming ?? {}).filter(([, value]) => value !== null && value !== undefined && value !== ""),
  ) }
}
