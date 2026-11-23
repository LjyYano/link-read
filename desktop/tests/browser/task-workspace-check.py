"""Actual App integration checks with only native IPC and reader transport mocked.

Run after task-workspace-build.mjs. Uses intercepted URLs, no HTTP server, source
network requests, AI calls or paid speech recognition. Browser installation is a
CI prerequisite; this script never changes the browser sandbox configuration.
"""
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

ROOT = Path(os.environ.get('LINKREAD_REPO', Path(__file__).resolve().parents[3])).resolve()
OUT = Path(os.environ.get('LINKREAD_TASK_WORKSPACE_OUT', ROOT.parent / 'task-workspace-verification')).resolve()
SITE = OUT / 'site'
ENGINE = os.environ.get('LINKREAD_ENGINE', 'chromium')
BASE = 'http://task-workspace.linkread.test/'
RESULTS = []
SOURCE_A = 'https://podcasts.apple.com/cn/podcast/id1552904790?i=1000801308527'
SOURCE_B = 'https://podcasts.apple.com/cn/podcast/id6788661772?i=1000775971124'

ASR = {'enabled': False, 'reuseToken': False, 'baseUrl': 'https://dashscope.aliyuncs.com/api/v1',
       'model': 'paraformer-v2', 'maxMinutes': 120, 'chunkMinutes': 5, 'ffmpegPath': ''}
PREFS = {'provider': 'codex', 'paths': {'codex': '/fixture/codex', 'claude': ''},
         'api': {'baseUrl': '', 'model': ''}, 'asr': ASR}


def article(identity, title, platform='local', **flags):
    return {'id': identity, 'title': title, 'platform': platform, 'author': 'Offline QA', 'category': 'QA',
            'hasAnalysis': True, 'sourceUrl': None, 'updatedAt': 1794974400000, 'publishedAt': None,
            'durationMs': 2460000, 'thumbnailUrl': None, 'isRead': False, 'isStarred': False,
            'readingKey': 'qa-workspace-' + identity, **flags}


ARTICLES = [article('qa-completed', '已完成的文章', isRead=True),
            article('qa-corrupt', '没有有效阅读位置的文章'),
            article('qa-resume', '计算器、火炮与登月：一场被低估的算力革命', 'apple_podcasts', isStarred=True),
            article('qa-fresh', '刚加入书架的新文章', 'youtube')]
POSITION = {'version': 1, 'updatedAt': 1794974400000, 'activeTab': 'transcript',
            'tabs': {'transcript': {'signature': 'p1-0123456789abcdef-1', 'anchor': {'kind': 'top'}, 'offset': 0, 'ratio': 0}},
            'mindmap': None}
POSITIONS = {'qa-workspace-qa-resume': POSITION, 'qa-workspace-qa-completed': POSITION,
             'qa-workspace-qa-corrupt': {'version': 999, 'activeTab': 'transcript'}}
RECENT = ['qa-deleted', 'qa-completed', 'qa-corrupt', 'qa-resume', 'qa-fresh']


def old_failure(index):
    return {'id': f'old-{index:02}', 'createdAt': 1794974400000, 'updatedAt': 1794974400000, 'status': 'error',
            'input': {'source': f'/fixture/old-{index:02}.srt', 'localFile': True, 'sourceKey': f'old:{index}', 'replaceRequested': False},
            'settings': {'provider': 'codex', 'executable': '/fixture/codex', 'baseUrl': '', 'model': '', 'asr': ASR},
            'metadata': {'title': f'旧任务 {index:02}', 'platform': 'local'},
            'progress': {'step': 1, 'percent': 20, 'message': '先前模型请求失败，已保留字幕'},
            'failure': {'message': '先前模型请求失败，已保留字幕', 'code': 'ai_unavailable', 'stage': 'analysis', 'retryable': True, 'action': 'retry'}}


