"""Exercise the real offline reader's appearance and attributed-copy controls.

Run reader-tools-fixture.py first. LINKREAD_ENGINE selects chromium or webkit;
LINKREAD_READER_TOOLS_OUT selects the fixture, JSON and screenshot directory.
Only the operating-system clipboard is replaced; all reader rendering and
interaction code runs unchanged, with network requests confined to the fixture.
"""
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect


REPO = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("LINKREAD_READER_TOOLS_OUT", REPO.parent / "reader-tools-verification")).resolve()
SITE = OUT / "site"
ENGINE = os.environ.get("LINKREAD_ENGINE", "chromium")
BASE = "https://reader-tools.linkread.test/"
PREFS = "linkread-reading-preferences-v1"
CHECKS, ERRORS = [], []

CLIPBOARD = """(() => {
  window.clipboardWrites = [];
  window.clipboardFails = false;
  Object.defineProperty(window, 'ClipboardItem', {configurable:true, writable:true,
    value:class {constructor(data) {this.data = data;}}});
  Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{
    async write(items) {
      if (window.clipboardFails) throw new Error('fixture clipboard denied');
      const data = items[0].data;
      window.clipboardWrites.push({plain:await data['text/plain'].text(), html:await data['text/html'].text()});
    },
    async writeText(plain) {
      if (window.clipboardFails) throw new Error('fixture clipboard denied');
      window.clipboardWrites.push({plain, html:null});
    },
  }});
  document.execCommand = () => false;
})();"""


async def routes(route):
    parsed = urlparse(route.request.url)
    assert parsed.hostname == "reader-tools.linkread.test", route.request.url
    if parsed.path == "/favicon.ico":
        await route.fulfill(status=204)
        return
    path = (SITE / parsed.path.lstrip("/")).resolve()
    assert path.is_relative_to(SITE) and path.is_file(), path
    await route.fulfill(path=str(path), content_type="text/html" if path.suffix == ".html" else "application/json")


async def settled(page):
    await page.evaluate("""async () => {
      await document.fonts.ready;
      await new Promise(resolve => {let frames=5; const frame=()=>--frames?requestAnimationFrame(frame):resolve(); requestAnimationFrame(frame);});
    }""")


async def open_article(page, name="article"):
    response = await page.goto(BASE + name + ".html#transcript")
    # A hash-only navigation reuses the current document and does not rerun
    # preference initialization. These independent cases must load the reader
    # again after changing stored values, just as reopening an article does.
    if response is None:
        await page.reload()
    await page.wait_for_function("!!window.linkreadReadingPosition")
    await settled(page)


async def appearance(page):
    await page.locator("#reader-appearance-toggle").click()
    await expect(page.locator("#reader-appearance")).to_be_visible()


async def position(page, tab="transcript"):
    return await page.evaluate("tab=>{linkreadReadingPosition.capture();return linkreadReadingPosition.getState().tabs[tab]}", tab)


async def highlights(page):
    await page.locator('[data-tab="highlights"]').click()
    await settled(page)
    card = page.locator(".anki-card").first
    await card.locator("summary").click()
    await expect(card).to_have_attribute("open", "")
    return card


async def copy_source(page, card):
    count = await page.evaluate("clipboardWrites.length")
    await card.locator("[data-copy-options]").click()
    await card.locator('[data-copy-side="source"]').click()
    await page.wait_for_function("count=>clipboardWrites.length>count", arg=count)
    return await page.evaluate("clipboardWrites.at(-1)")


