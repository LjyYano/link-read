// Native transport test double. App, menus, duplicate hook and position bridge are real.
const config = (window as any).__readingRefinementsConfig || {}
const clone = (value: any) => JSON.parse(JSON.stringify(value))
const originals = new Map((config.library || []).map((article: any) => [article.id, clone(article)]))
const overrides = new Map<string, { title: string | null; category: string | null }>()
const revisions = new Map<string, number>()
const fixture = {
  calls: [] as any[],
  library: clone(config.library || []),
  journal: { version: 1, tasks: [] } as any,
  positions: clone(config.positions || {}),
  pendingDuplicates: [] as any[],
  pendingGenerations: [] as any[],
  holdDuplicates: Boolean(config.holdDuplicates),
  missingTool: Boolean(config.missingTool),
  failDetails: false,
  cancelExport: false,
  failExport: false,
  releaseDuplicate(source: string) {
    const index = fixture.pendingDuplicates.findIndex(pending => pending.source === source)
    if (index < 0) throw Error('No duplicate lookup waiting for ' + source)
    const pending = fixture.pendingDuplicates.splice(index, 1)[0]
    pending.resolve(pending.result)
  },
  finishGeneration(id: string, article: any) {
    const index = fixture.pendingGenerations.findIndex(pending => pending.args.requestId === id)
    if (index < 0) throw Error('No generation waiting for ' + id)
    fixture.library = [clone(article), ...fixture.library.filter((item: any) => item.id !== article.id)]
    fixture.pendingGenerations.splice(index, 1)[0].resolve(clone(article))
  },
}
;(window as any).readingRefinementsFixture = fixture

function findArticle(id: string) {
  const article = fixture.library.find((item: any) => item.id === id)
  if (!article) throw Error('Archived article was removed')
  return article
}
function duplicate(source: string, localFile: boolean) {
  const article = fixture.library.find((item: any) => localFile
    ? source === '/fixture/' + item.id + '.json' : item.sourceUrl === source)
  return { key: article?.sourceUrl || source, article: article ? clone(article) : null }
}
function metadata(source: string) {
  return { title: source.includes('abcdefghij1') ? 'Original research notes' : 'A new source',
    platform: 'youtube', sourceUrl: source, author: 'Offline QA', publishedAt: null,
    durationMs: 1_740_000, thumbnailUrl: null }
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
  else if (command === 'save_task_journal') { fixture.journal = clone(args.journal); result = null }
  else if (command === 'list_articles') result = { articles: clone(fixture.library), unavailable: 0 }
  else if (command === 'check_tool') result = { executable: fixture.missingTool ? null : '/fixture/codex', keyAvailable: !fixture.missingTool }
  else if (command === 'preview_video') result = metadata(args.source)
  else if (command === 'check_source') result = { source: args.source, status: 'blocked', code: 'protected_media',
    message: 'This source is currently unavailable for transcription.', checkedAt: Date.now() }
  else if (command === 'check_duplicate') {
    result = duplicate(args.source, args.localFile)
    if (fixture.holdDuplicates) return await new Promise<T>((resolve, reject) => fixture.pendingDuplicates.push({ source: args.source, result, resolve, reject }))
  }
  else if (command === 'generate') return await new Promise<T>((resolve, reject) => {
    fixture.pendingGenerations.push({ args, resolve, reject })
    args.progress.onmessage({ stage: 'source_metadata', message: 'Source metadata is ready', completed: null, total: null,
      metadata: args.localFile ? clone(findArticle(args.source.split('/').pop().replace(/\.json$/, ''))) : metadata(args.source) })
  })
  else if (command === 'prepare_article') {
    const article = findArticle(args.id)
    result = { revision: 'reading-refinements-' + (revisions.get(args.id) || 0), readingKey: article.readingKey }
  }
  else if (command === 'article_transcript_path') result = '/fixture/' + args.id + '.json'
  else if (command === 'get_article_details') {
    const article = findArticle(args.id), original: any = originals.get(args.id) || article, value = overrides.get(args.id)
    result = { title: article.title, category: article.category, originalTitle: original.title, originalCategory: original.category,
      titleOverridden: value?.title != null, categoryOverridden: value?.category != null }
  }
  else if (command === 'update_article_details') {
    if (fixture.failDetails) throw Error('QA: details could not be saved')
    const article = findArticle(args.id), original: any = originals.get(args.id) || article
    overrides.set(args.id, { title: args.title, category: args.category })
    article.title = args.title ?? original.title
    article.category = args.category ?? original.category
    revisions.set(args.id, (revisions.get(args.id) || 0) + 1)
    result = clone(article)
  }
  else if (command === 'export_article') {
    if (fixture.failExport) throw Error('QA: export could not be saved')
    result = !fixture.cancelExport
  }
  else if (command === 'load_reading_position') result = clone(fixture.positions[args.key] ?? null)
  else if (command === 'save_reading_position') { fixture.positions[args.key] = clone(args.position); result = null }
  else if (command === 'update_article_state') {
    const article = findArticle(args.id)
    if (typeof args.isRead === 'boolean') article.isRead = args.isRead
    if (typeof args.isStarred === 'boolean') article.isStarred = args.isStarred
    result = { isRead: article.isRead, isStarred: article.isStarred }
  }
  else if (command === 'load_api_token' || command === 'load_asr_token') result = null
  else if (['open_external', 'open_video', 'open_article', 'reveal_article'].includes(command)) result = null
  else throw Error('Unexpected native command: ' + command)
  return result as T
}
