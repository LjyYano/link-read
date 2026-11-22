"""Actual App checks for duplicate routing, metadata edits and export scope.

Only native transport and reader HTML are fixtures. All external requests are
blocked; no model, source download, ASR or system save dialog is invoked.
"""
import asyncio
import html
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

ROOT = Path(os.environ.get('LINKREAD_REPO', Path(__file__).resolve().parents[3])).resolve()
OUT = Path(os.environ.get('LINKREAD_REFINEMENTS_OUT', ROOT.parent / 'reading-refinements-verification')).resolve()
SITE = OUT / 'site'
ENGINE = os.environ.get('LINKREAD_ENGINE', 'chromium')
BASE = 'http://reading-refinements.linkread.test/'
SOURCE_A = 'https://www.youtube.com/watch?v=abcdefghij1'
SOURCE_B = 'https://www.youtube.com/watch?v=abcdefghij2'
RESULTS = []

PREFS = {'provider': 'codex', 'paths': {'codex': '/fixture/codex', 'claude': ''},
         'api': {'baseUrl': '', 'model': ''},
         'asr': {'enabled': False, 'reuseToken': False, 'baseUrl': 'https://dashscope.aliyuncs.com/api/v1',
                 'model': 'paraformer-v2', 'maxMinutes': 120, 'chunkMinutes': 5, 'ffmpegPath': ''}}
POSITION = {'version': 1, 'updatedAt': 1794974400000, 'activeTab': 'transcript',
            'tabs': {'transcript': {'signature': 'p1-0123456789abcdef-1',
                                    'anchor': {'kind': 'cue', 'cue': 20, 'endCue': 22,
                                               'token': 'p1-0123456789abcdef-24', 'occurrence': 0},
                                    'offset': 48, 'ratio': 0.42}},
            'mindmap': None}


def article(identity='qa-saved', title='Original research notes', source=SOURCE_A):
    return {'id': identity, 'title': title, 'platform': 'youtube', 'author': 'Offline QA', 'category': 'Science',
            'hasAnalysis': True, 'sourceUrl': source, 'updatedAt': 1794974400000, 'publishedAt': None,
            'durationMs': 1740000, 'thumbnailUrl': None, 'isRead': False, 'isStarred': True,
            'readingKey': 'qa-refinements-' + identity}


async def routes(route):
    parsed = urlparse(route.request.url)
    if parsed.hostname != 'reading-refinements.linkread.test':
        await route.abort()
        raise AssertionError('Unexpected external request: ' + route.request.url)
    if parsed.path.startswith('/native/'):
        identity = parsed.path.rsplit('/', 1)[1]
        row = await route.request.frame.page.evaluate(
            'id=>window.readingRefinementsFixture.library.find(article=>article.id===id)', identity)
        body = '''<!doctype html><html><head><meta charset="utf-8"><style>
          body{font:16px/1.8 system-ui;max-width:760px;margin:auto;padding:36px;color:#303239}
          @media(prefers-color-scheme:dark){body{background:#191c23;color:#eff2f8}}
          </style></head><body><h1>TITLE</h1><p>CATEGORY</p><p>Saved transcript and reading position.</p>
          <script>
          const key=KEY, session='refinements_'+Date.now().toString(36)+Math.random().toString(36).slice(2);
          let state=POSITION; window.readerInstance=session;
          addEventListener('message',event=>{
            const restore=event.data.linkReadPositionRestore;
            if(restore&&restore.key===key&&restore.state){state=restore.state;window.readerRestoredState=restore.state;}
            const flush=event.data.linkReadPositionFlush;
            if(flush&&flush.key===key)parent.postMessage({linkReadPositionChanged:{version:1,key,session,state,requestId:flush.requestId}},'*');
            if(event.data.linkReadEscapeRequest)parent.postMessage({linkReadEscapeResult:{id:event.data.linkReadEscapeRequest,handled:false}},'*');
            if(event.data.linkReadCommand==='print')window.printRequested=true;
          });
          setTimeout(()=>parent.postMessage({linkReadPositionReady:{version:1,key,session}},'*'),0);
          </script></body></html>'''.replace('TITLE', html.escape(row['title'])).replace('CATEGORY', html.escape(row['category']))
        body = body.replace('KEY', json.dumps(row['readingKey'])).replace('POSITION', json.dumps(POSITION))
        await route.fulfill(content_type='text/html', body=body)
    else:
        path = SITE / (parsed.path.lstrip('/') or 'index.html')
        await route.fulfill(path=str(path), content_type={'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css'}.get(path.suffix))


