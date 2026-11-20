import assert from "node:assert/strict"
import fs from "node:fs"
import test from "node:test"
import { defaultAsr, loadAsrOptions } from "../src/asrSettings.ts"
import { parseTaskJournal, restoreTaskJournal, resumeNeedsAsrConfirmation } from "../src/taskJournal.ts"

const fixture = JSON.parse(fs.readFileSync(new URL("./fixtures/task-journal.json", import.meta.url), "utf8"))

test("old settings and task journals retain auto-detection without an FFmpeg path", () => {
  assert.equal(defaultAsr.ffmpegPath, "")
  assert.equal(loadAsrOptions(undefined).ffmpegPath, "")
  assert.equal(restoreTaskJournal(fixture)[0].input.asr.ffmpegPath, "")
})

test("a selected absolute FFmpeg path survives restart without expanding charged ASR scope", () => {
  const path = "/Users/yano/Tools/Audio Tools/ffmpeg"
  const settings = loadAsrOptions({ ...defaultAsr, enabled: true, ffmpegPath: path })
  assert.equal(settings.ffmpegPath, path)
  const data = structuredClone(fixture)
  data.tasks[0].settings.asr = settings
  assert.equal(restoreTaskJournal(data)[0].input.asr.ffmpegPath, path)
  assert.equal(resumeNeedsAsrConfirmation(settings, { ...settings, ffmpegPath: "/other/ffmpeg" }), false)
})

test("relative, control-character and oversized paths cannot enter the restored task", () => {
  for (const path of ["ffmpeg", "../ffmpeg", "/tmp/ffmpeg\n-h", "/" + "x".repeat(4096)]) {
    assert.equal(loadAsrOptions({ ffmpegPath: path }).ffmpegPath, "")
    const data = structuredClone(fixture)
    data.tasks[0].settings.asr.ffmpegPath = path
    assert.throws(() => parseTaskJournal(data), /FFmpeg 路径/)
  }
})
