import { useEffect, useState } from "react"
import { loadReadingPosition, type Article } from "./api"
import { findContinueReading, type ContinueReading } from "./continueReading"

export function useContinueReading(articles: Article[] | undefined, recent: string[], enabled: boolean) {
  const [resume, setResume] = useState<ContinueReading>()
  useEffect(() => {
    if (!enabled || !articles) return
    let current = true
    void findContinueReading(articles, recent, loadReadingPosition, () => current).then(value => {
      if (current) setResume(value)
    })
    return () => { current = false }
  }, [articles, recent, enabled])
  // Immediately hide a removed, replaced or completed item while a new lookup
  // runs. Background results cannot resurrect it after the library changes.
  const article = resume && articles?.find(item => item.id === resume.article.id
    && !item.isRead && !item.previewId && item.readingKey === resume.article.readingKey)
  return article && resume ? { ...resume, article } : undefined
}