async def routes(route):
    parsed = urlparse(route.request.url)
    if parsed.hostname != 'task-workspace.linkread.test':
        await route.abort()
        raise AssertionError('Unexpected external request: ' + route.request.url)
    if parsed.path.startswith('/native/'):
        identity = parsed.path.rsplit('/', 1)[1]
        key = 'qa-workspace-preview' if '/preview/' in parsed.path else 'qa-workspace-' + identity
        body = '''<!doctype html><html><head><meta charset="utf-8"><style>body{font:16px/1.8 system-ui;padding:32px 48px;max-width:800px;margin:auto;color:#303239}button{font:inherit}</style></head><body>
        <h1>Reader transport fixture</h1><p>The real App shell and reading-position bridge remain active.</p>
        <button id="frame-focus">Focus reader body</button><p>Task dialog dismissal must preserve this document and its position.</p>
        <script>
        const key=KEY,session='task_workspace_session_12345678';
        let state=POSITION;
        window.readerInstance=Math.random();
        addEventListener('message',event=>{
          const restore=event.data.linkReadPositionRestore;
          if(restore&&restore.key===key&&restore.state){state=restore.state;window.readerRestoredState=restore.state;}
          const flush=event.data.linkReadPositionFlush;
          if(flush&&flush.key===key)parent.postMessage({linkReadPositionChanged:{version:1,key,session,state,requestId:flush.requestId}},'*');
        });
        addEventListener('keydown',event=>{if(event.key==='Escape'&&!event.isComposing){event.preventDefault();parent.postMessage({linkReadEscape:true},'*');}});
        setTimeout(()=>parent.postMessage({linkReadPositionReady:{version:1,key,session}},'*'),0);
        </script></body></html>'''.replace('KEY', json.dumps(key)).replace('POSITION', json.dumps(POSITION))
        await route.fulfill(content_type='text/html', body=body)
    else:
        path = SITE / (parsed.path.lstrip('/') or 'index.html')
        await route.fulfill(path=str(path), content_type={'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css'}.get(path.suffix))


async def open_app(browser, *, old_count=0, reviewed=True, dark=False, width=1280, native_drag=False, locale='zh-CN'):
    context = await browser.new_context(viewport={'width': width, 'height': 980},
                                        color_scheme='dark' if dark else 'light', reduced_motion='reduce', locale=locale)
    await context.route('**/*', routes)
    page = await context.new_page()
    errors, confirmations = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))

    async def confirm(dialog):
        confirmations.append(dialog.message)
        await dialog.accept()

    page.on('dialog', confirm)
    config = {'library': ARTICLES, 'positions': POSITIONS, 'nativeDrag': native_drag,
              'journal': {'version': 1, 'tasks': [old_failure(index) for index in range(old_count)]}}
    seed = {'config': config, 'preferences': PREFS, 'recent': RECENT,
            'reviewed': [f'old-{index:02}' for index in range(old_count)] if reviewed else []}
    await page.add_init_script('''if(window===window.top){const seed=SEED;window.__taskWorkspaceConfig=seed.config;
        localStorage.setItem('linkread-preferences',JSON.stringify(seed.preferences));
        localStorage.setItem('linkread-recently-opened-v1',JSON.stringify(seed.recent));
        localStorage.setItem('linkread-reviewed-tasks-v1',JSON.stringify(seed.reviewed));}'''.replace('SEED', json.dumps(seed, ensure_ascii=False)))
    await page.goto(BASE)
    await page.wait_for_function("window.taskWorkspaceFixture?.calls.some(call=>call.command==='save_task_journal')")
    await expect(page.locator('.continue-reading')).to_be_visible()
    return context, page, errors, confirmations


async def check_no_overflow(page):
    overflowing = await page.evaluate('''() => [...document.querySelectorAll('.home-page,.home-form,.task-center[open],.task-center-body,.source-readiness,.continue-reading')]
      .filter(element=>element.getBoundingClientRect().width>0&&element.scrollWidth>element.clientWidth+1)
      .map(element=>({className:element.className,width:element.clientWidth,scroll:element.scrollWidth}))''')
    assert not overflowing, overflowing


