import assert from "node:assert/strict"
import fs from "node:fs"
import test from "node:test"
import { TaskJournalWriter, parseTaskJournal, restoreTaskJournal, resumeNeedsAsrConfirmation, taskSnapshot } from "../src/taskJournal.ts"
import { SerialTaskQueue } from "../src/taskQueue.ts"

const fixture = JSON.parse(fs.readFileSync(new URL("./fixtures/task-journal.json", import.meta.url), "utf8"))
const tick = () => new Promise(resolve => setImmediate(resolve))
function deferred() {
  let resolve, reject
  const promise = new Promise((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}
function journal(status = "running", id = "task_a") {
  const data = structuredClone(fixture)
  Object.assign(data.tasks[0], { status, id })
  return data
}

test("journal roundtrip preserves source identity, confirmed progress and actionable failure", () => {
  const restored = restoreTaskJournal(fixture)[0]
  assert.equal(restored.input.input, fixture.tasks[0].input.source)
  assert.equal(restored.input.previewId, "a".repeat(64))
  assert.equal(restored.input.identity.title, "商业就是这样 · 小历史")
  assert.equal(restored.job.activity.lanes.polish.completed, 2)
  assert.equal(restored.job.activity.lanes.analysis.state, "complete")
  assert.equal(restored.job.failure.action, "retry")
  assert.equal(restored.job.failure.stage, "polish")
  assert.equal(restored.input.replace, false, "a saved replace intent is not renewed permission")
  const roundtrip = taskSnapshot(restored.id, restored.input, restored.job, restored.createdAt, restored.updatedAt)
  assert.equal(roundtrip.input.source, fixture.tasks[0].input.source)
  assert.equal(roundtrip.input.replaceRequested, false)
})

test("the task journal accepts an IPv6 loopback API address", () => {
  const value = journal()
  value.tasks[0].settings.baseUrl = "http://[::1]:8000/v1"
  assert.equal(parseTaskJournal(value).tasks[0].settings.baseUrl, value.tasks[0].settings.baseUrl)
  value.tasks[0].settings.baseUrl = "http://[::2]/v1"
  assert.throws(() => parseTaskJournal(value), /服务地址无效/)
})

test("restart retains FIFO order and turns active or waiting work into interrupted records", () => {
  const source = { version:1, tasks:["running", "cancelling", "queued", "error", "cancelled"].map((status, index) => journal(status, `task_${index}`).tasks[0]) }
  const before = structuredClone(source)
  const restored = restoreTaskJournal(source)
  assert.deepEqual(restored.map(task => task.job.status), ["interrupted", "interrupted", "interrupted", "error", "cancelled"])
  assert.deepEqual(restored.map(task => task.id), ["task_0", "task_1", "task_2", "task_3", "task_4"])
  assert.deepEqual(source, before, "restoration must not rewrite the source object")
  for (const task of restored) assert.equal(task.input.replace, false)
  assert.deepEqual(restoreTaskJournal({version:1,tasks:[]}), [])
  assert.throws(() => parseTaskJournal(journal("done")), /状态无效/)
})

test("only the non-secret whitelist is copied and stale request activity is removed", () => {
  const data = journal()
  data.token = "top-secret"
  data.tasks[0].input.token = "source-secret"
  data.tasks[0].settings.token = "model-secret"
  data.tasks[0].settings.asr.token = "asr-secret"
  data.tasks[0].metadata.authorization = "metadata-secret"
  data.tasks[0].progress.activity.request = { active:4, queued:8, token:"request-secret" }
  data.tasks[0].progress.activity.lastResultAt = 2
  const result = parseTaskJournal(data)
  const wire = JSON.stringify(result)
  assert.doesNotMatch(wire, /top-secret|source-secret|model-secret|asr-secret|metadata-secret|request-secret/)
  assert.equal(result.tasks[0].progress.activity.request, undefined)
  assert.equal(result.tasks[0].progress.activity.lastResultAt, undefined)
  result.tasks[0].metadata.title = "changed"
  assert.equal(data.tasks[0].metadata.title, fixture.tasks[0].metadata.title)
})

test("invalid and oversized records fail visibly instead of silently dropping older work", () => {
  const tooMany = { version:1,tasks:Array.from({length:51}, (_,index) => journal("queued",`task_${index}`).tasks[0]) }
  assert.throws(() => parseTaskJournal(tooMany), /50/)
  const badPreview = journal(); badPreview.tasks[0].previewId = "../preview"
  assert.throws(() => parseTaskJournal(badPreview), /预览标识/)
  const badEndpoint = journal(); badEndpoint.tasks[0].settings.baseUrl = "https://example.com/v1?api_key=secret"
  assert.throws(() => parseTaskJournal(badEndpoint), /凭据或查询参数/)
  assert.throws(() => parseTaskJournal({version:2,tasks:[]}), /版本/)
  const duplicate = journal(); duplicate.tasks.push(structuredClone(duplicate.tasks[0]))
  assert.throws(() => parseTaskJournal(duplicate), /重复/)
  const tooLarge = { version:1,tasks:Array.from({length:50}, (_,index) => {
    const value = journal("queued",`large_${index}`).tasks[0]
    value.metadata.title = "中".repeat(2000)
    value.progress.activity.history = Array.from({length:12}, () => ({at:1,stage:"polish",message:"中".repeat(2000)}))
    return value
  }) }
  assert.throws(() => parseTaskJournal(tooLarge), /1 MiB/)
})

test("current ASR scope needs new confirmation only when it expands or changes the charged work", () => {
  const previous = fixture.tasks[0].settings.asr
  assert.equal(resumeNeedsAsrConfirmation(previous, {...previous}), false)
  assert.equal(resumeNeedsAsrConfirmation(previous, {...previous,enabled:true}), true)
  const enabled = {...previous,enabled:true}
  assert.equal(resumeNeedsAsrConfirmation(enabled,{...enabled,maxMinutes:60}), false)
  assert.equal(resumeNeedsAsrConfirmation(enabled,{...enabled,maxMinutes:240}), true)
  assert.equal(resumeNeedsAsrConfirmation(enabled,{...enabled,chunkMinutes:10}), true)
  assert.equal(resumeNeedsAsrConfirmation(enabled,{...enabled,baseUrl:"https://other.example.com"}), true)
  assert.equal(resumeNeedsAsrConfirmation(enabled,{...enabled,enabled:false}), false)
})

test("serialized writes coalesce pending snapshots and cannot resurrect a removed task", async () => {
  const gates = [deferred(),deferred()]
  const writes = []
  let disk
  const writer = new TaskJournalWriter(async value => {
    const index = writes.length
    writes.push(value)
    await gates[index].promise
    disk = value
  })
  const first = writer.write(journal())
  const second = writer.write(journal("cancelled"))
  const removed = writer.write({version:1,tasks:[]})
  assert.equal(writes.length,1)
  gates[0].resolve()
  await first
  await tick()
  assert.equal(writes.length,2)
  assert.deepEqual(writes[1],{version:1,tasks:[]})
  gates[1].resolve()
  await Promise.all([second,removed])
  assert.deepEqual(restoreTaskJournal(disk),[])
})

test("a failed write rejects its own admission but does not poison a later explicit save", async () => {
  const gate = deferred()
  let count = 0, disk
  const writer = new TaskJournalWriter(async value => {
    if (++count === 1) await gate.promise
    disk = value
  })
  const rejected = assert.rejects(writer.write(journal()), /disk full/)
  const retry = writer.write(journal("interrupted"))
  gate.reject(new Error("disk full"))
  await rejected
  await retry
  assert.equal(disk.tasks[0].status,"interrupted")
})

test("generation does not begin before the private journal confirms durable admission", async () => {
  const gate = deferred(), events = []
  const writer = new TaskJournalWriter(() => gate.promise)
  const queue = new SerialTaskQueue()
  queue.enqueue({id:"restore",beforeStart:() => writer.write(journal()),onStart:() => events.push("started"),
    run:async () => { events.push("model called"); return "done" },onSuccess:() => events.push("success"),onFailure:() => events.push("failure")})
  await tick()
  assert.deepEqual(events,[])
  gate.resolve()
  await tick()
  assert.deepEqual(events,["started","model called","success"])
})

test("cancel or remove during the first durable write prevents all generation work", async () => {
  const gate = deferred(), events = []
  const queue = new SerialTaskQueue()
  queue.enqueue({id:"cancel",beforeStart:() => gate.promise,onStart:() => events.push("started"),
    run:async () => events.push("model called"),onSuccess:() => events.push("success"),onFailure:() => events.push("failure")})
  assert.equal(queue.cancelWaiting("cancel"),true)
  assert.equal(queue.cancelWaiting("cancel"),false)
  gate.resolve()
  await tick()
  assert.deepEqual(events,[])
  assert.equal(queue.activeId,null)
})

test("failed durable admission never calls the model and keeps later queued work in FIFO order", async () => {
  const queue = new SerialTaskQueue(), events = []
  queue.enqueue({id:"failed",beforeStart:async () => {throw new Error("disk unavailable")},onStart:() => events.push("bad started"),
    run:async () => events.push("bad model"),onSuccess:() => events.push("bad success"),onFailure:() => events.push("saved failure")})
  for (const id of ["second","third"]) queue.enqueue({id,onStart:() => events.push(id),run:async () => id,onSuccess:() => {},onFailure:() => {}})
  await tick()
  assert.deepEqual(events,["saved failure","second","third"])
})
