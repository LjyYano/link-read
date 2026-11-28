import test from "node:test"
import assert from "node:assert/strict"
import { createRequire } from "node:module"

const require = createRequire(import.meta.url)
const { placeKeywords } = require("../../src/linkread_desktop/renderers/keyword-layout.js")
const OPTIONS = { margin: 4, gap: 4 }
const HEIGHT = 640

// Both generators keep the total area near 39% of a `width` x 640 canvas, so every case is
// dense enough to be realistic and always small enough to fit.
const scale = (count, width) => Math.min((width - 8) / 440, Math.sqrt(52 / count) * 0.55 * (width / 670))

// Sizes fall with rank as in the rendered cloud: a few large words, then many small ones.
const cloud = (count, width, seed = 1) => {
  const k = scale(count, width)
  return Array.from({ length: count }, (_, index) => {
    const rank = (1 + index * 0.6) ** -1.15, extra = ((index * 97 + seed * 53) % 40) / 100
    return { w: Math.round((50 + 190 * rank + 120 * extra) * k), h: Math.round((16 + 42 * rank) * k) }
  })
}

// Equally large rectangles stress the edge scan that follows the spiral.
const uniform = (count, width, seed = 1) => {
  const k = scale(count, width)
  return Array.from({ length: count }, (_, index) => ({
    w: Math.round((60 + ((index * 97 + seed * 53) % 380)) * k), h: Math.round((26 + ((index * 31) % 34)) * k) }))
}

function violations(boxes, items, width, height) {
  const { margin, gap } = OPTIONS, found = []
  boxes.forEach(({ x, y }, index) => {
    const { w, h } = items[index]
    if (x < margin || y < margin || x + w > width - margin || y + h > height - margin) found.push(["bounds", index])
    for (let other = index + 1; other < boxes.length; other++) {
      const b = boxes[other], i = items[other]
      if (x < b.x + i.w + gap && x + w + gap > b.x && y < b.y + i.h + gap && y + h + gap > b.y) found.push(["overlap", index, other])
    }
  })
  return found
}

test("every keyword stays inside the canvas without touching another", () => {
  for (const sizes of [cloud, uniform]) for (const count of [1, 7, 14, 30, 52]) for (const width of [670, 480, 300]) for (const seed of [1, 2, 3]) {
    const items = sizes(count, width, seed)
    const boxes = placeKeywords(items, width, HEIGHT, OPTIONS)
    assert.equal(boxes?.length, count, `${sizes.name}: ${count} words at ${width}px`)
    assert.deepEqual(violations(boxes, items, width, HEIGHT), [], `${sizes.name}: ${count} words at ${width}px`)
  }
})

test("the same keywords and width always produce the same layout", () => {
  const items = cloud(14, 670)
  assert.deepEqual(placeKeywords(items, 670, HEIGHT, OPTIONS), placeKeywords(items, 670, HEIGHT, OPTIONS))
})

test("the first keyword sits at the centre and the cloud spreads sideways", () => {
  const items = cloud(14, 670)
  const boxes = placeKeywords(items, 670, HEIGHT, OPTIONS)
  const centre = index => [boxes[index].x + items[index].w / 2, boxes[index].y + items[index].h / 2]
  assert.ok(Math.abs(centre(0)[0] - 335) <= 1 && Math.abs(centre(0)[1] - 320) <= 1, String(centre(0)))
  const left = Math.min(...boxes.map(box => box.x)), right = Math.max(...boxes.map((box, index) => box.x + items[index].w))
  const top = Math.min(...boxes.map(box => box.y)), bottom = Math.max(...boxes.map((box, index) => box.y + items[index].h))
  assert.ok(right - left > (bottom - top) * 1.15, `${right - left} wide x ${bottom - top} tall`)
})

test("a keyword that cannot fit returns null instead of a partial layout", () => {
  assert.equal(placeKeywords([{ w: 664, h: 30 }], 670, 300, OPTIONS), null)
  assert.equal(placeKeywords([{ w: 100, h: 294 }], 670, 300, OPTIONS), null)
  assert.equal(placeKeywords([{ w: 100, h: 30 }, { w: 700, h: 30 }], 670, 300, OPTIONS), null)
})
