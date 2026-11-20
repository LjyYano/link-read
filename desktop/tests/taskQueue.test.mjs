import assert from "node:assert/strict"
import test from "node:test"
import { SerialTaskQueue } from "../src/taskQueue.ts"

function deferred() {
  let resolve, reject
  const promise = new Promise((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}
const tick = () => new Promise(resolve => setImmediate(resolve))

function task(id, operation, events) {
  return {
    id,
    onStart: () => events.push("start:" + id),
    run: operation,
    onSuccess: value => events.push("done:" + id + ":" + value),
    onFailure: error => events.push("error:" + id + ":" + error.message),
    onFinish: () => events.push("finish:" + id),
  }
}

test("only one job runs; later submissions execute in FIFO order", async () => {
  const queue = new SerialTaskQueue()
  const a = deferred(), b = deferred()
  const events = []
  queue.enqueue(task("a", () => a.promise, events))
  queue.enqueue(task("b", () => b.promise, events))
  queue.enqueue(task("c", async () => "C", events))
  assert.deepEqual(events, ["start:a"])
  assert.deepEqual(queue.waitingIds, ["b", "c"])
  a.resolve("A")
  await tick()
  assert.deepEqual(events, ["start:a", "done:a:A", "finish:a", "start:b"])
  b.resolve("B")
  await tick()
  assert.deepEqual(events, [
    "start:a", "done:a:A", "finish:a", "start:b",
    "done:b:B", "finish:b", "start:c", "done:c:C", "finish:c",
  ])
  assert.equal(queue.activeId, null)
})

test("queued cancellation removes only the waiting job", async () => {
  const queue = new SerialTaskQueue()
  const a = deferred(), events = []
  queue.enqueue(task("a", () => a.promise, events))
  queue.enqueue(task("b", async () => "B", events))
  queue.enqueue(task("c", async () => "C", events))
  assert.equal(queue.cancelWaiting("b"), true)
  assert.equal(queue.cancelWaiting("b"), false)
  assert.equal(queue.cancelWaiting("a"), false)
  assert.deepEqual(queue.waitingIds, ["c"])
  a.resolve("A")
  await tick()
  assert.deepEqual(events.map(event => event.split(":").slice(0, 2).join(":")), [
    "start:a", "done:a", "finish:a", "start:c", "done:c", "finish:c",
  ])
})

test("a failure unblocks subsequent queued jobs", async () => {
  const queue = new SerialTaskQueue()
  const events = []
  queue.enqueue(task("a", async () => { throw new Error("network") }, events))
  queue.enqueue(task("b", async () => "B", events))
  await tick()
  assert.deepEqual(events, ["start:a", "error:a:network", "finish:a", "start:b", "done:b:B", "finish:b"])
})

test("an active cancellation rejection does not prevent the next job", async () => {
  const queue = new SerialTaskQueue()
  const a = deferred(), events = []
  queue.enqueue(task("a", () => a.promise, events))
  queue.enqueue(task("b", async () => "B", events))
  a.reject(new Error("已取消生成"))
  await tick()
  assert.deepEqual(events, ["start:a", "error:a:已取消生成", "finish:a", "start:b", "done:b:B", "finish:b"])
})