async def open_app(browser, *, hold=False, missing_tool=False, missing_key=False, provider='codex', dark=False, width=1280):
    context = await browser.new_context(viewport={'width': width, 'height': 920},
                                        color_scheme='dark' if dark else 'light', reduced_motion='reduce')
    await context.route('**/*', routes)
    page = await context.new_page()
    errors, confirmations = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))

    async def confirm(dialog):
        confirmations.append(dialog.message)
        await dialog.accept()

    page.on('dialog', confirm)
    config = {'library': [article()], 'positions': {article()['readingKey']: POSITION},
              'holdDuplicates': hold, 'missingTool': missing_tool, 'missingKey': missing_key}
    await page.add_init_script('''if(window===window.top){window.__readingRefinementsConfig=CONFIG;
      localStorage.setItem('linkread-preferences',JSON.stringify(PREFS));}'''
                              .replace('CONFIG', json.dumps(config)).replace('PREFS', json.dumps({**PREFS, 'provider': provider})))
    await page.goto(BASE)
    await page.wait_for_function("window.readingRefinementsFixture?.calls.some(call=>call.command==='save_task_journal')")
    await expect(page.locator('.article-card')).to_have_count(1)
    return context, page, errors, confirmations


async def count_calls(page, command):
    return await page.evaluate('command=>window.readingRefinementsFixture.calls.filter(call=>call.command===command).length', command)


async def wait_duplicate(page, source, count=1):
    await page.wait_for_function('value=>window.readingRefinementsFixture.pendingDuplicates.filter(item=>item.source===value.source).length===value.count',
                                 arg={'source': source, 'count': count})


async def no_overflow(page):
    bad = await page.evaluate('''() => [...document.querySelectorAll('.home-form,.existing-source-preview,.article-dialog[open],.article-dialog-body')]
      .filter(element=>element.getBoundingClientRect().width>0&&element.scrollWidth>element.clientWidth+1)
      .map(element=>({className:element.className,width:element.clientWidth,scroll:element.scrollWidth}))''')
    assert not bad, bad


async def verify_saved_source(browser):
    context, page, errors, confirmations = await open_app(browser, hold=True, missing_tool=True)
    await page.get_by_role('textbox', name='视频或播客链接', exact=True).fill(SOURCE_A)
    await wait_duplicate(page, SOURCE_A)
    await page.locator('.source-readiness').get_by_role('button', name='检查来源', exact=True).click()
    await expect(page.locator('.source-readiness')).to_have_attribute('data-state', 'blocked')
    await expect(page.locator('.home-primary')).to_be_disabled()
    await page.evaluate('source=>window.readingRefinementsFixture.releaseDuplicate(source)', SOURCE_A)
    await expect(page.locator('.home-primary')).to_have_text('打开文章')
    await expect(page.locator('.home-primary')).to_be_enabled()
    await expect(page.locator('.existing-source-preview')).to_contain_text('已在书架中')
    await no_overflow(page)
    await page.screenshot(path=str(OUT / f'{ENGINE}-existing-article.png'))
    await page.evaluate('window.readingRefinementsFixture.holdDuplicates=false')
    await page.locator('.home-primary').click()
    await expect(page.locator('.desktop-reader-frame')).to_have_attribute('title', article()['title'])
    assert await count_calls(page, 'generate') == 0
    assert not confirmations
    assert not errors, errors
    RESULTS.append({'check': 'An archived source opens without AI configuration even after an explicit blocked-source result; duplicate submission does not regenerate or ask for replacement'})
    await context.close()