async def verify_home_composer(browser, *, width, dark):
    context, page, errors, _ = await open_app(browser, width=width, dark=dark, native_drag=True)
    await page.set_viewport_size({'width': width, 'height': 640 if width > 720 else 580})
    composer = page.locator('.home-dropzone')
    field = composer.get_by_role('textbox', name='视频或播客链接')
    upload = composer.get_by_role('button', name='导入字幕')
    help_button = composer.get_by_role('button', name='查看支持的来源')
    tooltip = composer.locator('.home-import-tooltip')
    help_note = composer.get_by_role('note')
    await expect(field).to_have_attribute('placeholder', '粘贴视频或播客链接')
    await expect(upload).to_have_text('')
    await expect(tooltip).to_have_count(0)
    await expect(composer.get_by_role('button', name='生成文章')).to_be_disabled()
    assert await field.evaluate("field => getComputedStyle(field, '::placeholder').fontWeight") == '400'
    assert await field.evaluate("field => getComputedStyle(field, '::placeholder').color !== getComputedStyle(field).color")
    await page.screenshot(path=str(OUT / f'{ENGINE}-composer-{width}-idle.png'))

    idle_border = await composer.evaluate("element => getComputedStyle(element).borderTopColor")
    await field.focus()
    assert await composer.evaluate("element => getComputedStyle(element).borderTopColor") != idle_border
    await upload.hover()
    await expect(tooltip).to_have_text('导入字幕')
    await tooltip.hover()
    await expect(tooltip).to_be_visible()
    await page.screenshot(path=str(OUT / f'{ENGINE}-composer-{width}-hover.png'))
    await page.mouse.move(4, 45)
    await expect(tooltip).to_have_count(0)
    await upload.focus()
    await expect(tooltip).to_be_visible()
    await page.keyboard.press('Escape')
    await expect(tooltip).to_have_count(0)
    await expect(upload).to_be_focused()
    await help_button.press('Enter')
    await upload.hover()
    await expect(help_note).to_contain_text('SRT、VTT、JSON')
    await expect(tooltip).to_have_count(0)
    await page.keyboard.press('Escape')
    await expect(help_note).to_have_count(0)

    await upload.click()
    selected = composer.get_by_role('textbox', name='选中的字幕文件')
    await expect(selected).to_have_value('imported-subtitles.srt')
    await expect(tooltip).to_have_count(0)
    await composer.get_by_role('button', name='移除字幕文件').click()
    await expect(field).to_be_focused()
    draft = 'https://www.youtube.com/watch?v=abcdefghij1'
    await field.fill(draft)
    await page.evaluate("window.taskWorkspaceFixture.emitFileDrag('enter', ['/fixture/dropped.vtt'])")
    await expect(composer.get_by_role('status')).to_have_text('松开以导入字幕文件')
    await expect(field).to_have_value(draft)
    await page.evaluate("window.taskWorkspaceFixture.emitFileDrag('leave')")
    await expect(composer.get_by_role('status')).to_have_count(0)
    await expect(field).to_have_value(draft)
    await page.evaluate("window.taskWorkspaceFixture.emitFileDrag('drop', ['/fixture/dropped.vtt'])")
    await expect(selected).to_have_value('dropped.vtt')
    await expect(composer.get_by_role('button', name='生成文章')).to_be_enabled()
    await check_no_overflow(page)
    assert not errors, errors
    RESULTS.append({'check': f'Icon-only import, tooltip hover/focus/dismissal, help, file picker and drag feedback preserve interaction at width={width}, dark={dark}'})
    await context.close()


