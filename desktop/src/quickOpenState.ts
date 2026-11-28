import type { Article } from "./api"
import { searchArticles } from "./libraryState.ts"

const recentKey = "linkread-recently-opened-v1"
const limit = 30

export function loadRecentArticles(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(recentKey) || "[]")
    if (!Array.isArray(value)) return []
    return [...new Set(value.filter((id): id is string => typeof id === "string"
      && id.length > 0 && id.length <= 255 && !id.startsWith(".") && !/[\\/\u0000]/.test(id)))].slice(0, limit)
  } catch { return [] }
}

export function rememberArticle(recent: string[], id: string): string[] {
  const next = [id, ...recent.filter(item => item !== id)].slice(0, limit)
  try { localStorage.setItem(recentKey, JSON.stringify(next)) } catch { /* Opening remains available without optional history. */ }
  return next
}

export function quickOpenArticles(articles: Article[], query: string, recent: string[]): Article[] {
  const matching = searchArticles(articles, query)
  if (query.trim()) return matching
  const order = new Map(recent.map((id, index) => [id, index]))
  // Filter through the actual library first: deleted/replaced history never
  // becomes an invented article or an arbitrary path passed to the native layer.
  return [...matching].sort((a, b) => (order.get(a.id) ?? Infinity) - (order.get(b.id) ?? Infinity))
}

export type HighlightPart = { text: string; match: boolean }

export function highlightQueryParts(text: string, query: string): HighlightPart[] {
  const tokens = [...new Set(query.trim().toLowerCase().split(/\s+/).filter(Boolean))]
    .sort((a, b) => b.length - a.length)
  if (!text || !tokens.length) return [{ text, match: false }]
  const lower = text.toLowerCase()
  const ranges: Array<[number, number]> = []
  for (const token of tokens) {
    let from = 0
    while (from < lower.length) {
      const index = lower.indexOf(token, from)
      if (index < 0) break
      ranges.push([index, index + token.length])
      from = index + token.length
    }
  }
  if (!ranges.length) return [{ text, match: false }]
  ranges.sort((a, b) => a[0] - b[0] || b[1] - a[1])
  const merged: Array<[number, number]> = []
  for (const [start, end] of ranges) {
    const previous = merged.at(-1)
    if (previous && start <= previous[1]) previous[1] = Math.max(previous[1], end)
    else merged.push([start, end])
  }
  const parts: HighlightPart[] = []
  let cursor = 0
  for (const [start, end] of merged) {
    if (start > cursor) parts.push({ text: text.slice(cursor, start), match: false })
    parts.push({ text: text.slice(start, end), match: true })
    cursor = end
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor), match: false })
  return parts
}