async def verify_lookup_races_and_tasks(browser):
    context, page, errors, confirmations = await open_app(browser, hold=True)
    field = page.get_by_role('textbox', name='视频或播客链接', exact=True)
    await field.fill(SOURCE_A)
    await wait_duplicate(page, SOURCE_A)
    await page.locator('.home-primary').click()
    await wait_duplicate(page, SOURCE_A, 2)
    await field.fill(SOURCE_B)
    await wait_duplicate(page, SOURCE_B)
    await page.evaluate('window.readingRefinementsFixture.holdDuplicates=false')
    await page.locator('.home-primary').click()
    await page.wait_for_function('window.readingRefinementsFixture.pendingGenerations.length===1')
    await expect(field).to_have_value('')
    for source in (SOURCE_A, SOURCE_A, SOURCE_B):
        await page.evaluate('source=>window.readingRefinementsFixture.releaseDuplicate(source)', source)
    await expect(page.locator('.desktop-reader')).to_have_count(0)
    await expect(page.locator('.existing-source-preview')).to_have_count(0)
    assert await count_calls(page, 'generate') == 1
    assert await page.evaluate("window.readingRefinementsFixture.calls.find(call=>call.command==='generate').args.source") == SOURCE_B
    await field.fill(SOURCE_B)
    await expect(page.locator('.home-primary')).to_have_text('查看任务')
    task_id = await page.evaluate('window.readingRefinementsFixture.pendingGenerations[0].args.requestId')
    await page.locator('.home-primary').click()
    task = page.locator('.task-center [data-task-id="' + task_id + '"]')
    await expect(task).to_be_visible()
    await expect(task).to_be_focused()
    assert await count_calls(page, 'generate') == 1
    await page.keyboard.press('Escape')
    await expect(page.locator('.task-center')).to_have_count(0)
    # Capture a stale "not archived" response, then finish the active task.
    await page.evaluate('window.readingRefinementsFixture.holdDuplicates=true')
    await page.locator('.home-primary').click()
    await wait_duplicate(page, SOURCE_B)
    finished = article('qa-new', 'Newly finished article', SOURCE_B)
    await page.evaluate('value=>{window.readingRefinementsFixture.holdDuplicates=false;window.readingRefinementsFixture.finishGeneration(value.id,value.article)}',
                        {'id': task_id, 'article': finished})
    await expect(page.locator('.home-tasks .task-card-active')).to_have_count(0)
    await page.evaluate('source=>window.readingRefinementsFixture.releaseDuplicate(source)', SOURCE_B)
    await expect(page.locator('.desktop-reader-frame')).to_have_attribute('title', finished['title'])
    assert await count_calls(page, 'generate') == 1
    assert not confirmations
    assert not errors, errors
    RESULTS.append({'check': 'Late lookup and submit responses cannot open the old input or block a new input; an existing task is focused without requeueing; completion during lookup rechecks and opens the finished archive'})
    await context.close()


async def verify_read_during_regeneration(browser):
    context, page, errors, confirmations = await open_app(browser)
    await page.get_by_role('textbox', name='视频或播客链接', exact=True).fill(SOURCE_A)
    await expect(page.locator('.home-primary')).to_have_text('打开文章')
    card = page.locator('.existing-source-preview')
    await card.get_by_role('button', name=article()['title'] + '的更多操作', exact=True).click()
    await card.get_by_role('button', name='重新整理', exact=True).click()
    await page.wait_for_function('window.readingRefinementsFixture.pendingGenerations.length===1')
    await expect(card).to_contain_text('正在更新')
    await expect(page.locator('.home-primary')).to_have_text('打开文章')
    assert len(confirmations) == 1 and '成功后才替换旧文章' in confirmations[0]
    assert await page.evaluate("window.readingRefinementsFixture.calls.find(call=>call.command==='generate').args.replace") is True
    await page.locator('.home-primary').click()
    frame = page.locator('.desktop-reader-frame')
    await expect(frame).to_have_attribute('title', article()['title'])
    await page.evaluate("window.originalReadingFrame=document.querySelector('.desktop-reader-frame')")
    await page.evaluate("""saved=>{
      const fixture = window.readingRefinementsFixture;
      fixture.finishGeneration(fixture.pendingGenerations[0].args.requestId,saved);
    }""", article())
    await expect(page.get_by_text('A new version is ready.', exact=True)).to_be_visible()
    assert await page.evaluate("window.originalReadingFrame===document.querySelector('.desktop-reader-frame')")
    await page.get_by_role('button', name='Open updated article', exact=True).click()
    await page.wait_for_function("document.querySelector('.desktop-reader-frame') && window.originalReadingFrame!==document.querySelector('.desktop-reader-frame')")
    await page.get_by_role('button', name='More actions', exact=True).click()
    await page.get_by_role('button', name='Regenerate', exact=True).click()
    await expect(page.get_by_text('Reprocessing in the background.', exact=True)).to_be_visible()
    await expect(frame).to_be_visible()
    await page.wait_for_function("window.readingRefinementsFixture.calls.filter(call=>call.command==='generate').length===2")
    assert not errors, errors
    RESULTS.append({'check': 'Regeneration in Home and Reader is explicit; the old iframe stays loaded until the reader opens the replacement; another regeneration stays in the Reader'})
    await context.close()