async def verify_pasted_subtitles(browser):
    context, page, errors, _ = await open_app(browser, native_drag=True)
    composer = page.locator('.home-dropzone')
    field = composer.get_by_role('textbox', name='视频或播客链接')
    selected = composer.get_by_role('textbox', name='选中的字幕文件')
    srt = "1\n00:00:01,000 --> 00:00:02,000\nRead the original cue.\n"
    url = 'https://www.youtube.com/watch?v=abcdefghij1'

    async def paste_text(locator, text):
        await locator.evaluate('''(input, text) => {
          const clipboardData = new DataTransfer();
          clipboardData.setData('text/plain', text);
          input.dispatchEvent(new ClipboardEvent('paste', {clipboardData, bubbles:true, cancelable:true}));
        }''', text)

    await page.evaluate("""window.taskWorkspaceFixture.clipboard = {
      paths: ['/fixture/Finder subtitle.srt'], text: 'Finder subtitle.srt'
    }""")
    await field.press('Meta+v')
    await expect(selected).to_have_value('Finder subtitle.srt')
    await paste_text(selected, 'file:///fixture/Other%20subtitle.vtt')
    await expect(selected).to_have_value('Other subtitle.vtt')
    await paste_text(selected, srt)
    await expect(selected).to_have_value('粘贴的字幕.srt')
    saved = await page.evaluate("window.taskWorkspaceFixture.calls.find(call => call.command === 'save_pasted_subtitle').args")
    assert saved == {'name': '粘贴的字幕.srt', 'text': srt}, saved

    await paste_text(selected, url)
    await expect(field).to_have_value(url)
    await page.evaluate("window.taskWorkspaceFixture.holdPaste = true")
    await paste_text(field, srt)
    await page.wait_for_function("window.taskWorkspaceFixture.pendingPastes.length === 1")
    await expect(composer.get_by_role('button', name='生成文章')).to_be_disabled()
    updated = 'https://www.youtube.com/watch?v=abcdefghij2'
    await field.fill(updated)
    await page.evaluate("window.taskWorkspaceFixture.pendingPastes.shift()(); window.taskWorkspaceFixture.holdPaste = false")
    await expect(field).to_have_value(updated)

    await field.evaluate('''(input, text) => {
      const clipboardData = new DataTransfer();
      clipboardData.items.add(new File([text], 'Copied file.srt', {type:'application/x-subrip'}));
      input.dispatchEvent(new ClipboardEvent('paste', {clipboardData, bubbles:true, cancelable:true}));
    }''', srt)
    await expect(selected).to_have_value('Copied file.srt')
    await composer.get_by_role('button', name='生成文章').click()
    await page.wait_for_function("window.taskWorkspaceFixture.pendingGenerations.length === 1")
    args = await page.evaluate('window.taskWorkspaceFixture.pendingGenerations[0].args')
    assert args['localFile'] is True and args['source'] == '/fixture/pasted/Copied file.srt', args
    assert await page.evaluate("window.taskWorkspaceFixture.calls.every(call => call.command !== 'pick_subtitle')")
    assert not errors, errors
    RESULTS.append({'check': 'Finder, file URLs, timed text and web clipboard files import directly; normal links and newer drafts remain editable'})
    await context.close()


async def queue_video(page, index):
    source = 'https://www.youtube.com/watch?v=abcdefghij' + str(index)
    await page.get_by_role('textbox', name='视频或播客链接', exact=True).fill(source)
    await expect(page.locator('.source-preview')).to_contain_text(f'Processing video ij{index}')
    await page.locator('.home-primary').click()
    await expect(page.get_by_role('textbox', name='视频或播客链接', exact=True)).to_have_value('')


