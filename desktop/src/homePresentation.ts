import type { Article } from "./api"

const platformNames = {
  youtube: "YouTube", bilibili: "B 站", apple_podcasts: "Apple 播客", local: "本地字幕",
} as const

/** Keep completion order and reserve a card for the current resume article when needed. */
export function selectRecentArticles(
  finished: readonly Article[], library: readonly Article[] = [], resumeId?: string, limit = 6,
): Article[] {
  if (!Number.isSafeInteger(limit) || limit <= 0) return []
  const current = new Map(library.map(article => [article.id, article]))
  const seen = new Set<string>()
  const selected: Article[] = []
  for (const article of [...finished, ...library]) {
    if (seen.has(article.id)) continue
    seen.add(article.id)
    selected.push(current.get(article.id) ?? article)
    if (selected.length === limit) break
  }
  const resume = resumeId === undefined ? undefined : current.get(resumeId)
  if (resume && !seen.has(resume.id)) selected.splice(limit - 1, 1, resume)
  return selected
}

type SourceIdentity = Partial<Pick<Article, "platform" | "author" | "durationMs">>

export function sourcePresentation({ platform, author, durationMs }: SourceIdentity): {
  name: string; platformLabel: string; duration: string
} {
  const platformLabel = platform ? platformNames[platform] ?? "" : ""
  const name = typeof author === "string" && author.trim() ? author.trim() : platformLabel
  let duration = ""
  if (typeof durationMs === "number" && Number.isFinite(durationMs) && durationMs > 0) {
    const medium = platform === "apple_podcasts" ? "音频"
      : platform === "youtube" || platform === "bilibili" ? "视频" : "原时长"
    // A sub-minute source stays in seconds; a full minute never becomes “60 秒”.
    const time = durationMs < 60_000
      ? `${Math.max(1, Math.floor(durationMs / 1000))} 秒`
      : `${Math.round(durationMs / 60_000)} 分钟`
    duration = `${medium}约 ${time}`
  }
  return { name, platformLabel, duration }
}

export function updatedDatePresentation(updatedAt: number, now = new Date()): {
  label: string; title: string; dateTime: string
} | null {
  if (!Number.isFinite(updatedAt) || updatedAt <= 0) return null
  const date = new Date(updatedAt)
  if (!Number.isFinite(date.getTime())) return null

  const year = date.getFullYear(), month = date.getMonth() + 1, day = date.getDate()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday = new Date(today)
  yesterday.setDate(yesterday.getDate() - 1)
  const sameDay = (other: Date) => year === other.getFullYear()
    && month === other.getMonth() + 1 && day === other.getDate()
  const label = sameDay(today) ? "今天更新" : sameDay(yesterday) ? "昨天更新"
    : `${year === now.getFullYear() ? "" : `${year} 年 `}${month} 月 ${day} 日更新`
  const pad = (value: number) => String(value).padStart(2, "0")
  const title = `更新于 ${year} 年 ${month} 月 ${day} 日 ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  return { label, title, dateTime: date.toISOString() }
}
