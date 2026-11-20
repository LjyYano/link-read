import type { ArticleMetadata, GenerationFailure, Provider } from "./api"
import type { AsrOptions } from "./asrSettings"
import type { Lane, TaskActivity } from "./taskProgress"

export const taskJournalLimit = 50
export const taskJournalMaxBytes = 1024 * 1024
export type StoredTaskStatus = "queued" | "running" | "cancelling" | "error" | "cancelled" | "interrupted"
export type TaskSettings = {
  provider: Provider
  executable: string
  baseUrl: string
  model: string
  asr: AsrOptions
}
export type GenerationInput = TaskSettings & {
  input: string
  localFile: boolean
  sourceKey: string | null
  replace: boolean
  identity: Partial<ArticleMetadata>
  previewId?: string
}
export type StoredProgress = { step: number; percent: number; message: string; activity?: TaskActivity }
export type StoredTask = {
  id: string
  createdAt: number
  updatedAt: number
  status: StoredTaskStatus
  input: { source: string; localFile: boolean; sourceKey: string | null; replaceRequested: boolean }
  settings: TaskSettings
  metadata: Partial<ArticleMetadata>
  progress: StoredProgress
  previewId?: string
  failure?: GenerationFailure
}
export type TaskJournal = { version: 1; tasks: StoredTask[] }
export type JournalJob = Partial<ArticleMetadata> & {
  status: StoredTaskStatus
  message: string
  step?: number
  percent?: number
  activity?: TaskActivity
  previewId?: string
  failure?: GenerationFailure
}
export type RestoredTask = {
  id: string
  createdAt: number
  updatedAt: number
  input: GenerationInput
  job: JournalJob & { status: "interrupted" | "error" | "cancelled" }
}
const lanes: Lane[] = ["extract", "audio", "upload", "asr", "analysis", "polish", "publication", "render"]
const statuses = new Set<StoredTaskStatus>(["queued", "running", "cancelling", "error", "cancelled", "interrupted"])

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("保存的任务格式无效，请重试读取任务记录")
  return value as Record<string, unknown>
}
function text(value: unknown, max: number, label: string, truncate = false): string {
  if (typeof value !== "string" || value.includes("\0") || (!truncate && value.length > max)) {
    throw new Error(`任务记录中的${label}无效或过长`)
  }
  return truncate ? value.slice(0, max) : value
}
function number(value: unknown, min: number, max: number, label: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min || value > max) {
    throw new Error(`任务记录中的${label}无效`)
  }
  return value
}
function boolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") throw new Error(`任务记录中的${label}无效`)
  return value
}
function endpoint(value: unknown): string {
  const result = text(value, 2048, "服务地址")
  if (!result) return result
  try {
    const url = new URL(result)
    const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
    if ((url.protocol !== "https:" && !(url.protocol === "http:" && loopback))
        || url.username || url.password || url.search || url.hash) throw new Error()
  } catch { throw new Error("任务记录中的服务地址无效；不能包含凭据或查询参数") }
  return result
}
function settings(value: unknown): TaskSettings {
  const source = record(value), asr = record(source.asr)
  if (!["codex", "claude", "api"].includes(String(source.provider))) throw new Error("任务记录中的 AI 服务无效")
  const chunkMinutes = number(asr.chunkMinutes, 0, 15, "ASR 分片长度")
  if (![0, 5, 10, 15].includes(chunkMinutes)) throw new Error("任务记录中的 ASR 分片长度无效")
  const ffmpegPath = asr.ffmpegPath === undefined ? "" : text(asr.ffmpegPath, 4096, "FFmpeg 路径")
  if ((ffmpegPath && !ffmpegPath.startsWith("/")) || /[\u0000-\u001f\u007f]/.test(ffmpegPath)) {
    throw new Error("任务记录中的 FFmpeg 路径无效")
  }
  return {
    provider: source.provider as Provider, executable: text(source.executable, 4096, "可执行文件路径"),
    baseUrl: endpoint(source.baseUrl), model: text(source.model, 256, "模型名称"),
    asr: { enabled: boolean(asr.enabled, "ASR 开关"), reuseToken: boolean(asr.reuseToken, "ASR Token 设置"),
      baseUrl: endpoint(asr.baseUrl), model: text(asr.model, 256, "ASR 模型名称"),
      maxMinutes: number(asr.maxMinutes, 1, 720, "ASR 时长限制"), chunkMinutes,
      ffmpegPath },
  }
}
function metadata(value: unknown): Partial<ArticleMetadata> {
  const source = record(value), result: Partial<ArticleMetadata> = {}
  if (source.platform !== undefined) {
    if (!["bilibili", "youtube", "apple_podcasts", "local"].includes(String(source.platform))) throw new Error("任务记录中的来源平台无效")
    result.platform = source.platform as ArticleMetadata["platform"]
  }
  for (const key of ["title", "author", "publishedAt", "sourceUrl", "thumbnailUrl"] as const) {
    const value = source[key]
    if (value === undefined) continue
    if (value === null && key !== "title") result[key] = null
    else result[key] = text(value, key.endsWith("Url") ? 8192 : 2000, "来源信息", true)
  }
  if (source.durationMs !== undefined) result.durationMs = source.durationMs === null ? null
    : number(source.durationMs, 0, Number.MAX_SAFE_INTEGER, "时长")
  return result
}
function activity(value: unknown): TaskActivity {
  const source = record(value), savedLanes = record(source.lanes)
  const result: TaskActivity = {
    startedAt: number(source.startedAt, 0, Number.MAX_SAFE_INTEGER, "开始时间"),
    lastEventAt: number(source.lastEventAt, 0, Number.MAX_SAFE_INTEGER, "进度时间"), lanes: {}, history: [],
  }
  for (const key of lanes) {
    if (savedLanes[key] === undefined) continue
    const lane = record(savedLanes[key])
    if (lane.state !== "running" && lane.state !== "complete") throw new Error("任务记录中的阶段状态无效")
    result.lanes[key] = { label: text(lane.label, 128, "阶段名称", true), message: text(lane.message, 2000, "进度说明", true),
      state: lane.state, startedAt: number(lane.startedAt, 0, Number.MAX_SAFE_INTEGER, "阶段开始时间"),
      updatedAt: number(lane.updatedAt, 0, Number.MAX_SAFE_INTEGER, "阶段更新时间"),
      ...(lane.completed !== undefined ? { completed: number(lane.completed, 0, Number.MAX_SAFE_INTEGER, "已完成批次") } : {}),
      ...(lane.total !== undefined ? { total: number(lane.total, 0, Number.MAX_SAFE_INTEGER, "总批次") } : {}),
    }
  }
  if (!Array.isArray(source.history)) throw new Error("任务记录中的进度历史无效")
  result.history = source.history.slice(-12).map(value => {
    const line = record(value)
    return { at: number(line.at, 0, Number.MAX_SAFE_INTEGER, "进度时间"), stage: text(line.stage, 128, "进度阶段", true), message: text(line.message, 2000, "进度说明", true) }
  })
  return result
}
function failure(value: unknown): GenerationFailure {
  const source = record(value)
  if (!["retry", "settings", "setup", "source", "details"].includes(String(source.action))) throw new Error("任务记录中的重试动作无效")
  return { message: text(source.message, 2000, "错误说明", true), code: text(source.code, 128, "错误类型", true),
    retryable: boolean(source.retryable, "可重试状态"), action: source.action as GenerationFailure["action"],
    ...(source.stage !== undefined ? { stage: source.stage === null ? null : text(source.stage, 128, "失败阶段", true) } : {}),
  }
}
function storedTask(value: unknown): StoredTask {
  const source = record(value), input = record(source.input), progress = record(source.progress)
  const id = text(source.id, 128, "任务标识")
  if (!/^[a-zA-Z0-9_-]+$/.test(id)) throw new Error("任务记录中的任务标识无效")
  if (!statuses.has(source.status as StoredTaskStatus)) throw new Error("任务记录中的状态无效")
  const result: StoredTask = {
    id, createdAt: number(source.createdAt, 0, Number.MAX_SAFE_INTEGER, "创建时间"),
    updatedAt: number(source.updatedAt, 0, Number.MAX_SAFE_INTEGER, "更新时间"), status: source.status as StoredTaskStatus,
    input: { source: text(input.source, 8192, "输入"), localFile: boolean(input.localFile, "文件类型"),
      sourceKey: input.sourceKey === null ? null : text(input.sourceKey, 256, "来源标识"),
      replaceRequested: boolean(input.replaceRequested, "替换意图") },
    settings: settings(source.settings), metadata: metadata(source.metadata),
    progress: { step: number(progress.step, 0, 3, "处理阶段"), percent: number(progress.percent, 0, 99, "进度"),
      message: text(progress.message, 2000, "进度说明", true),
      ...(progress.activity !== undefined ? { activity: activity(progress.activity) } : {}) },
  }
  if (source.previewId !== undefined) {
    const id = text(source.previewId, 128, "预览标识")
    if (!/^[a-f0-9]{64}$/.test(id)) throw new Error("任务记录中的预览标识无效")
    result.previewId = id
  }
  if (source.failure !== undefined) result.failure = failure(source.failure)
  return result
}

