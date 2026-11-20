import type { Article, ArticleState } from "./api"

export type ReadFilter = "all" | "unread" | "completed"
export type ShelfFilters = { read: ReadFilter; starred: boolean }
export const defaultShelfFilters: ShelfFilters = { read: "all", starred: false }

const platformTerms = {
  youtube: "YouTube", bilibili: "Bilibili B 站 哔哩哔哩",
  apple_podcasts: "Apple Podcasts Apple 播客", local: "Local 本地字幕",
}

export function articleState(article: Pick<Article, "isRead" | "isStarred">): ArticleState {
  return { isRead: article.isRead === true, isStarred: article.isStarred === true }
}

export function searchArticles(articles: Article[], search: string): Article[] {
  const query = search.trim().toLocaleLowerCase()
  if (!query) return articles
  return articles.filter(article =>
    [article.title, article.category, platformTerms[article.platform], article.author]
      .some(text => text?.toLocaleLowerCase().includes(query)),
  )
}

export function filterArticles(articles: Article[], filters: ShelfFilters): Article[] {
  return articles.filter(article => {
    const state = articleState(article)
    return (!filters.starred || state.isStarred)
      && (filters.read === "all" || (filters.read === "completed" ? state.isRead : !state.isRead))
  })
}

export function shelfCounts(articles: Article[], starredOnly: boolean) {
  const starred = articles.filter(article => articleState(article).isStarred)
  const base = starredOnly ? starred : articles
  const completed = base.filter(article => articleState(article).isRead).length
  return { all: base.length, completed, unread: base.length - completed, starred: starred.length }
}

/** Keep newer metadata and creation dates while replacing only the saved flags. */
export function withArticleState<T extends Article>(article: T, id: string, state: ArticleState): T {
  return article.id === id ? { ...article, ...state } : article
}

/** A list request started before a saved toggle must not erase that toggle. */
export class ArticleStateChanges {
  version = 0
  private changes = new Map<string, { version: number; state: ArticleState }>()

  record(id: string, state: ArticleState) {
    this.changes.set(id, { version: ++this.version, state })
  }

  reconcile(articles: Article[], startedAt: number): Article[] {
    return articles.map(article => {
      const change = this.changes.get(article.id)
      return change && change.version > startedAt ? { ...article, ...change.state } : article
    })
  }
}

/** A new view can submit while the previous view's write is still in flight. */
export class ArticleStateWrites {
  private pending = new Map<string, Promise<void>>()

  run(id: string, write: () => Promise<void>): Promise<void> {
    const previous = this.pending.get(id) ?? Promise.resolve()
    const next = previous.catch(() => {}).then(write)
    this.pending.set(id, next)
    return next.finally(() => {
      if (this.pending.get(id) === next) this.pending.delete(id)
    })
  }
}