async def verify_setup_entry(browser):
    for provider, field in (('codex', '#codex-path'), ('claude', '#claude-auth-status'), ('api', '#api-url')):
        context, page, errors, _ = await open_app(browser, provider=provider,
                                                   missing_tool=provider == 'codex', missing_key=provider == 'claude')
        link = page.get_by_role('textbox', name='视频或播客链接')
        await link.fill(SOURCE_B)
        await page.get_by_role('button', name='配置 AI 工具', exact=True).click()
        await expect(page.locator(field)).to_be_focused()
        await page.keyboard.press('Escape')
        await expect(link).to_have_value(SOURCE_B)
        assert await count_calls(page, 'generate') == 0
        assert not errors, errors
        await context.close()
    RESULTS.append({'check': 'Missing Codex path, Claude key and API URL open focused setup with the original draft retained'})


async def open_shelf_menu(page, title):
    await page.locator('.shelf-row').get_by_role('button', name=title + '的更多操作', exact=True).click()
    bounds = await page.locator('.shelf-overflow-panel').bounding_box()
    workspace = await page.locator('.workspace').bounding_box()
    assert bounds['y'] >= workspace['y'] and bounds['y'] + bounds['height'] <= workspace['y'] + workspace['height'], bounds


async def verify_details_and_export(browser):
    context, page, errors, _ = await open_app(browser)
    await page.get_by_role('button', name='查看书架', exact=False).click()
    await open_shelf_menu(page, article()['title'])
    await page.get_by_role('button', name='编辑详情', exact=True).click()
    dialog = page.get_by_role('dialog', name='Edit details', exact=True)
    await expect(dialog.get_by_role('textbox', name='Display title', exact=True)).to_have_value(article()['title'])
    await dialog.get_by_role('textbox', name='Display title', exact=True).fill('Edited display title')
    await dialog.get_by_role('textbox', name='Category', exact=True).fill('Engineering')
    await page.evaluate('window.readingRefinementsFixture.failDetails=true')
    await dialog.get_by_role('button', name='Save changes', exact=True).click()
    await expect(dialog.get_by_role('alert')).to_contain_text('could not be saved')
    await expect(page.locator('.shelf-article-title')).to_have_text(article()['title'])
    await expect(dialog.get_by_role('textbox', name='Display title', exact=True)).to_have_value('Edited display title')
    await page.evaluate('window.readingRefinementsFixture.failDetails=false')
    await dialog.get_by_role('button', name='Save changes', exact=True).click()
    await expect(dialog).to_have_count(0)
    await expect(page.locator('.shelf-article-title')).to_have_text('Edited display title')
    search = page.get_by_role('searchbox', name='搜索书架', exact=True)
    await search.fill('Original research notes')
    await expect(page.locator('.shelf-row')).to_have_count(0)
    await search.fill('Engineering')
    await expect(page.locator('.shelf-row')).to_have_count(1)
    await search.fill('Edited display title')
    await expect(page.locator('.shelf-row')).to_have_count(1)
    await search.fill('')
    await open_shelf_menu(page, 'Edited display title')
    await page.get_by_role('button', name='导出 Markdown', exact=True).click()
    export = page.get_by_role('dialog', name='Export Markdown', exact=True)
    await expect(export).to_contain_text('Edited display title')
    await expect(export.get_by_role('radio', name='Readable article', exact=False)).to_be_checked()
    await expect(export.get_by_role('checkbox')).not_to_be_checked()
    await page.evaluate('window.readingRefinementsFixture.cancelExport=true')
    await export.get_by_role('button', name='Export Markdown', exact=True).click()
    await page.wait_for_function("window.readingRefinementsFixture.calls.some(call=>call.command==='export_article')")
    await expect(export).to_be_visible()
    first = await page.evaluate("window.readingRefinementsFixture.calls.find(call=>call.command==='export_article').args")
    assert first == {'id': 'qa-saved', 'format': 'markdown', 'options': {'content': 'readable', 'includeTimestamped': False}}, first
    await page.evaluate('window.readingRefinementsFixture.cancelExport=false;window.readingRefinementsFixture.failExport=true')
    await export.get_by_role('button', name='Export Markdown', exact=True).click()
    await expect(export.get_by_role('alert')).to_contain_text('could not be saved')
    await page.evaluate('window.readingRefinementsFixture.failExport=false')
    await export.get_by_role('radio', name='Full note', exact=False).check()
    await export.get_by_role('checkbox').check()
    await no_overflow(page)
    await page.screenshot(path=str(OUT / f'{ENGINE}-markdown-export.png'))
    await export.get_by_role('button', name='Export Markdown', exact=True).click()
    await expect(export).to_have_count(0)
    last = await page.evaluate("window.readingRefinementsFixture.calls.filter(call=>call.command==='export_article').at(-1).args")
    assert last['options'] == {'content': 'full', 'includeTimestamped': True}, last
    await page.locator('.shelf-article-link').click()
    frame = page.locator('.desktop-reader-frame')
    await expect(frame).to_have_attribute('title', 'Edited display title')
    await expect(page.frame_locator('.desktop-reader-frame').get_by_role('heading')).to_have_text('Edited display title')
    await page.wait_for_function('document.querySelector(".desktop-reader-frame")?.contentWindow.readerRestoredState')
    before = await frame.evaluate('element=>element.contentWindow.readerInstance')
    more = page.get_by_role('button', name='More actions', exact=True)
    await more.click()
    await page.get_by_role('button', name='Edit details', exact=True).click()
    await expect(dialog.get_by_role('textbox', name='Display title', exact=True)).to_have_value('Edited display title')
    await dialog.get_by_role('button', name='Close edit details', exact=True).focus()
    await page.keyboard.press('Shift+Tab')
    assert await dialog.evaluate('element=>element.contains(document.activeElement)')
    await page.keyboard.press('Escape')
    await expect(dialog).to_have_count(0)
    await expect(more).to_be_focused()
    assert await frame.evaluate('element=>element.contentWindow.readerInstance') == before
    await more.click()
    await page.get_by_role('button', name='Edit details', exact=True).click()
    resets = dialog.get_by_role('button', name='Reset to original', exact=True)
    await expect(resets.first).to_be_enabled()
    await resets.first.click()
    await resets.last.click()
    await page.screenshot(path=str(OUT / f'{ENGINE}-edit-details.png'))
    await dialog.get_by_role('button', name='Save changes', exact=True).click()
    await expect(dialog).to_have_count(0)
    await expect(page.frame_locator('.desktop-reader-frame').get_by_role('heading')).to_have_text(article()['title'])
    await page.wait_for_function('document.querySelector(".desktop-reader-frame")?.contentWindow.readerRestoredState')
    restored = await frame.evaluate('element=>element.contentWindow.readerRestoredState')
    assert restored == POSITION, restored
    saved = await page.evaluate("window.readingRefinementsFixture.calls.filter(call=>call.command==='update_article_details').at(-1).args")
    assert saved == {'id': 'qa-saved', 'title': None, 'category': None}, saved
    assert await page.evaluate('window.readingRefinementsFixture.library[0].isStarred') is True
    assert await page.evaluate('window.readingRefinementsFixture.library[0].isRead') is False
    export_trigger = page.get_by_role('button', name='Export', exact=True)
    await export_trigger.click()
    await expect(page.get_by_role('button', name='Print transcript / Save PDF', exact=True)).to_be_visible()
    await page.get_by_role('button', name='Markdown', exact=True).click()
    await expect(export).to_contain_text(article()['title'])
    await expect(export.get_by_role('radio', name='Readable article', exact=False)).to_be_checked()
    await expect(export.get_by_role('checkbox')).not_to_be_checked()
    await page.keyboard.press('Escape')
    await expect(export).to_have_count(0)
    await expect(export_trigger).to_be_focused()
    await expect(frame).to_be_visible()
    await export_trigger.click()
    await page.get_by_role('button', name='HTML', exact=True).click()
    last = await page.evaluate("window.readingRefinementsFixture.calls.filter(call=>call.command==='export_article').at(-1).args")
    assert last == {'id': 'qa-saved', 'format': 'html'}, last
    assert await count_calls(page, 'generate') == 0
    assert not errors, errors
    RESULTS.append({'check': 'Metadata save failures retain draft and old UI; successful title/category edits update search, reader and export; reset sends null; reader position and flags survive refresh; Escape consumes only the dialog and returns focus'})
    RESULTS.append({'check': 'Both Library and reader use Markdown scope options; default readable has no timestamp appendix, Full note can add it, save cancellation/error keeps options, and HTML/explicit transcript printing retain their paths'})
    await context.close()


