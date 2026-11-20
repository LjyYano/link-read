"""Browser regression: one Escape closes one real reader layer, then saves before leaving.

Run after reader-escape-fixture.py and reader-escape-build.mjs.
Needs playwright and its LINKREAD_ENGINE browser (chromium or webkit).
"""
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect


REPO = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("LINKREAD_ESCAPE_OUT", REPO.parent / "reader-escape-verification")).resolve()
SITE = OUT / "site"
ENGINE = os.environ.get("LINKREAD_ENGINE", "chromium")
BASE = "http://escape.linkread.test/"
CHECKS, ERRORS = [], []

# Preserve event ordering when a browser owns native fullscreen's Escape.
# Observers never cancel events or replace the native API's returned promise.
ESCAPE_TRACE = r"""(() => {
  window.escapeEvents=[];
  window.escapeSnapshot=()=>({
    native:document.fullscreenElement?.className??null,
    css:document.querySelector('.mindmap-wrap')?.classList.contains('is-fullscreen')??false,
    pressed:document.querySelector('[data-mindmap-fullscreen]')?.getAttribute('aria-pressed')??null,
    focused:document.activeElement?.getAttribute('aria-label')||document.activeElement?.textContent?.slice(0,60)||null,
    forwarded:window.forwardedEscapes??null,
  });
  const record=(kind,extra={})=>{
    window.escapeEvents.push({kind,at:performance.now(),...window.escapeSnapshot(),...extra});
    if(window.escapeEvents.length>200)window.escapeEvents.shift();
  };
  window.escapeTraceMark=stage=>record('stage',{stage});
  for(const type of ['keydown','keyup','fullscreenchange','click']){
    addEventListener(type,event=>{
      if((type==='keydown'||type==='keyup')&&event.key!=='Escape')return;
      if(type==='click'&&!event.target?.closest?.('[data-mindmap-fullscreen]'))return;
      const detail={stamp:event.timeStamp,key:event.key,repeat:event.repeat,trusted:event.isTrusted};
      record(type,detail);
      setTimeout(()=>record(type+':after',{...detail,prevented:event.defaultPrevented}),0);
    },true);
  }
  addEventListener('message',event=>{
    if(event.data?.linkReadShortcut==='Escape')record('shortcut-message');
  });
  for(const [prototype,name]of [[Element.prototype,'requestFullscreen'],[Document.prototype,'exitFullscreen']]){
    const original=prototype[name];
    if(typeof original!=='function')continue;
    prototype[name]=function(...args){
      record(name+':call');
      try{
        const result=Reflect.apply(original,this,args);
        if(result&&typeof result.then==='function')result.then(()=>record(name+':resolved'),error=>record(name+':rejected',{error:String(error)}));
        else record(name+':returned');
        return result;
      }catch(error){record(name+':threw',{error:String(error)});throw error;}
    };
  }
})();"""


async def routes(route):
    parsed = urlparse(route.request.url)
    assert parsed.hostname == "escape.linkread.test", route.request.url
    name = "article.html" if parsed.path.startswith("/native/") else parsed.path.lstrip("/")
    path = (SITE / name).resolve()
    assert path.is_relative_to(SITE) and path.is_file(), path
    mime = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}.get(path.suffix, "application/json")
    await route.fulfill(path=str(path), content_type=mime)


async def no_back(page):
    await page.wait_for_timeout(100)
    assert await page.evaluate("escapeFixture.back") == 0


async def fresh(page):
    await page.goto(BASE + "index.html")
    await expect(page.locator("iframe")).to_be_visible()
    child = page.frames[1]
    await child.wait_for_function("!!window.linkreadReadingPosition")
    await page.wait_for_function("escapeFixture.calls.some(c=>c.command==='load_reading_position')")
    return child


async def map_ready(child):
    await child.locator('[data-tab="mindmap"]').click()
    await child.wait_for_function("window.linkreadReadingPosition.getState().mindmap!==null")


