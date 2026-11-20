/** Ask the embedded reader to consume Escape before leaving the article. */
let nextRequest = 0

export class ReaderEscapeBridge {
  private pending?: { id: string; onUnhandled: () => void; timer: ReturnType<typeof setTimeout> }
  private send: (message: unknown) => void
  private timeoutMs: number

  constructor(send: (message: unknown) => void, timeoutMs = 1500) {
    this.send = send
    this.timeoutMs = timeoutMs
  }

  request(onUnhandled: () => void) {
    // Holding Escape or pressing it while the frame is busy must not queue exits.
    if (this.pending) return
    const id = String(++nextRequest)
    const timer = setTimeout(() => {
      if (this.pending?.id === id) this.pending = undefined
    }, this.timeoutMs)
    this.pending = { id, onUnhandled, timer }
    try { this.send({ linkReadEscapeRequest: id }) }
    catch { this.cancel() }
  }

  receive(message: unknown) {
    if (!this.pending || !message || typeof message !== "object" || !("linkReadEscapeResult" in message)) return
    const result = message.linkReadEscapeResult
    if (!result || typeof result !== "object" || !("id" in result) || !("handled" in result)
      || result.id !== this.pending?.id || typeof result.handled !== "boolean") return
    const callback = this.pending!.onUnhandled
    this.cancel()
    if (!result.handled) callback()
  }

  cancel() {
    if (this.pending) clearTimeout(this.pending.timer)
    this.pending = undefined
  }
}

export function readerShortcutsBlocked(root: Pick<Document, "querySelector"> = document) {
  return Boolean(root.querySelector('dialog[open], [role="dialog"][aria-modal="true"], [data-reader-shortcuts-blocked]'))
}
