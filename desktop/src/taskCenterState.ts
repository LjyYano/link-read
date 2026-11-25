import type { TaskJob } from "./Home"
import type { Article } from "./api"

const reviewedKey = "linkread-reviewed-tasks-v1"
const reviewedLimit = 100
const completedKey = "linkread-recently-completed-v1"
const completedLimit = 8

function validId(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= 255
    && !/[\\/\u0000-\u001f]/.test(value)
}

/** Only UI acknowledgement is stored here. Execution and recovery remain in the task journal. */
export function loadReviewedTasks(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(reviewedKey) || "[]")
    return Array.isArray(value) ? [...new Set(value.filter(validId))].slice(0, reviewedLimit) : []
  } catch { return [] }
}

export function saveReviewedTasks(ids: readonly string[]): string[] {
  const next = [...new Set(ids.filter(validId))].slice(0, reviewedLimit)
  try { localStorage.setItem(reviewedKey, JSON.stringify(next)) } catch {
    // Optional acknowledgement storage never prevents reading or task recovery.
  }
  return next
}

export function loadCompletedArticleIds(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(completedKey) || "[]")
    return Array.isArray(value) ? [...new Set(value.filter(validId))].slice(0, completedLimit) : []
  } catch { return [] }
}

export function rememberCompletedArticle(ids: readonly string[], id: string): string[] {
  const next = [...new Set([id, ...ids].filter(validId))].slice(0, completedLimit)
  try { localStorage.setItem(completedKey, JSON.stringify(next)) } catch { /* History is optional. */ }
  return next
}

export function completedArticles(articles: readonly Article[], ids: readonly string[]): Article[] {
  const archived = new Map(articles.map(article => [article.id, article]))
  return ids.flatMap(id => {
    const article = archived.get(id)
    return article ? [article] : []
  })
}

export function groupTasks(jobs: readonly TaskJob[]) {
  return {
    active: jobs.filter(task => task.job.status === "running" || task.job.status === "cancelling"),
    queued: jobs.filter(task => task.job.status === "queued"),
    attention: jobs.filter(isTerminalTask).reverse(),
  }
}

export function activeTaskCount(jobs: readonly TaskJob[]): number {
  return jobs.filter(task => ["running", "cancelling", "queued"].includes(task.job.status)).length
}

export function isTerminalTask(task: TaskJob): boolean {
  return ["error", "cancelled", "interrupted"].includes(task.job.status)
}

export function unreviewedTaskIds(jobs: readonly TaskJob[], reviewedIds: readonly string[]): string[] {
  const reviewed = new Set(reviewedIds)
  // A cancellation requested by the user isn't a new failure notification.
  return jobs.filter(task => (task.job.status === "error" || task.job.status === "interrupted")
    && !reviewed.has(task.id)).map(task => task.id)
}

export function reviewedTerminalTaskIds(jobs: readonly TaskJob[], reviewedIds: readonly string[]): string[] {
  const reviewed = new Set(reviewedIds)
  return jobs.filter(task => isTerminalTask(task) && reviewed.has(task.id)).map(task => task.id)
}

export function reviewTasks(reviewedIds: readonly string[], ids: readonly string[], jobs?: readonly TaskJob[]): string[] {
  const live = jobs ? new Set(jobs.filter(isTerminalTask).map(task => task.id)) : undefined
  return [...new Set([...ids, ...reviewedIds].filter(id => validId(id) && (!live || live.has(id))))]
    .slice(0, reviewedLimit)
}