async def run():
    OUT.mkdir(parents=True, exist_ok=True)
    fixture = json.loads((SITE / "fixture.json").read_text())
    async with async_playwright() as playwright:
        browser = await getattr(playwright, ENGINE).launch()
        context = await browser.new_context(viewport={"width": 1480, "height": 900}, reduced_motion="reduce")
        await context.route("**/*", routes)
        await context.add_init_script(CLIPBOARD)
        page = await context.new_page()
        page.on("pageerror", lambda error: ERRORS.append(str(error)))
        try:
            await open_article(page)
            await page.evaluate("key=>localStorage.setItem(key,JSON.stringify({version:1,fontSize:18}))", PREFS)
            await page.reload()
            await settled(page)
            await expect(page.locator(".article-body")).to_have_css("font-size", "18px")
            await expect(page.locator("html")).to_have_attribute("data-reading-width", "standard")
            await expect(page.locator("html")).to_have_attribute("data-reading-spacing", "relaxed")
            original = await page.locator(".article-body").text_content()
            standard_width = await page.locator("#panel-transcript .panel-inner").evaluate("el=>el.getBoundingClientRect().width")
            await page.locator('.reading-paragraph[data-cue-start="13"]').evaluate("""p=>{
              const rect=p.getBoundingClientRect(),inset=document.querySelector('.tabs').getBoundingClientRect().height+12;
              window.scrollTo({top:scrollY+rect.top-inset+rect.height*.35,behavior:'instant'});
            }""")
            await settled(page)
            before = await position(page)
            assert before["anchor"]["cue"] == 13 and before["ratio"] > .3, before
            await appearance(page)
            await page.locator('button[data-reading-width="wide"]').click()
            await settled(page)
            after_width = await position(page)
            assert after_width["anchor"] == before["anchor"] and abs(after_width["ratio"] - before["ratio"]) < .03
            await page.locator('button[data-reading-spacing="compact"]').click()
            await settled(page)
            after_spacing = await position(page)
            assert after_spacing["anchor"] == before["anchor"] and abs(after_spacing["ratio"] - before["ratio"]) < .03
            wide_width = await page.locator("#panel-transcript .panel-inner").evaluate("el=>el.getBoundingClientRect().width")
            assert wide_width > standard_width + 100, (standard_width, wide_width)
            leading = await page.locator(".reading-paragraph").first.evaluate("el=>parseFloat(getComputedStyle(el).lineHeight)/parseFloat(getComputedStyle(el).fontSize)")
            assert abs(leading - 1.7) < .01, leading
            assert await page.locator(".article-body").text_content() == original
            await page.screenshot(path=str(OUT / f"{ENGINE}-reading-appearance.png"))
            await page.keyboard.press("Escape")
            await expect(page.locator("#reader-appearance")).not_to_be_visible()
            await expect(page.locator("#reader-appearance-toggle")).to_be_focused()
            await expect(page.locator('[data-tab="transcript"]')).to_have_attribute("aria-selected", "true")
            CHECKS.append("legacy font preference upgrades; width and spacing preserve the real paragraph and within-paragraph position")

            await open_article(page, "another")
            await expect(page.locator(".article-body")).to_have_css("font-size", "18px")
            await expect(page.locator("html")).to_have_attribute("data-reading-width", "wide")
            await expect(page.locator("html")).to_have_attribute("data-reading-spacing", "compact")
            await page.locator('[data-tab="shownotes"]').click()
            await settled(page)
            await expect(page.locator(".shownotes-copy")).to_have_css("font-size", "18px")
            shownotes_leading = await page.locator(".shownotes-copy").evaluate("el=>parseFloat(getComputedStyle(el).lineHeight)/parseFloat(getComputedStyle(el).fontSize)")
            assert abs(shownotes_leading - 1.7) < .01
            await page.locator('[data-tab="mindmap"]').click()
            await settled(page)
            map_before = await page.locator(".mindmap-wrap").evaluate("el=>[el.clientWidth,el.clientHeight]")
            await appearance(page)
            await page.locator("#font-reset").click()
            await settled(page)
            assert await page.locator(".mindmap-wrap").evaluate("el=>[el.clientWidth,el.clientHeight]") == map_before
            saved = await page.evaluate("key=>JSON.parse(localStorage.getItem(key))", PREFS)
            assert saved == {"version": 1, "fontSize": None, "lineWidth": "standard", "lineSpacing": "relaxed"}, saved
            await expect(page.locator("#font-reset")).to_be_disabled()
            CHECKS.append("appearance persists across source identities, applies to Shownotes, and resets without changing the map canvas")

            for raw, size in [(json.dumps({"version": 1, "fontSize": 22, "lineWidth": "broken", "lineSpacing": []}), "22px"), ("{broken", "16px")]:
                await page.evaluate("args=>localStorage.setItem(args[0],args[1])", [PREFS, raw])
                await open_article(page, "another")
                await expect(page.locator(".article-body")).to_have_css("font-size", size)
                await expect(page.locator("html")).to_have_attribute("data-reading-width", "standard")
                await expect(page.locator("html")).to_have_attribute("data-reading-spacing", "relaxed")
            stored = await page.evaluate("key=>localStorage.getItem(key)", PREFS)
            await page.evaluate("""key=>{
              const original=Storage.prototype.setItem;
              Storage.prototype.setItem=function(name,value){if(name===key)throw new Error('fixture storage full');return original.call(this,name,value)};
            }""", PREFS)
            await appearance(page)
            await page.locator('button[data-reading-width="wide"]').click()
            await expect(page.locator("html")).to_have_attribute("data-reading-width", "wide")
            await expect(page.locator("#appearance-status")).to_have_attribute("data-error", "true")
            await expect(page.locator("#appearance-status")).to_contain_text("could not be saved")
            assert await page.evaluate("key=>localStorage.getItem(key)", PREFS) == stored
            CHECKS.append("invalid fields fall back independently; storage failure applies locally and explicitly reports that preferences were not saved")

            await open_article(page)
            assert await page.evaluate("isSecureContext")
            card = await highlights(page)
            await card.locator("[data-copy-options]").click()
            await expect(card.locator(".quote-copy-menu")).to_be_visible()
            await page.keyboard.press("Escape")
            await expect(card.locator(".quote-copy-menu")).not_to_be_visible()
            await expect(card.locator("[data-copy-options]")).to_be_focused()
            await expect(card).to_have_attribute("open", "")
            await expect(page.locator('[data-tab="highlights"]')).to_have_attribute("aria-selected", "true")
            copied = await copy_source(page, card)
            expected = f'{fixture["quote"]}\n\n{fixture["title"]} · {fixture["author"]}\nTime: {fixture["time"]}\n{fixture["source"]}'
            assert copied["plain"] == expected, copied["plain"]
            details = await page.evaluate("""html=>{
              const doc=new DOMParser().parseFromString(html,'text/html');
              return {links:[...doc.querySelectorAll('a')].map(a=>a.href),text:doc.body.textContent,
                unsafe:doc.querySelectorAll('safe,reader,script,img').length,
                bold:doc.querySelectorAll('strong,b').length,red:[...doc.querySelectorAll('.red')].some(e=>e.style.color==='rgb(212, 75, 64)')};
            }""", copied["html"])
            assert details["links"] == [fixture["source"]] and details["unsafe"] == 0, details
            assert fixture["title"] in details["text"] and fixture["author"] in details["text"]
            assert details["bold"] > 0 and details["red"], details
            count = await page.evaluate("clipboardWrites.length")
            await card.locator('[data-copy-side="front"]').click()
            await page.wait_for_function("n=>clipboardWrites.length>n", arg=count)
            assert await page.evaluate("clipboardWrites.at(-1).plain") == fixture["quote"]
            count += 1
            await card.locator('[data-copy-side="back"]').click()
            await page.wait_for_function("n=>clipboardWrites.length>n", arg=count)
            assert await page.evaluate("clipboardWrites.at(-1).plain") == fixture["context"] + "\nTime: " + fixture["time"]
            await card.locator("[data-copy-options]").click()
            await page.screenshot(path=str(OUT / f"{ENGINE}-copy-with-source.png"))
            await page.keyboard.press("Escape")
            CHECKS.append("source copy includes the edited title, author, exact cue range and trusted seek link; rich text stays escaped and legacy copy actions remain intact")

            await page.evaluate("window.ClipboardItem=undefined")
            fallback = await copy_source(page, card)
            assert fallback == {"plain": expected, "html": None}, fallback
            await page.evaluate("window.clipboardFails=true")
            count = await page.evaluate("clipboardWrites.length")
            await card.locator("[data-copy-options]").click()
            await card.locator('[data-copy-side="source"]').click()
            await expect(page.locator("#toast")).to_have_text("Could not copy. Check clipboard permissions.")
            assert await page.evaluate("clipboardWrites.length") == count
            CHECKS.append("plain-text clipboard fallback keeps attribution; clipboard denial reports failure")

            for name, title, url in [("apple", "A saved podcast episode", "https://podcasts.apple.com/cn/podcast/id1603392589?i=1000757475500"), ("local", "Local transcript", None)]:
                await open_article(page, name)
                card = await highlights(page)
                copied = await copy_source(page, card)
                expected = fixture["quote"] + f'\n\n{title}\nTime: {fixture["time"]}' + (f"\n{url}" if url else "")
                assert copied["plain"] == expected, copied["plain"]
                assert "file:" not in copied["plain"] and "/Users/" not in copied["plain"]
                assert "&t=" not in copied["plain"]
            CHECKS.append("Apple Podcasts keeps its real episode URL without invented seek parameters; local copies omit machine paths and missing authors")

            await page.set_viewport_size({"width": 560, "height": 760})
            await open_article(page)
            await page.evaluate("document.documentElement.dataset.theme='dark'")
            await appearance(page)
            await page.locator('button[data-reading-width="wide"]').click()
            await page.locator('button[data-reading-spacing="compact"]').click()
            await settled(page)
            bounds = await page.locator("#reader-appearance").bounding_box()
            assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 560 and bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 760, bounds
            assert await page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            await page.screenshot(path=str(OUT / f"{ENGINE}-narrow-dark.png"))
            await page.keyboard.press("Escape")
            card = await highlights(page)
            await card.locator("[data-copy-options]").click()
            bounds = await card.locator(".quote-copy-menu").bounding_box()
            assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 560 and bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 760, bounds
            assert await page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            CHECKS.append("appearance and copy menus fit a narrow dark viewport without horizontal overflow")
            assert not ERRORS, ERRORS
        except Exception as error:
            ERRORS.append(str(error))
            await page.screenshot(path=str(OUT / f"{ENGINE}-reader-tools-failure.png"))
            raise
        finally:
            (OUT / f"{ENGINE}-reader-tools-results.json").write_text(json.dumps({"engine": ENGINE, "checks": CHECKS, "errors": ERRORS}, indent=2))
            await browser.close()
    print(json.dumps({"engine": ENGINE, "status": "passed", "checks": len(CHECKS)}))


if __name__ == "__main__":
    asyncio.run(run())
