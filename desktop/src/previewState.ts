import { normalizeVideoLink } from "./videoLinks.ts"
/** Only complete, supported video or episode URLs start background preview work. */
export function previewKey(source: string): string | undefined {
  try {
    const url = new URL(normalizeVideoLink(source))
    if (url.protocol !== "https:" || url.username || url.password || url.port) return
    if (["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"].includes(url.hostname)) {
      const path = url.pathname.split("/").filter(Boolean)
      const id = url.hostname.endsWith("youtu.be") ? path[0]
        : url.pathname === "/watch" ? url.searchParams.get("v")
        : ["shorts", "live", "embed"].includes(path[0]) ? path[1] : undefined
      if (id && /^[\w-]{11}$/.test(id)) return "youtube:" + id
    }
    if (["bilibili.com", "www.bilibili.com", "m.bilibili.com"].includes(url.hostname)) {
      const id = url.pathname.match(/\/video\/(BV[\da-zA-Z]{10})(?:\/|$)/)?.[1]
      const part = url.searchParams.get("p") || "1"
      if (id && /^\d+$/.test(part) && Number(part) > 0) return `bilibili:${id}:${Number(part)}`
    }
    if (["b23.tv", "www.b23.tv"].includes(url.hostname) && /^\/[\w-]+\/?$/.test(url.pathname)) return url.href
    if (url.hostname === "podcasts.apple.com") {
      // A storefront can expose different metadata; keep it in the cache key.
      const [, country, , show] = url.pathname.split("/")
      return `apple_podcasts:${country}:${show.slice(2)}:${url.searchParams.get("i")}`
    }
  } catch { /* Incomplete input is not an error and must not trigger requests. */ }
}

export class PreviewRevision {
  private version = 0
  next(): number { return ++this.version }
  accepts(value: number): boolean { return value === this.version }
}
