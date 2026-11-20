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
