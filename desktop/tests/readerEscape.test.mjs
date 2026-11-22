import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { Script, createContext } from 'node:vm'
import { ReaderEscapeBridge, readerShortcutsBlocked } from '../src/readerEscape.ts'

const sourceRoot = process.env.LINKREAD_ESCAPE_SOURCE
const read = path => readFileSync(sourceRoot ? `${sourceRoot}/${path}` : new URL(`../../${path}`, import.meta.url), 'utf8')

// Execute the shipped map and desktop bridge event handlers together. The DOM
// harness leaves rendering dormant; the browser suite verifies rendered layers.
function mapFrame() {
  const listeners = new Map(), messages = [], nodes = new Map()
  let focused
  const element = () => ({
    hidden: true, dataset: {}, textContent: '',
    classList: {
      values: new Set(),
      add(name) { this.values.add(name) }, remove(name) { this.values.delete(name) },
      contains(name) { return this.values.has(name) },
      toggle(name, active) { active ? this.add(name) : this.remove(name) },
    },
    setAttribute() {}, addEventListener() {}, focus() { focused = this },
    querySelector(selector) { if (!nodes.has(selector)) nodes.set(selector, element()); return nodes.get(selector) },
    querySelectorAll() { return [] },
  })
  const panel = element(), wrap = element(), svg = element()
  panel.querySelector = () => svg
  svg.closest = () => wrap
  const document = {
    readyState: 'loading', fullscreenElement: null, body: element(), documentElement: element(),
    querySelector: selector => selector === '[data-panel="mindmap"]' ? panel : null,
    querySelectorAll: () => [],
    getElementById: id => ({ textContent: id === 'yano-mindmap-data' ? '"# Root\\n## Topic"' : '{}' }),
    addEventListener(name, callback) { const list = listeners.get(name) || []; list.push(callback); listeners.set(name, list) },
    async exitFullscreen() { this.fullscreenElement = null; dispatch('fullscreenchange') },
  }
  const parent = { postMessage: value => { if (value.linkReadShortcut) messages.push(value) } }
  const window = { parent, addEventListener() {}, LinkReadReadingPosition: { fingerprint: () => 'map', visibleCenter: x => x } }
  const sandbox = createContext({ document, window, localStorage: { getItem: () => null },
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    MutationObserver: class { observe() {} }, requestAnimationFrame: () => 1, cancelAnimationFrame() {},
    setTimeout, clearTimeout, console, getComputedStyle: () => ({ display: 'none' }),
  })
  new Script(read('src/linkread_desktop/renderers/mindmap.js')).runInContext(sandbox)
  new Script(read('desktop/src-tauri/src/preview.html').match(/<script>([\s\S]*?)<\/script>/)[1]).runInContext(sandbox)
  function dispatch(name, attributes = {}) {
    const event = { key: 'Escape', isTrusted: true, defaultPrevented: false, stopped: false,
      preventDefault() { this.defaultPrevented = true },
      stopPropagation() {}, stopImmediatePropagation() { this.stopped = true }, ...attributes }
    for (const callback of listeners.get(name) || []) { callback(event); if (event.stopped) break }
    return event
  }
  return { document, wrap, messages, dispatch, fullscreenButton: nodes.get('[data-mindmap-fullscreen]'), focused: () => focused }
}

test('CSS fullscreen consumes one Escape before the native bridge can navigate', () => {
  const frame = mapFrame()
  frame.wrap.classList.add('is-fullscreen')
  const event = frame.dispatch('keydown')
  assert.equal(frame.wrap.classList.contains('is-fullscreen'), false)
  assert.equal(event.defaultPrevented, true)
  assert.equal(frame.messages.length, 0)
  assert.equal(frame.focused(), frame.fullscreenButton)
  frame.dispatch('keyup')
  frame.dispatch('keydown')
  assert.equal(frame.messages.length, 1)
  assert.equal(frame.messages[0].linkReadShortcut, 'Escape')
})

test('IME composition and a held Escape neither close the layer nor navigate', () => {
  for (const attributes of [{ isComposing: true }, { keyCode: 229 }, { repeat: true }]) {
    const frame = mapFrame()
    frame.wrap.classList.add('is-fullscreen')
    frame.dispatch('keydown', attributes)
    assert.equal(frame.wrap.classList.contains('is-fullscreen'), true)
    assert.equal(frame.messages.length, 0)
  }
})