async def verify_dark_narrow(browser):
    context, page, errors, _ = await open_app(browser, dark=True, width=740)
    await page.get_by_role('button', name='查看书架', exact=False).click()
    await open_shelf_menu(page, article()['title'])
    await page.get_by_role('button', name='编辑详情', exact=True).click()
    await expect(page.get_by_role('textbox', name='Display title', exact=True)).to_have_value(article()['title'])
    await no_overflow(page)
    await page.screenshot(path=str(OUT / f'{ENGINE}-edit-details-dark-narrow.png'))
    await page.keyboard.press('Escape')
    await open_shelf_menu(page, article()['title'])
    await page.get_by_role('button', name='导出 Markdown', exact=True).click()
    export = page.get_by_role('dialog', name='Export Markdown', exact=True)
    await export.get_by_role('radio', name='Full note', exact=False).check()
    await no_overflow(page)
    bounds = await export.bounding_box()
    assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 741, bounds
    await page.screenshot(path=str(OUT / f'{ENGINE}-markdown-export-dark-narrow.png'))
    assert not errors, errors
    RESULTS.append({'check': 'Metadata and export dialogs fit a 740px dark viewport without horizontal overflow'})
    await context.close()


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await getattr(playwright, ENGINE).launch()
        try:
            await verify_saved_source(browser)
            await verify_setup_entry(browser)
            await verify_lookup_races_and_tasks(browser)
            await verify_read_during_regeneration(browser)
            await verify_details_and_export(browser)
            await verify_dark_narrow(browser)
        except Exception:
            for context_index, context in enumerate(browser.contexts):
                for page_index, page in enumerate(context.pages):
                    stem = f'{ENGINE}-refinements-failure-{context_index}-{page_index}'
                    await page.screenshot(path=str(OUT / f'{stem}.png'))
                    (OUT / f'{stem}.txt').write_text(await page.locator('body').inner_text())
                    calls = await page.evaluate('window.readingRefinementsFixture?.calls')
                    (OUT / f'{stem}-calls.json').write_text(json.dumps(calls, ensure_ascii=False, indent=2))
            raise
        finally:
            (OUT / f'{ENGINE}-reading-refinements-metrics.json').write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2))
            await browser.close()
    print(json.dumps({'engine': ENGINE, 'passed': True, 'checks': len(RESULTS)}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
