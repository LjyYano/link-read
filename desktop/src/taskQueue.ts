import { tr } from "./language.ts"
/** FIFO controller for desktop generation. Only one sidecar request may be active. */
export type QueueTask<T> = {
  id: string
  /** Durable admission/preflight must finish before any model or media work starts. */
  beforeStart?: () => Promise<void>
  run: () => Promise<T>
  onStart: () => void
  onSuccess: (result: T) => void
  onFailure: (error: unknown) => void
  onFinish?: () => void
}

export class SerialTaskQueue<T> {
  private waiting: QueueTask<T>[] = []
  private active: QueueTask<T> | null = null
  private started = false
  private cancelledBeforeStart = false

  get activeId(): string | null { return this.active?.id ?? null }
  get waitingIds(): string[] { return this.waiting.map(item => item.id) }

  enqueue(task: QueueTask<T>): void {
    if (this.active?.id === task.id || this.waiting.some(item => item.id === task.id)) {
      throw new Error(tr("重复的任务标识"))
    }
    this.waiting.push(task)
    this.advance()
  }

  /** Remove a queued task without touching the currently running sidecar. */
  cancelWaiting(id: string): boolean {
    if (this.active?.id === id && !this.started && !this.cancelledBeforeStart) {
      this.cancelledBeforeStart = true
      return true
    }
    const index = this.waiting.findIndex(item => item.id === id)
    if (index === -1) return false
    this.waiting.splice(index, 1)
    return true
  }

  private advance(): void {
    if (this.active || !this.waiting.length) return
    const next = this.waiting.shift()!
    this.active = next
    this.started = false
    this.cancelledBeforeStart = false
    void this.execute(next)
  }

  private async execute(task: QueueTask<T>): Promise<void> {
    try {
      if (task.beforeStart) await task.beforeStart()
      if (this.cancelledBeforeStart) return
      this.started = true
      task.onStart()
      task.onSuccess(await task.run())
    } catch (error) {
      if (!this.cancelledBeforeStart) task.onFailure(error)
    } finally {
      try {
        task.onFinish?.()
      } finally {
        this.active = null
        this.advance()
      }
    }
  }
}