/** Copy only the journal schema. Provider credentials and arbitrary IPC fields never enter it. */
export function parseTaskJournal(value: unknown): TaskJournal {
  if (value === null) return { version: 1, tasks: [] }
  const source = record(value)
  if (source.version !== 1 || !Array.isArray(source.tasks)) throw new Error("保存的任务版本无效，请重试读取任务记录")
  if (source.tasks.length > taskJournalLimit) throw new Error(`最多保留 ${taskJournalLimit} 个未完成任务，请先移除不需要的任务`)
  const tasks = source.tasks.map(storedTask)
  if (new Set(tasks.map(task => task.id)).size !== tasks.length) throw new Error("保存的任务标识重复，请重试读取任务记录")
  const journal: TaskJournal = { version: 1, tasks }
  if (new TextEncoder().encode(JSON.stringify(journal)).byteLength > taskJournalMaxBytes) {
    throw new Error("未完成任务记录超过 1 MiB，请先移除不需要的任务")
  }
  return journal
}

export function taskSnapshot(id: string, input: GenerationInput, job: JournalJob, createdAt: number, updatedAt: number): StoredTask {
  return storedTask({ id, createdAt, updatedAt, status: job.status,
    input: { source: input.input, localFile: input.localFile, sourceKey: input.sourceKey, replaceRequested: input.replace },
    settings: input, metadata: { ...input.identity, ...metadata(job) },
    progress: { step: job.step ?? 0, percent: job.percent ?? 0, message: job.message, activity: job.activity },
    previewId: job.previewId ?? input.previewId, failure: job.failure,
  })
}