async def run():
    async with async_playwright() as playwright:
        browser = await getattr(playwright, ENGINE).launch()
        context = await browser.new_context(viewport={"width": 1280, "height": 850}, reduced_motion="reduce")
        await context.route("**/*", routes)
        page = await context.new_page()
        await page.add_init_script(ESCAPE_TRACE)
        page.on("pageerror", lambda error: ERRORS.append(str(error)))
        try:
            child = await fresh(page)
            await child.locator('#reader-search-toggle').click()
            await child.locator('#text-search').fill('Topic 12')
            await expect(child.locator('#match-count')).to_contain_text('of 1')
            await child.locator('#reader-appearance-toggle').click()
            await page.keyboard.press('Escape')
            await expect(child.locator('#reader-appearance')).not_to_be_visible()
            await expect(child.locator('#reader-appearance-toggle')).to_be_focused()
            await expect(child.locator('#reader-search')).to_be_visible()
            await no_back(page)
            await page.keyboard.press('Escape')
            await expect(child.locator('#reader-search')).not_to_be_visible()
            await expect(child.locator('#reader-search-toggle')).to_be_focused()
            await no_back(page)
            CHECKS.append('Aa closes before search, returns focus, and never returns to the library')

            await child.locator('#reader-search-toggle').click()
            await page.get_by_role('button', name='Export', exact=True).click()
            await expect(page.get_by_role('group', name='Export format')).to_be_visible()
            await page.keyboard.press('Escape')
            await expect(page.get_by_role('group', name='Export format')).not_to_be_visible()
            await expect(page.get_by_role('button', name='Export', exact=True)).to_be_focused()
            await expect(child.locator('#reader-search')).to_be_visible()
            await no_back(page)
            await page.keyboard.press('Escape')
            await expect(child.locator('#reader-search')).not_to_be_visible()
            await expect(child.locator('#reader-search-toggle')).to_be_focused()
            await no_back(page)
            CHECKS.append('native Export closes first; outer-toolbar Escape then asks the iframe to close search')

            await map_ready(child)
            await child.evaluate("document.querySelector('.mindmap-wrap').requestFullscreen=undefined")
            folded = await child.evaluate("window.linkreadReadingPosition.getState().mindmap.folded")
            await child.locator('[data-mindmap-fullscreen]').click()
            await expect(child.locator('.mindmap-wrap')).to_have_class('mindmap-wrap is-fullscreen')
            await page.keyboard.down('Escape')
            await expect(child.locator('.mindmap-wrap')).to_have_class('mindmap-wrap')
            await page.keyboard.down('Escape')
            await no_back(page)
            await page.keyboard.up('Escape')
            await expect(child.locator('[data-mindmap-fullscreen]')).to_be_focused()
            assert await child.evaluate("window.linkreadReadingPosition.getState().mindmap.folded") == folded
            await expect(child.locator('[data-tab="mindmap"]')).to_have_attribute('aria-selected', 'true')
            await page.screenshot(path=str(OUT / f'{ENGINE}-escape-after-fullscreen.png'))
            CHECKS.append('CSS fullscreen closes once; held Escape does not return; map folds and active tab remain')

            await child.locator('[data-mindmap-fullscreen]').click()
            await page.get_by_role('button', name='Library', exact=True).focus()
            await page.keyboard.press('Escape')
            await expect(child.locator('.mindmap-wrap')).to_have_class('mindmap-wrap')
            await expect(child.locator('[data-mindmap-fullscreen]')).to_be_focused()
            await no_back(page)
            CHECKS.append('outer-toolbar Escape also exits the iframe fullscreen without navigating')

            await child.locator('#reader-search-toggle').click()
            await child.locator('#text-search').evaluate("input=>input.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true,isComposing:true}))")
            await expect(child.locator('#reader-search')).to_be_visible()
            await page.get_by_role('button', name='Export', exact=True).click()
            await page.evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true,isComposing:true}))")
            await expect(page.get_by_role('group', name='Export format')).to_be_visible()
            await no_back(page)
            await page.keyboard.press('Escape')
            await page.keyboard.press('Escape')
            await expect(child.locator('#reader-search')).not_to_be_visible()
            CHECKS.append('IME Escape leaves both embedded search and the native menu alone')

            # Hold the actual postMessage handshake, then mimic a newer App
            # navigation intent (e.g. open + close Tasks) before the response.
            # Start this independent race scenario without a previous Escape
            # round trip still in flight from the layered-menu checks above.
            # Install before the preview's Window message handler. Holding a
            # message after its receiver already ran does not test a delayed
            # response, even if the later listener records the same request.
            await page.add_init_script("""(() => {
              window.heldEscape=null;
              window.holdEscape=event=>{if(event.data?.linkReadEscapeRequest){window.heldEscape=event.data.linkReadEscapeRequest;event.stopImmediatePropagation()}};
              addEventListener('message',window.holdEscape,true);
            })();""")
            child = await fresh(page)
            await page.get_by_role('button', name='Library', exact=True).focus()
            await page.keyboard.press('Escape')
            await child.wait_for_function('!!window.heldEscape')
            await no_back(page)
            await page.evaluate("escapeFixture.navigation++")
            await child.evaluate("""()=>{
              removeEventListener('message',window.holdEscape,true);
              parent.postMessage({linkReadEscapeResult:{id:window.heldEscape,handled:false}},'*');
            }""")
            await no_back(page)
            CHECKS.append('a delayed iframe response cannot override a newer navigation intent')

            await page.evaluate("escapeFixture.holdSaves=true")
            await page.get_by_role('button', name='Library', exact=True).focus()
            await page.keyboard.press('Escape')
            await page.wait_for_function('escapeFixture.pendingSaves.length>0')
            await no_back(page)
            await page.evaluate("escapeFixture.releaseSaves()")
            await page.wait_for_function('escapeFixture.back===1')
            events = await page.evaluate('escapeFixture.events')
            assert events[-1] == 'back' and 'saved' in events[:-1], events
            CHECKS.append('when no layer remains, one Escape saves the position before leaving exactly once')

            # Direct rendered page additionally exercises native fullscreen
            # where the engine supports it, and the independent SVG fallback.
            for filename in ('article.html', 'fallback.html'):
                await page.goto(BASE + filename)
                await map_ready(page)
                await page.evaluate("""()=>{window.forwardedEscapes=0;addEventListener('message',e=>{if(e.data?.linkReadShortcut==='Escape')window.forwardedEscapes++})}""")
                await page.evaluate("escapeTraceMark('before fullscreen click')")
                await page.locator('[data-mindmap-fullscreen]').click()
                # click() does not await toggleFullscreen's native promise.
                # Do not mistake the not-yet-entered state for a successful exit.
                await expect(page.locator('[data-mindmap-fullscreen]')).to_have_attribute('aria-pressed', 'true')
                await page.wait_for_function("document.fullscreenElement===document.querySelector('.mindmap-wrap')||document.querySelector('.mindmap-wrap').classList.contains('is-fullscreen')")
                native = await page.evaluate("document.fullscreenElement!==null")
                await page.evaluate("escapeTraceMark('before first Escape')")
                await page.keyboard.press('Escape')
                await page.wait_for_function("document.fullscreenElement===null&&!document.querySelector('.mindmap-wrap').classList.contains('is-fullscreen')")
                assert await page.evaluate('window.forwardedEscapes') == 0
                await expect(page.locator('[data-mindmap-fullscreen]')).to_be_focused()
                await page.evaluate("escapeTraceMark('before second Escape')")
                await page.keyboard.press('Escape')
                await page.wait_for_function('window.forwardedEscapes===1')
                await page.evaluate("escapeTraceMark('second Escape forwarded')")
                (OUT / f'{ENGINE}-{filename}-escape-events.json').write_text(json.dumps(await page.evaluate('escapeEvents'), indent=2))
                CHECKS.append(f'{filename}: {"native" if native else "CSS"} fullscreen consumes one Escape, then the next press forwards')
            assert not ERRORS, ERRORS
        except Exception:
            diagnostics = await page.evaluate("({url:location.href,state:window.escapeSnapshot?.(),events:window.escapeEvents||[]})")
            (OUT / f'{ENGINE}-escape-failure-events.json').write_text(json.dumps(diagnostics, indent=2))
            await page.screenshot(path=str(OUT / f'{ENGINE}-escape-failure.png'))
            raise
        finally:
            (OUT / f'{ENGINE}-escape-results.json').write_text(json.dumps({"engine": ENGINE, "checks": CHECKS, "errors": ERRORS}, indent=2))
            await browser.close()
    print(json.dumps({"engine": ENGINE, "status": "passed", "checks": len(CHECKS)}))


if __name__ == '__main__':
    asyncio.run(run())
