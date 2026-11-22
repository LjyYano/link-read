import type { Article } from "./api"

const readingTabs = ["summary", "mindmap", "transcript", "keywords", "highlights", "shownotes"] as const
type ReadingTab = typeof readingTabs[number]
export type ContinueReading = { article: Article; tab: ReadingTab; updatedAt: number }

/** Membership and manual completion come from the live library, never history. */
export function continueCandidates(articles: Article[], recent: string[]): Article[] {
  const current = new Map(articles.filter(article => !article.isRead && !article.previewId
    && typeof article.readingKey === "string" && article.readingKey.length > 0).map(article => [article.id, article]))
  return [...new Set(recent)].slice(0, 30).flatMap(id => current.has(id) ? [current.get(id)!] : [])
}

export function readingSummary(value: unknown): { tab: ReadingTab; updatedAt: number } | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  const saved = value as Record<string, unknown>
  if (saved.version !== 1 || !Number.isSafeInteger(saved.updatedAt) || (saved.updatedAt as number) <= 0
      || (saved.updatedAt as number) > 8_640_000_000_000_000
      || !readingTabs.some(tab => tab === saved.activeTab)
      || !saved.tabs || typeof saved.tabs !== "object" || Array.isArray(saved.tabs)) return null
  const tab = saved.activeTab as ReadingTab
  const position = (saved.tabs as Record<string, unknown>)[tab]
  if (!position || typeof position !== "object") return null
  const { signature, anchor } = position as Record<string, unknown>
  if (typeof signature !== "string" || !/^p1-[0-9a-f]{16}-[0-9]+$/.test(signature)
      || !anchor || typeof anchor !== "object" || !("kind" in anchor)
      || !["top", "cue", "block"].includes(String(anchor.kind))) return null
  // The reader restores its verified anchors and map state separately on open.
  return { tab, updatedAt: saved.updatedAt as number }
}

export async function findContinueReading(articles: Article[], recent: string[],
  load: (key: string) => Promise<unknown>, isCurrent = () => true): Promise<ContinueReading | undefined> {
  for (const article of continueCandidates(articles, recent)) {
    if (!isCurrent()) return undefined
    try {
      const summary = readingSummary(await load(article.readingKey!))
      if (!isCurrent()) return undefined
      if (summary) return { article, ...summary }
    } catch { /* One unreadable record must not prevent another article from resuming. */ }
  }
  return undefined
}
