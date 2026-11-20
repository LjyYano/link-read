/** Metadata requests outlive a single input subscription, but are shared by key.
 * A cleared composer must not discard a useful response or start another request
 * when the same episode is already loading in a visible article card.
 */
export class PreviewRequests<T> {
  private cache = new Map<string, { at: number; data: T }>()
  private pending = new Map<string, Promise<T>>()
  private ttl: number
  private capacity: number
  private now: () => number
  constructor(ttl = 10 * 60_000, capacity = 32, now = Date.now) {
    this.ttl = ttl
    this.capacity = capacity
    this.now = now
  }

  peek(key: string): T | undefined {
    const saved = this.cache.get(key)
    if (!saved) return
    if (this.now() - saved.at >= this.ttl) { this.cache.delete(key); return }
    return saved.data
  }

  request(key: string, load: () => Promise<T>): Promise<T> {
    const saved = this.peek(key)
    if (saved !== undefined) return Promise.resolve(saved)
    const pending = this.pending.get(key)
    if (pending) return pending
    // Resolve through a microtask so a synchronous loader error also clears the entry.
    const request = Promise.resolve().then(load).then(data => {
      this.cache.delete(key)
      this.cache.set(key, { at: this.now(), data })
      while (this.cache.size > this.capacity) this.cache.delete(this.cache.keys().next().value!)
      return data
    }).finally(() => { if (this.pending.get(key) === request) this.pending.delete(key) })
    this.pending.set(key, request)
    return request
  }
}