async def verify_tasks(browser):
    context, page, errors, confirmations = await open_app(browser, old_count=20)
    for index in (1, 2, 3):
        await queue_video(page, index)
    await expect(page.locator('.home-tasks .task-card')).to_have_count(1)
    await expect(page.locator('.home-task-summary')).to_contain_text('2 个任务排队中')
    await expect(page.locator('.home-task-summary')).to_contain_text('20 条已保留记录')
    assert await page.evaluate("window.taskWorkspaceFixture.calls.filter(call=>call.command==='generate').length") == 1
    await check_no_overflow(page)
    await page.screenshot(path=str(OUT / f'{ENGINE}-workspace-home.png'))
    await page.locator('.titlebar-tasks').click()
    dialog = page.locator('.task-center[open]')
    await expect(dialog).to_be_visible()
    await expect(dialog.locator('.task-center-queue > li')).to_have_count(2)
    await expect(dialog.locator('.task-center-disclosure')).to_have_attribute('aria-expanded', 'false')
    await expect(dialog.locator('.task-card-active')).to_have_count(1)
    await page.screenshot(path=str(OUT / f'{ENGINE}-workspace-tasks.png'))
    await dialog.locator('.task-center-queue > li').first.get_by_role('button', name='取消排队', exact=True).click()
    await expect(dialog.locator('.task-center-queue > li')).to_have_count(1)
    assert await page.evaluate("window.taskWorkspaceFixture.calls.filter(call=>call.command==='cancel_generation').length") == 0
    first_id = await page.evaluate('window.taskWorkspaceFixture.pendingGenerations[0].args.requestId')
    await page.evaluate('''id=>window.taskWorkspaceFixture.failGeneration(id,{message:'QA new failure: saved transcript remains available',code:'ai_unavailable',stage:'analysis',retryable:true,action:'retry'})''', first_id)
    await expect(dialog.locator('.task-center-queue > li')).to_have_count(0)
    await expect(dialog.locator('.task-center-disclosure')).to_have_attribute('aria-expanded', 'true')
    await expect(dialog.locator('.task-center-unseen')).to_contain_text('1 条未查看')
    assert first_id not in await page.evaluate("JSON.parse(localStorage.getItem('linkread-reviewed-tasks-v1'))")
    # The new failure is below the existing records: opening the drawer alone
    # must not acknowledge or clear it without it entering the scroll viewport.
    await page.evaluate('window.taskWorkspaceFixture.failJournal=true')
    before = await dialog.locator('.task-card').count()
    await dialog.get_by_role('button', name='清理已查看记录', exact=False).click()
    await expect(dialog).to_contain_text('清理未完成，任务记录已保留')
    await expect(dialog.locator('.task-card')).to_have_count(before)
    assert await page.evaluate('window.taskWorkspaceFixture.journal.tasks.some(task=>task.id==="old-00")')
    assert confirmations and '处理缓存不会删除' in confirmations[-1]
    await page.evaluate('window.taskWorkspaceFixture.failJournal=false')
    await dialog.get_by_role('button', name='清理已查看记录', exact=False).click()
    await page.wait_for_function("!window.taskWorkspaceFixture.journal.tasks.some(task=>task.id.startsWith('old-'))")
    assert await page.evaluate('id=>window.taskWorkspaceFixture.journal.tasks.some(task=>task.id===id)', first_id)
    assert await page.evaluate("window.taskWorkspaceFixture.journal.tasks.some(task=>task.status==='running')")
    await check_no_overflow(page)
    RESULTS.append({'check': 'One running plus two queued and 20 old failures shows one full Home card; queued cancellation is local; new unseen failures remain visible; clearing preserves records on journal write failure and removes only reviewed terminal records after successful persistence'})

    await page.keyboard.press('Escape')
    await expect(page.locator('.task-center')).to_have_count(0)
    await page.locator('.continue-reading-link').click()
    frame = page.locator('.desktop-reader-frame')
    await expect(frame).to_be_visible()
    await page.wait_for_function("document.querySelector('.desktop-reader-frame')?.contentWindow.readerRestoredState?.activeTab==='transcript'")
    await page.evaluate('window.workspaceOriginalFrame=document.querySelector(".desktop-reader-frame");window.workspaceReaderInstance=window.workspaceOriginalFrame.contentWindow.readerInstance')
    prepare_count = await page.evaluate("window.taskWorkspaceFixture.calls.filter(call=>call.command==='prepare_article').length")
    await page.locator('.titlebar-tasks').click()
    await expect(page.locator('.task-center[open]')).to_be_visible()
    await page.keyboard.press('Escape')
    await expect(page.locator('.task-center')).to_have_count(0)
    assert await page.evaluate('document.querySelector(".desktop-reader-frame")===window.workspaceOriginalFrame')
    assert await page.evaluate('document.querySelector(".desktop-reader-frame").contentWindow.readerInstance===window.workspaceReaderInstance')
    assert await page.evaluate("window.taskWorkspaceFixture.calls.filter(call=>call.command==='prepare_article').length") == prepare_count
    assert await page.locator('.titlebar-tasks').evaluate('element=>element===document.activeElement')
    RESULTS.append({'check': 'Tasks is accessible while reading; one Escape dismisses only the dialog, restores trigger focus and preserves the iframe document, source, session and reading state'})
    assert not errors, errors
    await context.close()


