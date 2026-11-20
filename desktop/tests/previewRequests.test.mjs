import assert from "node:assert/strict"
import test from "node:test"
import { PreviewRequests } from "../src/previewRequests.ts"

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

test("composer, visible cover and retry share one request for an episode", async () => {
  const requests = new PreviewRequests()
  const response = deferred()
  let calls = 0
  const loader = () => { calls++; return response.promise }
  const composer = requests.request("episode", loader)
  const cover = requests.request("episode", loader)
  await Promise.resolve()
  assert.equal(calls, 1)
  assert.equal(composer, cover)
  // A timed-out view can retry while the native call is still winding down.
  const retry = requests.request("episode", loader)
  assert.equal(retry, composer)
  response.resolve({ title: "单集标题" })
  assert.deepEqual(await retry, { title: "单集标题" })
  assert.equal(calls, 1)
})

test("a response remains reusable after its original input subscription ends", async () => {
  const requests = new PreviewRequests()
  const response = deferred()
  const oldSubscription = requests.request("episode", () => response.promise)
  // No UI subscriber awaits this response until the user pastes the link again.
  response.resolve({ title: "已识别的播客" })
  await oldSubscription
  assert.deepEqual(requests.peek("episode"), { title: "已识别的播客" })
  const cached = await requests.request("episode", () => assert.fail("must use cache"))
  assert.equal(cached, requests.peek("episode"))
})

test("different episodes settle independently and do not share metadata", async () => {
  const requests = new PreviewRequests()
  const a = deferred(), b = deferred()
  const first = requests.request("a", () => a.promise)
  const second = requests.request("b", () => b.promise)
  b.resolve({ title: "B" })
  await second
  assert.equal(requests.peek("a"), undefined)
  a.resolve({ title: "A" })
  await first
  assert.deepEqual(requests.peek("a"), { title: "A" })
  assert.deepEqual(requests.peek("b"), { title: "B" })
})

test("network failures and synchronous failures can be retried", async () => {
  const requests = new PreviewRequests()
  await assert.rejects(requests.request("a", () => Promise.reject(new Error("network"))), /network/)
  assert.equal(requests.peek("a"), undefined)
  await assert.rejects(requests.request("a", () => { throw new Error("bridge") }), /bridge/)
  assert.deepEqual(await requests.request("a", () => Promise.resolve({ title: "成功" })), { title: "成功" })
})

test("metadata expiry starts on completion; cache stays bounded", async () => {
  let now = 100
  const requests = new PreviewRequests(1000, 2, () => now)
  const response = deferred()
  const request = requests.request("a", () => response.promise)
  now = 1100
  response.resolve("A")
  await request
  now = 2099
  assert.equal(requests.peek("a"), "A")
  now = 2100
  assert.equal(requests.peek("a"), undefined)
  await requests.request("a", () => Promise.resolve("new A"))
  await requests.request("b", () => Promise.resolve("B"))
  await requests.request("c", () => Promise.resolve("C"))
  assert.equal(requests.peek("a"), undefined)
  assert.equal(requests.peek("b"), "B")
  assert.equal(requests.peek("c"), "C")
})
