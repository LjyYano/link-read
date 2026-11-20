import { useCallback, useEffect, useRef, useState } from "react"
import { previewVideo, type ArticleMetadata } from "./api"
import { previewKey, PreviewRevision } from "./previewState"
import { PreviewRequests } from "./previewRequests"

type PreviewState =
  | { status: "idle"; source: string }
  | { status: "loading"; source: string; slow?: boolean }
  | { status: "error"; source: string; timedOut?: boolean }
  | { status: "ready"; source: string; metadata: ArticleMetadata }
export type LinkPreview = PreviewState & { retry: () => void }

const requests = new PreviewRequests<ArticleMetadata>()
export function useLinkPreview(input: string, localFile: boolean): LinkPreview {
  const source = input.trim()
  const [state, setState] = useState<PreviewState>({ status: "idle", source: "" })
  const [attempt, setAttempt] = useState(0)
  const retry = useCallback(() => setAttempt(value => value + 1), [])
  const revision = useRef(new PreviewRevision())
  useEffect(() => {
    const request = revision.current.next()
    const key = localFile ? undefined : previewKey(source)
    if (!key) { setState({ status: "idle", source }); return }
    const saved = requests.peek(key)
    if (saved) {
      setState({ status: "ready", source, metadata: saved }); return
    }
    setState({ status: "loading", source })
    let settled = false
    let slowTimer: number | undefined
    let deadline: number | undefined
    const timer = window.setTimeout(() => {
      slowTimer = window.setTimeout(() => {
        if (!settled && revision.current.accepts(request)) setState({ status: "loading", source, slow: true })
      }, 4000)
      deadline = window.setTimeout(() => {
        if (!settled && revision.current.accepts(request)) {
          settled = true
          setState({ status: "error", source, timedOut: true })
        }
      }, 12000)
      // Cache population is independent of this hook's revision. Retrying while
      // the native call still runs reuses it instead of spawning another process.
      requests.request(key, () => previewVideo(source)).then(data => {
        if (settled || !revision.current.accepts(request)) return
        settled = true
        window.clearTimeout(slowTimer)
        window.clearTimeout(deadline)
        setState({ status: "ready", source, metadata: data })
      }, () => {
        if (settled || !revision.current.accepts(request)) return
        settled = true
        window.clearTimeout(slowTimer)
        window.clearTimeout(deadline)
        setState({ status: "error", source })
      })
    }, 200)
    return () => {
      window.clearTimeout(timer)
      window.clearTimeout(slowTimer)
      window.clearTimeout(deadline)
      revision.current.next()
    }
  }, [source, localFile, attempt])
  // Never show a thumbnail for the preceding draft during React effect scheduling.
  return { ...(state.source === source ? state : { status: "idle" as const, source }), retry }
}