async def verify_continue(browser):
    context, page, errors, _ = await open_app(browser)
    resume = page.locator('.continue-reading')
    await expect(resume.locator('.continue-reading-copy strong')).to_have_text(ARTICLES[2]['title'])
    await expect(resume.locator('.continue-reading-copy > span')).to_have_count(0)
    loaded = await page.evaluate("window.taskWorkspaceFixture.calls.filter(call=>call.command==='load_reading_position').map(call=>call.args.key)")
    assert loaded == ['qa-workspace-qa-corrupt', 'qa-workspace-qa-resume'], loaded
    assert await page.evaluate("window.taskWorkspaceFixture.calls.filter(call=>call.command==='prepare_article').length") == 0
    await expect(page.locator('.article-card[data-article-id="qa-resume"]')).to_have_count(1)
    await page.get_by_role('button', name='查看书架', exact=True).click()
    await page.get_by_role('button', name='未读 3', exact=True).click()
    row = page.locator('.shelf-row[data-article-id="qa-resume"]')
    more = row.get_by_role('button', name=ARTICLES[2]['title'] + '的更多操作', exact=True)
    await expect(row.get_by_role('button')).to_have_count(2)
    await more.press('Enter')
    await row.get_by_role('button', name='标为已读', exact=True).press('Enter')
    await expect(row).to_have_count(0)
    await expect(page.locator('.shelf-row[data-article-id="qa-fresh"] .shelf-article-link')).to_be_focused()
    await page.get_by_role('button', name='已读 2', exact=True).click()
    await expect(row.get_by_role('img', name='已读', exact=True)).to_be_visible()
    await page.get_by_role('button', name='新建文章', exact=True).click()
    await expect(page.locator('.home-page')).to_be_visible()
    await expect(resume).to_have_count(0)
    assert await page.evaluate("window.taskWorkspaceFixture.library.find(article=>article.id==='qa-resume').isRead")
    assert not errors, errors
    await context.close()

    context, page, errors, confirmations = await open_app(browser)
    await expect(page.locator('.article-card[data-article-id="qa-resume"]')).to_have_count(1)
    await page.get_by_role('button', name='查看书架', exact=True).click()
    row = page.locator('.shelf-row[data-article-id="qa-resume"]')
    await row.get_by_role('button', name=ARTICLES[2]['title'] + '的更多操作', exact=True).click()
    await row.get_by_role('button', name='删除文章', exact=True).click()
    await expect(row).to_have_count(0)
    await page.get_by_role('button', name='新建文章', exact=True).click()
    await expect(page.locator('.home-page')).to_be_visible()
    await expect(page.locator('.continue-reading')).to_have_count(0)
    assert not await page.evaluate("window.taskWorkspaceFixture.library.some(article=>article.id==='qa-resume')")
    assert confirmations and '确认永久删除' in confirmations[-1]
    assert await page.evaluate("window.taskWorkspaceFixture.calls.filter(call=>call.command==='generate').length") == 0
    RESULTS.append({'check': 'Continue uses current unread membership, exact reading keys and valid saved positions; recent cards include the continued article once; completing or deleting it through the bookshelf menu removes the continuation without generation or reader preparation'})
    assert not errors, errors
    await context.close()


async def verify_unseen_visibility(browser):
    context, page, errors, _ = await open_app(browser, old_count=20, reviewed=False)
    await expect(page.locator('.home-task-summary')).to_contain_text('20 个任务需要查看')
    await page.locator('.titlebar-tasks').click()
    await expect(page.locator('.task-center-disclosure')).to_have_attribute('aria-expanded', 'true')
    await page.wait_for_function("JSON.parse(localStorage.getItem('linkread-reviewed-tasks-v1')).includes('old-00')")
    assert 'old-19' not in await page.evaluate("JSON.parse(localStorage.getItem('linkread-reviewed-tasks-v1'))")
    # Expose only the final card's first 24px (its cover/title), keeping the
    # actual error reason below the viewport: the record must remain unseen.
    await page.evaluate('''() => {
      const area=document.querySelector('.task-center-body');
      const card=document.querySelector('.task-center-attention .task-card:last-child');
      area.scrollTop+=card.getBoundingClientRect().top-area.getBoundingClientRect().bottom+24;
    }''')
    await page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    assert 'old-19' not in await page.evaluate("JSON.parse(localStorage.getItem('linkread-reviewed-tasks-v1'))")
    await page.locator('.task-center-attention .task-card').last.locator('.task-error-summary').scroll_into_view_if_needed()
    await page.wait_for_function("JSON.parse(localStorage.getItem('linkread-reviewed-tasks-v1')).includes('old-19')")
    # Native modal focus remains inside the drawer while tabbing backward.
    await page.get_by_role('button', name='关闭任务中心', exact=True).focus()
    await page.keyboard.press('Shift+Tab')
    assert await page.locator('.task-center').evaluate('element=>element.contains(document.activeElement)')
    RESULTS.append({'check': 'Only a substantially visible error explanation acknowledges a task; title/cover slivers and off-screen failures remain unread; the native modal keeps keyboard focus inside'})
    assert not errors, errors
    await context.close()