/** Restoring data never enqueues work. An explicit Continue action must recheck current settings and duplicates. */
export function restoreTaskJournal(value: unknown): RestoredTask[] {
  return parseTaskJournal(value).tasks.map(task => ({
    id: task.id, createdAt: task.createdAt, updatedAt: task.updatedAt,
    input: { input: task.input.source, localFile: task.input.localFile, sourceKey: task.input.sourceKey,
      replace: false, ...task.settings, identity: task.metadata, previewId: task.previewId },
    job: { ...task.metadata, ...task.progress, previewId: task.previewId, failure: task.failure,
      status: task.status === "error" || task.status === "cancelled" ? task.status : "interrupted",
      message: task.status === "error" || task.status === "cancelled" ? task.progress.message
        : "上次处理已中断。点击继续后，将检查已保存的结果和当前设置。",
    },
  }))
}

/** A changed ASR scope is new spending authorization, not a cached-task resume. */
export function resumeNeedsAsrConfirmation(previous: AsrOptions, current: AsrOptions): boolean {
  return current.enabled && (!previous.enabled || current.maxMinutes > previous.maxMinutes
    || current.baseUrl !== previous.baseUrl || current.model !== previous.model || current.chunkMinutes !== previous.chunkMinutes)
}

/** At most one disk write runs. Newer full snapshots coalesce without allowing a delayed old write to resurrect removed tasks. */
export class TaskJournalWriter {
  private revision = 0
  private pending: { revision: number; journal: TaskJournal } | undefined
  private running = false
  private waiters: Array<{ revision: number; resolve: () => void; reject: (error: unknown) => void }> = []
  private save: (journal: TaskJournal) => Promise<void>

  constructor(save: (journal: TaskJournal) => Promise<void>) { this.save = save }

  write(journal: TaskJournal): Promise<void> {
    const snapshot = parseTaskJournal(journal)
    const revision = ++this.revision
    this.pending = { revision, journal: snapshot }
    const result = new Promise<void>((resolve, reject) => this.waiters.push({ revision, resolve, reject }))
    void this.advance()
    return result
  }

  private async advance(): Promise<void> {
    if (this.running) return
    this.running = true
    try {
      while (this.pending) {
        const current = this.pending
        this.pending = undefined
        let error: unknown
        let failed = false
        try { await this.save(current.journal) } catch (failure) { failed = true; error = failure }
        const completed = this.waiters.filter(waiter => waiter.revision <= current.revision)
        this.waiters = this.waiters.filter(waiter => waiter.revision > current.revision)
        for (const waiter of completed) failed ? waiter.reject(error) : waiter.resolve()
      }
    } finally { this.running = false }
  }
}
