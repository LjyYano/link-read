import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { thumbnailSource, mergeArticleMetadata } from '../src/coverSource.ts'
import { titlebarMouseDown } from '../src/titlebarActions.ts'

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8')

test('double-click toggles maximize and restores the original bounds once', async () => {
  let bounds=[120,90,1060,640], saved, drags=0, toggles=0
  const original=[...bounds]
  const window={
    async startDragging(){drags++},
    async toggleMaximize(){toggles++; if(saved){bounds=saved;saved=undefined}else{saved=[...bounds];bounds=[0,0,1920,1080]}},
  }
  const press=detail => ({button:0,buttons:1,detail})
  await titlebarMouseDown(press(1),window)
  await titlebarMouseDown(press(2),window)
  assert.deepEqual(bounds,[0,0,1920,1080])
  assert.equal(toggles,1)
  await titlebarMouseDown(press(1),window)
  await titlebarMouseDown(press(2),window)
  assert.deepEqual(bounds,original)
  assert.equal(toggles,2)
  assert.equal(drags,2)
  await titlebarMouseDown({button:2,buttons:2,detail:2},window)
  await titlebarMouseDown(press(3),window)
  assert.equal(toggles,2)
})

test('titlebar action has permission and is not mixed with implicit double-click handling', () => {
  const capabilities=JSON.parse(read('../src-tauri/capabilities/main.json'))
  assert.ok(capabilities.permissions.includes('core:window:allow-toggle-maximize'))
  const main=read('../src/main.tsx')
  const titleBar=read('../src/TitleBar.tsx')
  assert.match(titleBar,/titlebarMouseDown\(event, getCurrentWindow\(\)\)/)
  assert.doesNotMatch(main,/className="titlebar-drag"[^>]*data-tauri-drag-region/)
  assert.doesNotMatch(titleBar,/className="titlebar-drag"[^>]*data-tauri-drag-region/)
})

test('Bilibili cover URLs accept HTTP or relative input but only fetch trusted HTTPS', () => {
  for (const url of ['http://i1.hdslb.com/bfs/x.jpg','//i1.hdslb.com/bfs/x.jpg','https://i1.hdslb.com/bfs/x.jpg']) {
    assert.equal(thumbnailSource(url),'https://i1.hdslb.com/bfs/x.jpg')
  }
  for (const url of ['http://evil.test/1.png','https://i0.hdslb.com.evil.test/x.jpg','https://a:b@i0.hdslb.com/a.jpg','https://i0.hdslb.com:443/a.jpg','file:///secret','https://i0.hdslb.com\\@evil.test/x.jpg']) {
    assert.equal(thumbnailSource(url),undefined)
  }
})

test('later metadata with missing cover must not erase a successful preview', () => {
  const previous={title:'预览标题',thumbnailUrl:'https://i0.hdslb.com/bfs/a.jpg',durationMs:10000,status:'running'}
  const merged=mergeArticleMetadata(previous,{title:'已读取标题',thumbnailUrl:null,durationMs:null})
  assert.equal(merged.thumbnailUrl,previous.thumbnailUrl)
  assert.equal(merged.durationMs,10000)
  assert.equal(merged.title,'已读取标题')
  assert.equal(merged.status,'running')
  assert.equal(previous.title,'预览标题')
})