test('native fullscreen handled by JS returns focus without forwarding Escape', async () => {
  const frame = mapFrame()
  frame.document.fullscreenElement = frame.wrap
  frame.dispatch('fullscreenchange')
  frame.dispatch('keydown')
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(frame.document.fullscreenElement, null)
  assert.equal(frame.messages.length, 0)
  assert.equal(frame.focused(), frame.fullscreenButton)
  frame.dispatch('keyup')
  frame.dispatch('keydown')
  assert.equal(frame.messages.length, 1)
})

test('native browser exit before JS consumes only that key, not the next press', () => {
  const frame = mapFrame()
  frame.document.fullscreenElement = frame.wrap
  frame.dispatch('fullscreenchange')
  frame.document.fullscreenElement = null
  frame.dispatch('fullscreenchange')
  frame.dispatch('keydown')
  assert.equal(frame.messages.length, 0)
  frame.dispatch('keyup')
  frame.dispatch('keydown')
  assert.equal(frame.messages.length, 1)
})

test('WebKit keyup before delayed fullscreenchange does not swallow the next Escape', () => {
  const frame = mapFrame()
  frame.document.fullscreenElement = frame.wrap
  frame.dispatch('fullscreenchange')
  // WebKit consumes the first keydown natively, then releases Escape while
  // fullscreenElement still names the map. Its exit event arrives later.
  frame.dispatch('keyup')
  frame.document.fullscreenElement = null
  frame.dispatch('fullscreenchange')
  assert.equal(frame.messages.length, 0)
  assert.equal(frame.focused(), frame.fullscreenButton)
  frame.dispatch('keydown')
  frame.dispatch('keyup')
  assert.equal(frame.messages.length, 1)
  assert.equal(frame.messages[0].linkReadShortcut, 'Escape')
})

test('outer toolbar Escape waits for the current frame response and exits once', () => {
  const sent = [], bridge = new ReaderEscapeBridge(message => sent.push(message))
  let exits = 0
  bridge.request(() => exits++)
  bridge.request(() => exits++)
  assert.equal(sent.length, 1)
  bridge.receive({ linkReadEscapeResult: { id: 'wrong', handled: false } })
  bridge.receive({ linkReadEscapeResult: { id: sent[0].linkReadEscapeRequest } })
  assert.equal(exits, 0)
  const result = { linkReadEscapeResult: { id: sent[0].linkReadEscapeRequest, handled: false } }
  bridge.receive(result)
  bridge.receive(result)
  bridge.receive({ linkReadEscapeResult: { id: undefined, handled: false } })
  assert.equal(exits, 1)
})

test('handled layers and responses from an abandoned article cannot leave it', () => {
  const sent = [], bridge = new ReaderEscapeBridge(message => sent.push(message))
  let exits = 0
  bridge.request(() => exits++)
  bridge.receive({ linkReadEscapeResult: { id: sent[0].linkReadEscapeRequest, handled: true } })
  bridge.request(() => exits++)
  bridge.cancel()
  const next = new ReaderEscapeBridge(message => sent.push(message))
  next.request(() => exits++)
  assert.notEqual(sent[1].linkReadEscapeRequest, sent[2].linkReadEscapeRequest)
  const old = { linkReadEscapeResult: { id: sent[1].linkReadEscapeRequest, handled: false } }
  bridge.receive(old); next.receive(old)
  assert.equal(exits, 0)
  next.cancel()
})

test('an unresponsive or unavailable frame never causes an unconfirmed navigation', async () => {
  let exits = 0
  const sent = [], bridge = new ReaderEscapeBridge(message => sent.push(message), 10)
  bridge.request(() => exits++)
  await new Promise(resolve => setTimeout(resolve, 25))
  bridge.receive({ linkReadEscapeResult: { id: sent[0].linkReadEscapeRequest, handled: false } })
  assert.equal(exits, 0)
  const broken = new ReaderEscapeBridge(() => { throw Error('Frame replaced') })
  broken.request(() => exits++)
  assert.equal(exits, 0)
})

test('native dialogs, modal drawers and explicit blockers take keyboard priority', () => {
  assert.equal(readerShortcutsBlocked({ querySelector: selector => {
    assert.match(selector, /dialog\[open\]/)
    assert.match(selector, /\[role="dialog"\]\[aria-modal="true"\]/)
    assert.match(selector, /data-reader-shortcuts-blocked/)
    return {}
  } }), true)
  assert.equal(readerShortcutsBlocked({ querySelector: () => null }), false)
})