async def verify_source_checks(browser, *, dark=False, width=1280):
    context, page, errors, _ = await open_app(browser, dark=dark, width=width)
    field = page.get_by_role('textbox', name='视频或播客链接', exact=True)
    panel = page.locator('.source-readiness')
    await field.fill(SOURCE_A)
    await expect(page.locator('.source-preview')).to_contain_text('商业就是这样')
    await expect(panel).to_have_attribute('data-state', 'idle')
    assert await page.evaluate("window.taskWorkspaceFixture.calls.filter(call=>['check_source','generate'].includes(call.command)).length") == 0
    await panel.get_by_role('button', name='检查来源', exact=True).click()
    await page.wait_for_function('window.taskWorkspaceFixture.pendingSourceChecks.length===1')
    await field.fill(SOURCE_B)
    await expect(panel).to_have_attribute('data-state', 'idle')
    await expect(page.locator('.source-preview')).to_contain_text('Human Stories')
    await panel.get_by_role('button', name='检查来源', exact=True).click()
    await page.wait_for_function('window.taskWorkspaceFixture.pendingSourceChecks.length===2')
    blocked = {'source': SOURCE_B, 'status': 'blocked', 'code': 'protected_media',
               'message': '该单集使用受保护音源，请打开来源收听，或导入你已有的字幕。', 'checkedAt': 1794974400000}
    await page.evaluate('result=>window.taskWorkspaceFixture.releaseSource(result.source,result)', blocked)
    await expect(panel).to_have_attribute('data-state', 'blocked')
    await page.evaluate('result=>window.taskWorkspaceFixture.releaseSource(result.source,result)',
                        {'source': SOURCE_A, 'status': 'captions', 'code': 'captions_available', 'message': 'STALE SOURCE A', 'checkedAt': 1794974400000})
    await expect(panel).to_have_attribute('data-state', 'blocked')
    assert 'STALE SOURCE A' not in await panel.inner_text()
    await expect(page.locator('.home-primary')).to_be_disabled()
    await field.press('Enter')
    await page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    assert await page.evaluate("window.taskWorkspaceFixture.calls.filter(call=>call.command==='generate').length") == 0
    await panel.get_by_role('button', name='查看来源', exact=True).click()
    assert await page.evaluate("window.taskWorkspaceFixture.calls.filter(call=>call.command==='open_video').at(-1).args.source") == SOURCE_B
    await check_no_overflow(page)
    await page.screenshot(path=str(OUT / f'{ENGINE}-source-blocked{"-dark-narrow" if dark else ""}.png'))

    # Rechecking a known blocked source must not briefly re-enable submission
    # before the replacement result has been received.
    await panel.get_by_role('button', name='重新检查', exact=True).click()
    await page.wait_for_function('window.taskWorkspaceFixture.pendingSourceChecks.length===1')
    await expect(panel).to_have_attribute('data-state', 'checking')
    await expect(page.locator('.home-primary')).to_be_disabled()
    await page.evaluate('result=>window.taskWorkspaceFixture.releaseSource(result.source,result)', blocked)
    await expect(panel).to_have_attribute('data-state', 'blocked')

    # The alternate file import remains available even while the link is blocked.
    await panel.get_by_role('button', name='导入字幕', exact=True).click()
    await expect(page.get_by_role('textbox', name='选中的字幕文件', exact=True)).to_have_value('imported-subtitles.srt')
    await expect(panel).to_have_count(0)
    await expect(page.locator('.home-primary')).to_be_enabled()
    await page.locator('.home-primary').click()
    await page.wait_for_function("window.taskWorkspaceFixture.calls.some(call=>call.command==='generate'&&call.args.localFile===true)")
    assert await page.evaluate("window.taskWorkspaceFixture.calls.filter(call=>call.command==='check_source').length") == 3
    if dark:
        await page.locator('.titlebar-tasks').click()
        await expect(page.locator('.task-center[open]')).to_be_visible()
        await check_no_overflow(page)
        bounds = await page.locator('.task-center').bounding_box()
        assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= width + 1, bounds
        await page.screenshot(path=str(OUT / f'{ENGINE}-tasks-dark-narrow.png'))
    RESULTS.append({'check': f'Source availability check is explicit; late source A cannot override blocked source B; Enter cannot bypass blocked submission; open-source and local-subtitle import remain available; viewport={width}, dark={dark}'})
    assert not errors, errors
    await context.close()


