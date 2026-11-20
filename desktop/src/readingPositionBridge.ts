/** The native host owns durable state; every iframe load has its own session. */
type Payload = { version?: unknown; key?: unknown; session?: unknown; state?: unknown; requestId?: unknown }
type Options = {
  key: string
  load: (key: string) => Promise<unknown>
  save: (key: string, state: unknown) => Promise<void>
  send: (message: unknown) => void
  onError: (error: unknown) => void
}
const writes = new Map<string, Promise<void>>()
const activeBridges = new Set<ReadingPositionBridge>()

/** App-level navigation (shortcuts and completion notices) must save before unmounting the reader. */
export async function flushReadingPositions(): Promise<void> {
  await Promise.all([...activeBridges].map(bridge => bridge.flush()))
}

export class ReadingPositionBridge {
  private session = ""
  private alive = true
  private sequence = 0
  private restoreGate: Promise<void> = Promise.resolve()
  private flushes = new Map<string, () => void>()
  private options: Options
  constructor(options: Options) { this.options = options; activeBridges.add(this) }

  private matches(value: Payload): boolean {
    return this.alive && value.version === 1 && value.key === this.options.key && value.session === this.session
  }

  receive(message: Record<string, unknown>): void {
    const ready = message.linkReadPositionReady as Payload | undefined
    if (ready && ready.version === 1 && ready.key === this.options.key && typeof ready.session === "string"
      && /^[A-Za-z0-9_-]{8,160}$/.test(ready.session) && ready.session !== this.session) {
      this.session = ready.session
      const session = this.session
      this.restoreGate = (writes.get(this.options.key) ?? Promise.resolve()).then(() => this.options.load(this.options.key)).then(state => {
        if (this.alive && this.session === session) this.options.send({
          linkReadPositionRestore: { version: 1, key: this.options.key, session, state },
        })
      }).catch(error => {
        if (this.alive && this.session === session) {
          this.options.onError(error)
          this.options.send({ linkReadPositionRestore: { version: 1, key: this.options.key, session, state: null } })
        }
      })
    }
    const changed = message.linkReadPositionChanged as Payload | undefined
    if (!changed || !this.session || !this.matches(changed) || !changed.state || typeof changed.state !== "object") return
    // The native validator checks the full anchor/map schema and size again.
    try { if (JSON.stringify(changed.state).length > 256 * 1024) return } catch { return }
    const key = this.options.key
    const pending = (writes.get(key) ?? Promise.resolve()).then(() => this.options.save(key, changed.state))
      .catch(error => this.options.onError(error))
    writes.set(key, pending)
    void pending.then(() => {
      if (writes.get(key) === pending) writes.delete(key)
      if (typeof changed.requestId === "string") {
        this.flushes.get(changed.requestId)?.()
        this.flushes.delete(changed.requestId)
      }
    })
  }

  async flush(): Promise<void> {
    if (!this.alive || !this.session) { await writes.get(this.options.key); return }
    const session = this.session
    // A fresh document must see its saved native record before it captures a
    // default top position. Messages from this host retain Restore → Flush order.
    const restored = await new Promise<boolean>(resolve => {
      const timer = setTimeout(() => resolve(false), 700)
      void this.restoreGate.then(() => { clearTimeout(timer); resolve(true) })
    })
    if (!restored || !this.alive || this.session !== session) { await writes.get(this.options.key); return }
    const requestId = `flush-${++this.sequence}`
    await new Promise<void>(resolve => {
      const timer = setTimeout(() => { this.flushes.delete(requestId); resolve() }, 700)
      this.flushes.set(requestId, () => { clearTimeout(timer); resolve() })
      this.options.send({ linkReadPositionFlush: { version: 1, key: this.options.key, session: this.session, requestId } })
    })
    await writes.get(this.options.key)
  }

  close(): void {
    this.alive = false
    activeBridges.delete(this)
    for (const resolve of this.flushes.values()) resolve()
    this.flushes.clear()
  }
}
