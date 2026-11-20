// Test transport only. App, hooks, task journal, queue, dialogs and reader bridge are real modules.
const config = (window as any).__taskWorkspaceConfig || {}
const clone = (value: any) => JSON.parse(JSON.stringify(value))
const fixture = {
  calls: [] as any[],
  library: clone(config.library || []),
  journal: clone(config.journal || { version: 1, tasks: [] }),
  positions: clone(config.positions || {}),
  failJournal: false,
  pickedPath: '/fixture/imported-subtitles.srt',
  pendingGenerations: [] as any[],
  pendingSourceChecks: [] as any[],
  holdSourceChecks: true,
  releaseSource(source: string, result: any) {
    const index = fixture.pendingSourceChecks.findIndex((pending: any) => pending.source === source)
    if (index < 0) throw Error('No source check waiting for ' + source)
    fixture.pendingSourceChecks.splice(index, 1)[0].resolve(clone(result))
  },
  failGeneration(requestId: string, failure: any) {
    const index = fixture.pendingGenerations.findIndex((pending: any) => pending.args.requestId === requestId)
    if (index < 0) throw Error('No generation waiting for ' + requestId)
    fixture.pendingGenerations.splice(index, 1)[0].reject(clone(failure))
  },
}
;(window as any).taskWorkspaceFixture = fixture

function metadata(source: string) {
  const podcast = source.includes('podcasts.apple.com')
  return { title: podcast ? source.includes('6788661772') ? 'Human Stories · Episode 001' : '商业就是这样 · 单集预览'
    : `Processing video ${source.slice(-3)}`, platform: podcast ? 'apple_podcasts' : 'youtube',
    sourceUrl: source, author: 'Offline QA', publishedAt: null, durationMs: podcast ? 388000 : 1740000,
    thumbnailUrl: null }
}

export class Channel<T> {
  onmessage: (message: T) => void
  constructor(callback: (message: T) => void) { this.onmessage = callback }
}
export const isTauri = () => false
export const getCurrentWebview = () => ({ onDragDropEvent: async () => () => {} })
export const getCurrentWindow = () => ({})

export async function invoke<T>(command: string, args: any = {}): Promise<T> {
  fixture.calls.push({ command, args: clone({ ...args, ...(args.progress ? { progress: 'test-channel' } : {}) }) })
  let result: any
  if (command === 'load_task_journal') result = clone(fixture.journal)
  else if (command === 'save_task_journal') {
    if (fixture.failJournal) throw Error('QA: task journal is temporarily unwritable')
    fixture.journal = clone(args.journal)
    result = null
  }
  else if (command === 'list_articles') result = { articles: clone(fixture.library), unavailable: 0 }
  else if (command === 'check_tool') result = { executable: '/fixture/codex', keyAvailable: true }
  else if (command === 'preview_video') result = metadata(args.source)
  else if (command === 'check_source') {
    if (fixture.holdSourceChecks) return await new Promise<T>((resolve, reject) => fixture.pendingSourceChecks.push({ source: args.source, resolve, reject }))
    result = { source: args.source, status: 'unknown', code: 'qa_unknown', message: '尚未确认字幕是否可读取', checkedAt: Date.now() }
  }
  else if (command === 'check_duplicate') result = { key: args.localFile ? 'local:qa-subtitles' : args.source, article: null }
  else if (command === 'generate') {
    return await new Promise<T>((resolve, reject) => {
      fixture.pendingGenerations.push({ args, resolve, reject })
      args.progress.onmessage({ stage: 'source_metadata', message: '已获取来源信息', completed: null, total: null,
        metadata: args.localFile ? { ...metadata(args.source), title: 'Imported subtitles', platform: 'local', sourceUrl: null } : metadata(args.source) })
      args.progress.onmessage({ stage: 'ai_done', message: '已完成 1/3 批', completed: 1, total: 3,
        previewId: 'a'.repeat(64) })
    })
  }
  else if (command === 'cancel_generation') {
    fixture.failGeneration(args.requestId, { code: 'cancelled', message: '已取消生成', retryable: true, action: 'retry' })
    result = null
  }
  else if (command === 'prepare_article') {
    const article = fixture.library.find((row: any) => row.id === args.id)
    if (!article) throw Error('Archived article was removed')
    result = { revision: 'task-workspace-fixture', readingKey: article.readingKey }
  }
  else if (command === 'prepare_preview') result = { revision: 'task-workspace-preview', readingKey: 'qa-workspace-preview',
    article: { ...clone(fixture.library.find((row: any) => row.id === 'qa-resume')), id: 'qa-preview', title: 'Original transcript', previewId: args.id } }
  else if (command === 'load_reading_position') result = clone(fixture.positions[args.key] ?? null)
  else if (command === 'save_reading_position') { fixture.positions[args.key] = clone(args.position); result = null }
  else if (command === 'pick_subtitle') result = fixture.pickedPath
  else if (command === 'load_api_token' || command === 'load_asr_token') result = null
  else if (['open_external', 'open_video', 'open_article', 'reveal_article'].includes(command)) result = null
  else if (command === 'delete_article') { fixture.library = fixture.library.filter((article: any) => article.id !== args.id); result = null }
  else if (command === 'update_article_state') {
    const article = fixture.library.find((row: any) => row.id === args.id)
    if (!article) throw Error('Missing article')
    if (typeof args.isRead === 'boolean') article.isRead = args.isRead
    if (typeof args.isStarred === 'boolean') article.isStarred = args.isStarred
    result = { isRead: article.isRead, isStarred: article.isStarred }
  }
  else throw Error('Unexpected native command: ' + command)
  return result as T
}