async def verify_language_preferences(browser):
    context, page, errors, _ = await open_app(browser, locale='fr-FR')
    await expect(page.locator('html')).to_have_attribute('lang', 'en')
    await page.get_by_role('button', name='Settings', exact=True).click()
    await page.get_by_label('Generated page language').select_option('zh-CN')
    await page.get_by_label('Interface language').select_option('zh-CN')
    await expect(page.get_by_role('heading', name='语言', exact=True)).to_be_visible()
    await page.reload()
    await expect(page.locator('html')).to_have_attribute('lang', 'zh-CN')
    await page.get_by_role('button', name='设置', exact=True).click()
    await expect(page.get_by_label('生成网页语言')).to_have_value('zh-CN')
    await page.get_by_label('界面语言').select_option('en')
    await expect(page.locator('html')).to_have_attribute('lang', 'en')
    await page.get_by_role('button', name='Back', exact=True).click()
    await page.get_by_role('button', name='Import subtitles', exact=True).click()
    await page.get_by_role('button', name='Generate article', exact=True).click()
    await page.wait_for_function("window.taskWorkspaceFixture.pendingGenerations.length === 1")
    targets = await page.evaluate("""() => {
      const fixture = window.taskWorkspaceFixture, args = fixture.pendingGenerations[0].args;
      return [args.uiLanguage, args.pageLanguage, fixture.journal.tasks[0].settings.pageLanguage];
    }""")
    assert targets == ['en', 'zh-CN', 'zh-CN'], targets
    assert not errors, errors
    RESULTS.append({'check': 'Non-Chinese system defaults to English; both settings survive restart and the queue passes English logs with Chinese page output'})
    await context.close()


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await getattr(playwright, ENGINE).launch()
        try:
            await verify_home_composer(browser, width=1060, dark=False)
            await verify_home_composer(browser, width=720, dark=True)
            await verify_pasted_subtitles(browser)
            await verify_tasks(browser)
            await verify_unseen_visibility(browser)
            await verify_continue(browser)
            await verify_source_checks(browser)
            await verify_source_checks(browser, dark=True, width=740)
            await verify_language_preferences(browser)
        except Exception:
            for context_index, context in enumerate(browser.contexts):
                for page_index, page in enumerate(context.pages):
                    stem = f'{ENGINE}-failure-{context_index}-{page_index}'
                    await page.screenshot(path=str(OUT / f'{stem}.png'))
                    (OUT / f'{stem}.txt').write_text(await page.locator('body').inner_text())
                    calls = await page.evaluate('window.taskWorkspaceFixture?.calls')
                    (OUT / f'{stem}-calls.json').write_text(json.dumps(calls, ensure_ascii=False, indent=2))
            raise
        finally:
            (OUT / f'{ENGINE}-task-workspace-metrics.json').write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2))
            await browser.close()
    print(json.dumps({'engine': ENGINE, 'passed': True, 'checks': len(RESULTS)}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
