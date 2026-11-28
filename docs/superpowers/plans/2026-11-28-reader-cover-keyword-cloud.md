# 阅读页封面与紧凑词云 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 阅读页标题区显示封面，关键词页改为紧凑铺满、大小对比更强的横排词云。

**Architecture:** 封面由 `covers.py` 统一解析：归档里有本地封面就内嵌为 `data:` 图片，否则用白名单内的在线网址。`render_html(cover_url=...)` 渲染封面，并在渲染时再校验一次；原生阅读页的图片策略只增加三个封面域名。词云排布抽成不依赖 DOM 的 `keyword-layout.js`，随页面内联，由 `reader.js` 测量文字后调用。

**Tech Stack:** Python（pytest）、原生 HTML/CSS/JS（不新增第三方库）、Rust/Tauri（cargo test）、Node（node:test）。

**Spec:** `docs/superpowers/specs/2026-11-28-reader-cover-keyword-cloud-design.md`

## Global Constraints

- 阅读页保持单文件、离线可用：不引用 CDN 脚本或字体，不新增第三方库。
- 封面只接受两种值：`data:image/(jpeg|png|gif|webp);base64,<合法 base64>`，或 `thumbnail_url()` 原样返回的 HTTPS 网址。
- 封面域名白名单：`i.ytimg.com`、`*.hdslb.com`、`*.mzstatic.com`。图片策略的 `img-src` 为 `data: https://i.ytimg.com https://*.hdslb.com https://*.mzstatic.com`，其余指令不变。
- 本地封面内嵌上限：`MAX_EMBEDDED_COVER_BYTES = 1024 * 1024`。
- 打开文章和重建阅读缓存不联网，只用已保存的内容。
- 关键词的顺序、文字和「可点击、可用键盘操作的搜索按钮」不变，全部横排。
- 词云超过 52 个词或放不下时，回到现有的流式排布。
- 铺满：词云宽度不到卡片宽度的 85% 时整体放大重排，上限 1.35 倍，最多 3 轮测量。
- `reader_cache.CACHE_VERSION` 从 12 改为 13。
- 本计划没有提交步骤：改动保持未提交，等用户要求时再提交（提交信息带 `[skip ci]`）。不触发 GitHub Actions。
- 测试命令（仓库根目录）：
  - `.venv/bin/python -m pytest -q`
  - `cd desktop && npm run build && node --test tests/*.test.mjs`
  - `cd desktop/src-tauri && cargo test`

## Review Focus

规格隐含、但没有任务的单元测试覆盖、最可能影响使用者的五种情况。每一条都钉在拥有对应代码的任务里。

1. **内嵌封面损坏，或在线封面离线加载失败** → 标题区回到无封面的排版，不留空框或破图。钉在 Task 2 的无头浏览器检查。
2. **归档里的本地封面超过 1 MiB** → 改用在线网址，不放弃封面。钉在 Task 1 的 `test_embedded_cover_skips_oversized_missing_and_symlinked_covers`。
3. **本地字幕文章关联或取消关联来源** → 封面随关联来源出现或消失。钉在 Task 3 的 `test_local_subtitle_reader_follows_the_linked_source_cover`。
4. **旧的 YouTube 归档没有保存封面网址** → 用视频 ID 生成稳定的在线封面。钉在 Task 3 的 `test_prepared_reader_uses_saved_thumbnail_urls_and_never_the_network`。
5. **白名单与图片策略脱节** → Python、前端、Rust 共用同一份用例。钉在 Task 4。

另有一条在 Task 6 用浏览器检查钉住：关键词超过 52 个时回到流式排布。

---

### Task 1: 封面来源解析

**Files:**
- Modify: `src/linkread_desktop/covers.py`
- Test: `tests/test_local_cover.py`

**Interfaces:**
- Produces（Task 2、Task 3 使用）：

```python
MAX_EMBEDDED_COVER_BYTES: int            # 1024 * 1024
def embedded_cover(folder: Path) -> str | None
def cover_source(folder: Path | None, thumbnail: str | None) -> str | None
def valid_cover_source(value: object) -> str | None
def display_thumbnail(source: SourceInfo) -> str | None
```

- [ ] **Step 1: 写失败的测试**（追加到 `tests/test_local_cover.py`，文件顶部补 `import base64` 和 `GIF_BYTES = b"GIF89a" + b"\x00" * 32`）

```python
@pytest.mark.parametrize("payload,ext,mime", [
    (JPEG_BYTES, ".jpg", "image/jpeg"), (PNG_BYTES, ".png", "image/png"),
    (WEBP_BYTES, ".webp", "image/webp"), (GIF_BYTES, ".gif", "image/gif"),
])
def test_embedded_cover_inlines_a_small_local_cover_as_a_data_uri(tmp_path, payload, ext, mime):
    covers.save_cover_bytes(tmp_path, ext, payload)
    assert covers.embedded_cover(tmp_path) == f"data:{mime};base64," + base64.b64encode(payload).decode("ascii")


def test_embedded_cover_skips_oversized_missing_and_symlinked_covers(tmp_path, monkeypatch):
    assert covers.embedded_cover(tmp_path) is None
    covers.save_cover_bytes(tmp_path, ".jpg", JPEG_BYTES)
    monkeypatch.setattr(covers, "MAX_EMBEDDED_COVER_BYTES", 16)
    assert covers.embedded_cover(tmp_path) is None
    monkeypatch.undo()
    article = tmp_path / "article"
    article.mkdir()
    (article / "cover.jpg").symlink_to(tmp_path / "cover.jpg")
    assert covers.embedded_cover(article) is None


def test_cover_source_prefers_the_local_cover_then_the_allowlisted_thumbnail(tmp_path):
    thumb = "https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg"
    assert covers.cover_source(tmp_path, thumb) == thumb
    assert covers.cover_source(tmp_path, "https://evil.example/cover.jpg") is None
    covers.save_cover_bytes(tmp_path, ".jpg", JPEG_BYTES)
    assert covers.cover_source(tmp_path, "https://evil.example/cover.jpg") == covers.embedded_cover(tmp_path)
    assert covers.cover_source(None, thumb) == thumb


def test_valid_cover_source_accepts_only_inlined_raster_images_and_allowlisted_thumbnails():
    inline = "data:image/png;base64,iVBORw0KGgo="
    url = "https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg"
    assert covers.valid_cover_source(inline) == inline
    assert covers.valid_cover_source(url) == url
    for bad in (None, "", 7, "http://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg", "//i.ytimg.com/a.jpg",
                "data:image/svg+xml,<svg onload=alert(1)>", "data:text/html;base64,PGh0bWw+",
                'data:image/png;base64,iVBORw0KGgo=" onerror="alert(1)', "data:image/png;base64,",
                "data:image/png;base64," + "A" * (2 * 1024 * 1024)):
        assert covers.valid_cover_source(bad) is None, bad


def test_display_thumbnail_uses_a_stable_youtube_frame_then_the_saved_cover():
    youtube = SourceInfo(platform="youtube", title="t", video_id="1-Fo8McCD9w",
                         url="https://www.youtube.com/watch?v=1-Fo8McCD9w", thumbnail_url="https://i.ytimg.com/vi/1-Fo8McCD9w/maxresdefault.jpg")
    assert covers.display_thumbnail(youtube) == "https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg"
    show = SourceInfo(platform="apple_podcasts", title="t", url="https://podcasts.apple.com/podcast/id1",
                      thumbnail_url="https://is1-ssl.mzstatic.com/image/thumb/cover.jpg")
    assert covers.display_thumbnail(show) == "https://is1-ssl.mzstatic.com/image/thumb/cover.jpg"
    assert covers.display_thumbnail(SourceInfo(platform="local", title="t")) is None
```

- [ ] **Step 2: 运行，确认因缺少函数而失败**

Run: `.venv/bin/python -m pytest -q tests/test_local_cover.py -k "embedded_cover or cover_source or valid_cover_source or display_thumbnail"`
Expected: FAIL，`AttributeError: module 'linkread_desktop.covers' has no attribute ...`

- [ ] **Step 3: 在 `covers.py` 实现**

- `embedded_cover`：用 `find_local_cover` 找到文件，再用 `os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)` 打开，确认是普通文件，最多读 `MAX_EMBEDDED_COVER_BYTES + 1` 字节，超限返回 `None`。MIME 取自 `detect_image_extension` 的魔数结果，不信任扩展名。
- `cover_source`：`folder` 不为 `None` 时先试 `embedded_cover`，否则返回 `thumbnail_url(thumbnail)`。
- `valid_cover_source`：总长度超过 `2 * MAX_EMBEDDED_COVER_BYTES` 返回 `None`；`data:` 开头的必须完整匹配 `data:image/(?:jpeg|png|gif|webp);base64,[A-Za-z0-9+/]+={0,2}`；其余必须满足 `thumbnail_url(value) == value`。
- `display_thumbnail`：YouTube 且 `source_info_link(source)` 的 `v` 参数是合法视频 ID 时，返回 `thumbnail_url(f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg")`；否则返回 `thumbnail_url(source.thumbnail_url)`。

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest -q tests/test_local_cover.py`
Expected: PASS

---

### Task 2: 渲染封面与加载失败回退

**Files:**
- Modify: `src/linkread_desktop/renderers/html.py`（`render_html` 签名约第 133 行；模板取值约第 233–254 行）
- Modify: `src/linkread_desktop/renderers/reader.html`（标题区第 180 行；样式块 `linkread-reader-extensions` 第 22 行之后；打印规则第 165 行）
- Modify: `src/linkread_desktop/renderers/reader.js`（第 232 行 `[data-keyword]` 处理之后）
- Test: `tests/test_reader_cover.py`（新建）

**Interfaces:**
- Consumes: `covers.valid_cover_source(value) -> str | None`（Task 1）
- Produces（Task 3 使用）: `render_html(document, *, home_href=None, category=None, description=None, extras=None, title=None, reading_state=None, edition=None, language=None, cover_url: str | None = None) -> str`

- [ ] **Step 1: 写失败的测试**（新建 `tests/test_reader_cover.py`）

```python
"""Offline cover rendering: validation, markup and the unchanged text-only header."""
from html.parser import HTMLParser
import pytest
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.renderers.html import render_html

VIDEO_COVER = "https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg"
INLINE_COVER = "data:image/jpeg;base64,/9j/4AAQSkZJRg"


def document(platform="youtube"):
    url = "https://www.youtube.com/watch?v=1-Fo8McCD9w" if platform == "youtube" else None
    return TranscriptDocument(
        source=SourceInfo(platform=platform, title="带封面的文章", video_id="1-Fo8McCD9w" if url else None, url=url),
        transcript_source="subtitle",
        segments=[TranscriptSegment(start_ms=0, end_ms=1000, text="正文。")])


class CoverParser(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.images, self.header_class, self.header_children = [], None, []
        self._in_header = False
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "header":
            self.header_class, self._in_header = attributes.get("class"), True
        elif self._in_header:
            self.header_children.append(tag)
        if tag == "img":
            self.images.append(attributes)

    def handle_endtag(self, tag):
        if tag == "header":
            self._in_header = False


@pytest.mark.parametrize("platform,css,size", [
    ("youtube", "article-cover", ("480", "270")),
    ("apple_podcasts", "article-cover is-square", ("600", "600")),
])
def test_cover_is_rendered_as_a_non_text_image_first_in_the_header(platform, css, size):
    page = CoverParser(render_html(document(platform), cover_url=VIDEO_COVER))
    img = page.images[0]
    assert (img["class"], (img["width"], img["height"])) == (css, size)
    assert (img["src"], img["alt"], img["decoding"], img["referrerpolicy"]) == (
        VIDEO_COVER, "", "async", "no-referrer")
    assert page.header_class == "hero has-cover"
    assert page.header_children[:3] == ["img", "h1", "div"]     # 封面、标题、元信息
    assert CoverParser(render_html(document(platform), cover_url=INLINE_COVER)).images[0]["src"] == INLINE_COVER


@pytest.mark.parametrize("bad", [
    None, "", "https://evil.example/cover.jpg", "javascript:alert(1)",
    "data:image/svg+xml,<svg onload=alert(1)>", 'data:image/png;base64,AAAA" onerror="alert(1)',
    "http://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg",
])
def test_untrusted_cover_values_keep_the_plain_text_header(bad):
    markup = render_html(document(), cover_url=bad)
    page = CoverParser(markup)
    assert page.images == [] and page.header_class == "hero"
    assert "article-cover" not in markup and "alert(1)" not in markup


def test_without_a_cover_the_header_markup_is_unchanged():
    markup = render_html(document())
    assert '<header class="hero"><h1>' in markup
    assert "<img" not in markup
    assert markup == render_html(document())
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest -q tests/test_reader_cover.py`
Expected: FAIL，`TypeError: render_html() got an unexpected keyword argument 'cover_url'`

- [ ] **Step 3: 实现**

- `html.py`：新增 `_cover(platform: str, cover_url: str | None) -> str`，先过 `valid_cover_source`，无结果返回空串。有结果时输出

  ```html
  <img class="article-cover[ is-square]" src="…" alt="" width="W" height="H" decoding="async" referrerpolicy="no-referrer">
  ```

  `apple_podcasts` 用 `is-square` 和 `600×600`，其余用 `480×270`；`src` 经 `escape(..., quote=True)`。`render_html` 增加 `cover_url` 参数，向模板传 `"COVER"` 和 `"HERO_CLASS"`（有封面为 `" has-cover"`，否则为空串）。
- `reader.html`：
  - 第 180 行改为 `<header class="hero<!--LR:HERO_CLASS-->"><!--LR:COVER--><h1>…`。
  - 在 `.reading-paragraph{scroll-margin-top:94px}` 之后加入下面的样式。
  - 打印规则的隐藏列表加入 `.article-cover`。

```css
/* The cover sits beside the title; a page without one keeps its text-only header. */
.hero.has-cover{display:grid;grid-template-columns:auto minmax(0,1fr);column-gap:30px;align-items:start}
.article-cover{grid-row:1/span 3;justify-self:start;width:236px;height:133px;object-fit:cover;border:1px solid var(--line);border-radius:10px;background:var(--surface)}
.article-cover.is-square{width:168px;height:168px}
@media(max-width:1050px){.article-cover{width:204px;height:115px}.article-cover.is-square{width:148px;height:148px}}
@media(max-width:790px){.hero.has-cover{column-gap:18px}.article-cover{width:132px;height:74px}.article-cover.is-square{width:96px;height:96px}}
```

- `reader.js`：监听每个 `.article-cover` 的 `error` 事件，从最近的 `.hero` 移除 `has-cover` 并删除该图片。

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest -q tests/test_reader_cover.py tests/test_keyword_cloud.py tests/test_render_site.py`
Expected: PASS（关键词注入测试仍断言无封面时页面没有 `<img>`）

- [ ] **Step 5: 用真实浏览器检查加载失败回退**

在临时目录生成两份样张：一份用在线封面，一份没有封面。用隔离配置的无头 Chrome 打开样张并把封面域名指向本机（必然加载失败），导出渲染后的 DOM。

Run: `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --user-data-dir=<临时目录> --host-rules="MAP i.ytimg.com 127.0.0.1" --virtual-time-budget=3000 --dump-dom "file://<样张路径>"`
Expected: DOM 里是 `<header class="hero">`，不含 `has-cover`，不含 `article-cover`。
去掉 `--host-rules` 再跑一次（联网）：DOM 里是 `<header class="hero has-cover">`。

---

### Task 3: 调用方接入封面并提升缓存版本

**Files:**
- Modify: `src/linkread_desktop/desktop.py`（导入第 22 行；`_source_info_metadata` 约第 80 行；`_article_record` 约第 97–115 行；`_prepared_reader` 约第 351–369 行）
- Modify: `src/linkread_desktop/generation.py`（导入第 19 行；封面线程约第 112–119 行；最终渲染约第 227–234 行）
- Modify: `src/linkread_desktop/renderers/site.py`（约第 223 行）
- Modify: `src/linkread_desktop/reader_cache.py:18`
- Test: `tests/test_reader_cover.py`、`tests/test_local_cover.py`、`tests/test_render_site.py`

**Interfaces:**
- Consumes: `covers.cover_source(folder, thumbnail)`、`covers.display_thumbnail(source)`（Task 1）；`render_html(cover_url=...)`（Task 2）
- Produces: `desktop._linked_source(folder: Path, source: SourceInfo) -> dict | None`（仅本地字幕文章返回 `.article-details.json` 里的 `linkedSource`）

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_reader_cover.py`（复用 `tests/test_reader_cache.py` 的 `archive()` 和 `tests/test_local_cover.py` 的 `JPEG_BYTES`；`VIDEO`、`ARTWORK`、`video_preview`、`make_local_archive` 取自 `tests/test_article_details.py`。只导入这几个名字，不要把那个文件里的测试函数导进来）：

```python
def cached(article):
    return article.with_name(".reader.html").read_text(encoding="utf-8")


def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise pytest.fail("opening an article must stay offline")
    # desktop.py 按名字导入了 fetch_cover_bytes，两处都要替换才拦得住。
    monkeypatch.setattr(covers, "fetch_cover_bytes", refuse)
    monkeypatch.setattr(desktop, "fetch_cover_bytes", refuse)


def test_prepared_reader_prefers_the_local_cover_and_never_fetches(tmp_path, monkeypatch):
    no_network(monkeypatch)
    article = archive(tmp_path)
    original = article.read_bytes()
    desktop._prepared_reader(article)
    assert "data:image" not in cached(article)
    covers.save_cover_bytes(article.parent, ".jpg", JPEG_BYTES)
    desktop._prepared_reader(article)
    html = cached(article)
    assert 'src="data:image/jpeg;base64,' + base64.b64encode(JPEG_BYTES).decode("ascii") + '"' in html
    assert '<header class="hero has-cover">' in html
    assert article.read_bytes() == original


def test_prepared_reader_uses_saved_thumbnail_urls_and_never_the_network(tmp_path, monkeypatch):
    no_network(monkeypatch)
    article = archive(tmp_path)          # 旧的 YouTube 归档：没有保存封面网址
    desktop._prepared_reader(article)
    assert 'src="https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg"' in cached(article)
    assert "data:image" not in cached(article)


def test_local_subtitle_reader_follows_the_linked_source_cover(tmp_path, monkeypatch):
    library = tmp_path / "library"
    article = make_local_archive(library) / "article.html"
    monkeypatch.setattr(desktop, "video_metadata", video_preview)
    monkeypatch.setattr(desktop, "fetch_cover_bytes", lambda *a, **k: None)     # 不保存本地封面，走在线网址
    desktop._prepared_reader(article)
    assert "article-cover" not in cached(article)
    desktop._update_article_details(library, article.parent.name, {"title": None, "category": None, "sourceUrl": VIDEO})
    desktop._prepared_reader(article)
    assert f'src="{ARTWORK}"' in cached(article)
    desktop._update_article_details(library, article.parent.name, {"title": None, "category": None, "sourceUrl": ""})
    desktop._prepared_reader(article)
    assert "article-cover" not in cached(article)
```

追加到 `tests/test_local_cover.py`：

```python
def test_generation_without_a_local_cover_writes_the_online_thumbnail(tmp_path, monkeypatch):
    raw = make_transcript(text="没有本地封面的文章")
    monkeypatch.setattr("linkread_desktop.generation.extract_document", lambda *a, **k: raw)
    monkeypatch.setattr(covers, "fetch_cover_bytes", lambda url, session=None: None)
    html = (generate_article(raw.source.url, FakeProvider(), library=tmp_path / "library")).read_text(encoding="utf-8")
    assert 'src="https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg"' in html
    assert "data:image" not in html
```

并在已有的 `test_generation_caches_cover_locally_and_library_index_tracks_it` 末尾加一行：

```python
    assert "data:image/jpeg;base64," + base64.b64encode(JPEG_BYTES).decode("ascii") in (folder / "article.html").read_text(encoding="utf-8")
```

追加到 `tests/test_render_site.py`：

```python
def test_site_pages_carry_the_cover_while_the_bookshelf_stays_text(tmp_path):
    source_dir = tmp_path / "transcripts"
    (source_dir / "a").mkdir(parents=True)
    (source_dir / "a" / "transcript.json").write_text(make_doc().model_dump_json(), encoding="utf-8")
    assert build_site(source_dir, tmp_path / "site") == 1
    page = next((tmp_path / "site" / "pages").glob("*/index.html")).read_text(encoding="utf-8")
    assert 'class="article-cover" src="https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg"' in page
    assert "<img" not in (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest -q tests/test_reader_cover.py tests/test_local_cover.py tests/test_render_site.py -k "prepared_reader or linked_source_cover or online_thumbnail or caches_cover or site_pages_carry"`
Expected: FAIL（缓存和 `article.html` 里没有封面）

- [ ] **Step 3: 实现**

- `desktop.py`
  - 导入 `cover_source`、`display_thumbnail`；`_source_info_metadata` 的缩略图改用 `display_thumbnail(source)`。
  - 新增 `_linked_source(folder, source)`：非 `local` 平台返回 `None`，否则返回 `(read_article_details(folder) or {}).get("linkedSource")`。`_article_record` 改用它。
  - `_prepared_reader`：`linked = _linked_source(article.parent, raw.source)`，`thumbnail = linked["thumbnailUrl"] if linked else _source_metadata(raw)["thumbnailUrl"]`，调用 `render_html(..., cover_url=cover_source(article.parent, thumbnail))`。
- `generation.py`
  - 导入 `cover_source`、`display_thumbnail`。
  - 把 `if cover_thread is not None: cover_thread.join(timeout=12)` 移到 `render_started = time.monotonic()` 之前。
  - `render_html(...)` 增加 `cover_url=cover_source(folder, display_thumbnail(raw.source))`。
- `site.py`：导入两个函数，页面渲染增加 `cover_url=cover_source(record["path"].parent, display_thumbnail(transcript.source))`。
- `reader_cache.py`：`CACHE_VERSION = 13`。

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest -q`
Expected: 全部 PASS

---

### Task 4: 原生图片策略与共用用例

**Files:**
- Create: `tests/fixtures/cover_hosts.json`
- Modify: `desktop/src-tauri/src/main.rs`（第 1832 行的 `POLICY` 提到模块级；`mod tests` 内加测试）
- Test: `tests/test_reader_cover.py`、`desktop/tests/coverSource.test.mjs`、`desktop/src-tauri/src/main.rs`

**Interfaces:**
- Produces: `main.rs` 模块级常量 `READER_CONTENT_SECURITY_POLICY: &str`，`preview_response` 使用它。

- [ ] **Step 1: 新建共用用例 `tests/fixtures/cover_hosts.json`**

```json
{
  "csp": ["https://i.ytimg.com", "https://*.hdslb.com", "https://*.mzstatic.com"],
  "accepted": [
    "https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg",
    "https://i0.hdslb.com/bfs/archive/cover.jpg",
    "https://is1-ssl.mzstatic.com/image/thumb/cover/600x600bb.jpg"
  ],
  "rejected": [
    "https://evil.example/cover.jpg",
    "https://i.ytimg.com.evil.example/cover.jpg",
    "https://ytimg.com/cover.jpg",
    "https://hdslb.com/cover.jpg",
    "https://i.ytimg.com:8443/cover.jpg",
    "https://user@i.ytimg.com/cover.jpg",
    "data:image/svg+xml,<svg onload=alert(1)>"
  ],
  "upgraded": [
    {"input": "http://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg", "expected": "https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg"},
    {"input": "//i0.hdslb.com/bfs/archive/cover.jpg", "expected": "https://i0.hdslb.com/bfs/archive/cover.jpg"}
  ]
}
```

- [ ] **Step 2: 写失败的测试**

`tests/test_reader_cover.py`：

```python
COVER_CASES = json.loads((Path(__file__).parent / "fixtures" / "cover_hosts.json").read_text(encoding="utf-8"))


def test_cover_host_allowlist_matches_the_shared_cases():
    for url in COVER_CASES["accepted"]:
        assert covers.valid_cover_source(url) == url, url
    for url in COVER_CASES["rejected"]:
        assert covers.valid_cover_source(url) is None, url
    for case in COVER_CASES["upgraded"]:
        assert thumbnail_url(case["input"]) == case["expected"]
```

`desktop/tests/coverSource.test.mjs`：

```js
test('thumbnailSource matches the shared cover host cases', () => {
  const cases = JSON.parse(readFileSync(new URL('../../tests/fixtures/cover_hosts.json', import.meta.url), 'utf8'))
  for (const url of cases.accepted) assert.equal(thumbnailSource(url), url)
  for (const url of cases.rejected) assert.equal(thumbnailSource(url), undefined, url)
  for (const { input, expected } of cases.upgraded) assert.equal(thumbnailSource(input), expected)
})
```

`main.rs` 的 `mod tests`：

```rust
#[test]
fn reader_policy_allows_only_inlined_code_and_known_cover_images() {
    let cases: serde_json::Value =
        serde_json::from_str(include_str!("../../../tests/fixtures/cover_hosts.json")).unwrap();
    let directive = |name: &str| super::READER_CONTENT_SECURITY_POLICY
        .split(';').map(str::trim)
        .find_map(|part| part.strip_prefix(name)).map(str::trim).unwrap();
    assert_eq!(directive("default-src:"), "'none'");
    assert_eq!(directive("script-src:"), "'unsafe-inline'");
    assert_eq!(directive("style-src:"), "'unsafe-inline'");
    let mut expected = vec!["data:"];
    expected.extend(cases["csp"].as_array().unwrap().iter().map(|value| value.as_str().unwrap()));
    let actual: Vec<&str> = directive("img-src:").split_whitespace().collect();
    assert_eq!(actual, expected);
}
```

- [ ] **Step 3: 运行，确认失败**

Run: `cd desktop/src-tauri && cargo test reader_policy`
Expected: 编译失败（`READER_CONTENT_SECURITY_POLICY` 不存在）
Run: `.venv/bin/python -m pytest -q tests/test_reader_cover.py -k shared_cases` 和 `cd desktop && node --test tests/coverSource.test.mjs`
Expected: PASS（这两处只钉住现有白名单，Rust 侧才是要改的）

- [ ] **Step 4: 实现**

`main.rs`：把 `POLICY` 移到模块级并改名 `READER_CONTENT_SECURITY_POLICY`，`img-src` 改为 `data: https://i.ytimg.com https://*.hdslb.com https://*.mzstatic.com`，其余指令逐字不变；`preview_response` 使用新常量。

- [ ] **Step 5: 运行，确认通过**

Run: `cd desktop/src-tauri && cargo test`
Expected: 全部 PASS

---

### Task 5: 词云排布模块

**Files:**
- Create: `src/linkread_desktop/renderers/keyword-layout.js`
- Test: `desktop/tests/keywordLayout.test.mjs`

**Interfaces:**
- Produces（Task 6 使用）：

```js
placeKeywords(items, width, height, options) -> Array<{ x: number, y: number }> | null
// items: [{ w, h }]，单位 CSS 像素；width、height 为画布尺寸
// options: { margin = 4, gap = 4, squash = 0.52 }
// 返回与 items 等长的左上角坐标；任何一项放不下时返回 null
// 同时挂到根对象 LinkReadKeywordLayout，并在 Node 下导出 module.exports
```

- [ ] **Step 1: 写失败的测试**（新建 `desktop/tests/keywordLayout.test.mjs`）

```js
import test from "node:test"
import assert from "node:assert/strict"
import { createRequire } from "node:module"

const require = createRequire(import.meta.url)
const { placeKeywords } = require("../../src/linkread_desktop/renderers/keyword-layout.js")
const OPTIONS = { margin: 4, gap: 4 }
const cloud = (count, seed = 1) => Array.from({ length: count }, (_, index) => ({
  w: 60 + ((index * 97 + seed * 53) % 380), h: 26 + ((index * 31) % 34) }))

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
  for (const count of [1, 7, 14, 30, 52]) for (const width of [670, 480, 300]) {
    const items = cloud(count)
    const boxes = placeKeywords(items, width, 640, OPTIONS)
    assert.equal(boxes.length, count)
    assert.deepEqual(violations(boxes, items, width, 640), [], `${count} words at ${width}px`)
  }
})

test("the same keywords and width always produce the same layout", () => {
  const items = cloud(14)
  assert.deepEqual(placeKeywords(items, 670, 420, OPTIONS), placeKeywords(items, 670, 420, OPTIONS))
})

test("the first keyword sits at the centre and the cloud spreads sideways", () => {
  const items = cloud(14)
  const boxes = placeKeywords(items, 670, 420, OPTIONS)
  const centre = (index) => [boxes[index].x + items[index].w / 2, boxes[index].y + items[index].h / 2]
  assert.ok(Math.abs(centre(0)[0] - 335) <= 1 && Math.abs(centre(0)[1] - 210) <= 1)
  const left = Math.min(...boxes.map((box, index) => box.x)), right = Math.max(...boxes.map((box, index) => box.x + items[index].w))
  const top = Math.min(...boxes.map(box => box.y)), bottom = Math.max(...boxes.map((box, index) => box.y + items[index].h))
  assert.ok(right - left > (bottom - top) * 1.15, `${right - left} x ${bottom - top}`)
})

test("a keyword that cannot fit returns null instead of a partial layout", () => {
  assert.equal(placeKeywords([{ w: 664, h: 30 }], 670, 300, OPTIONS), null)
  assert.equal(placeKeywords([{ w: 100, h: 294 }], 670, 300, OPTIONS), null)
  assert.equal(placeKeywords([{ w: 100, h: 30 }, { w: 700, h: 30 }], 670, 300, OPTIONS), null)
})
```

- [ ] **Step 2: 运行，确认失败**

Run: `cd desktop && node --test tests/keywordLayout.test.mjs`
Expected: FAIL，`Cannot find module '.../keyword-layout.js'`

- [ ] **Step 3: 实现 `keyword-layout.js`**

写法沿用 `reading-position.js`：立即执行函数，根对象为 `typeof window === 'undefined' ? globalThis : window`，末尾 `if (typeof module !== 'undefined' && module.exports) module.exports = api`。

算法（签名和测试决定不了的部分）：

1. 任一项满足 `w + 2*margin > width` 或 `h + 2*margin > height`，直接返回 `null`。
2. 按给定顺序逐个放置。对第 `index` 项，从画布中心沿扁椭圆螺旋找第一个合法位置：`step` 从 0 递增，`angle = step * 0.3 + index * 2.399963`，`radius = step * 0.55`（`step` 为 0 时落在正中），`x = round(width/2 + cos(angle)*radius - w/2)`，`y = round(height/2 + sin(angle)*radius*squash - h/2)`，最多 6000 步。
3. 合法：在边距内，且与已放置的任何一项都不满足 `x < p.x + p.w + gap && x + w + gap > p.x && y < p.y + p.h + gap && y + h + gap > p.y`。
4. 螺旋找不到时，用已放置各项的四条边和画布四边组成候选坐标，按离画布中心最近的顺序取第一个合法位置。
5. 仍找不到则返回 `null`。

- [ ] **Step 4: 运行，确认通过**

Run: `cd desktop && node --test tests/keywordLayout.test.mjs`
Expected: PASS

---

### Task 6: 词云接入页面

**Files:**
- Modify: `src/linkread_desktop/renderers/html.py`（`READER_SCRIPT` 约第 252–254 行）
- Modify: `src/linkread_desktop/renderers/reader.js`（`layoutKeywords`，第 236–310 行）
- Modify: `src/linkread_desktop/renderers/reader.html`（`.cloud-word` 的字号，第 51 行和第 66 行）
- Modify: `src/linkread_desktop/renderers/notes.py`（权重公式，第 45–47 行）
- Test: `tests/test_keyword_cloud.py`

**Interfaces:**
- Consumes: `LinkReadKeywordLayout.placeKeywords(items, width, height, options)`（Task 5）

- [ ] **Step 1: 写失败的测试**（追加到 `tests/test_keyword_cloud.py`）

```python
def test_keyword_cloud_ships_the_layout_module_inline():
    markup = render_html(make_note(["关键词"]))
    assert "LinkReadKeywordLayout" in markup
    assert not any(tag == "script" and "src" in attrs for tag, attrs in CloudParser(markup).elements)


def test_keyword_weights_keep_the_saved_rank_order_within_range():
    page = CloudParser(render_html(make_note(["短词", "第二个词", "第三个主题词", "第四个关键词", "很长的第五个关键词主题"])))
    weights = [float(re.fullmatch(r"--weight:([0-9.]+)", word["attrs"]["style"])[1]) for word in page.words]
    assert weights[0] == 1 and weights == sorted(weights, reverse=True)
    assert all(0 < weight <= 1 for weight in weights) and weights[0] > weights[-1]
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest -q tests/test_keyword_cloud.py -k "layout_module or rank_order"`
Expected: `test_keyword_cloud_ships_the_layout_module_inline` FAIL（页面里没有该模块）；`test_keyword_weights_keep_the_saved_rank_order_within_range` PASS（它只钉住不变的性质）

- [ ] **Step 3: 实现**

- `html.py`：`READER_SCRIPT` 按 `reading-position.js`、`keyword-layout.js`、`reader.js` 的顺序拼接。
- `notes.py`：`rank_weight = (1 + index * .6) ** -1.15`，`weight = rank_weight * min(1, 22 / len(word))`。
- `reader.html`：`.cloud-word` 的字号改为 `calc((13px + 43px * var(--weight)) * var(--cloud-scale, 1))`；`max-width:790px` 下改为 `calc((12px + 30px * var(--weight)) * var(--cloud-scale, 1))`。
- `reader.js` 的 `layoutKeywords`：

  1. 保留现有的前置步骤：宽度无效或未变则返回；清除 `is-positioned`、高度、各词的 `left`、`top`、`max-width` 和 `.keyword-cloud` 上的 `--cloud-scale`。
  2. 超过 52 个词，或页面上没有 `LinkReadKeywordLayout` 时，只做 `fitHover()` 然后返回（流式排布）。
  3. 设好各词的 `max-width`，加上 `is-positioned`，初值 `scale = 1`，最多 3 轮：
     - 设置 `--cloud-scale` 为 `scale`，测量每个词的 `offsetWidth`、`offsetHeight`。
     - 高度从 `max(最高词高 + 2*margin, ceil(面积 / (width * .7)))` 起，每次加 40，最多 8 次，调用 `placeKeywords(sizes, width, height, {margin: 4, gap: 4})`。
     - 放不下就继续加高；8 次都放不下则移除 `is-positioned`，`fitHover()` 后返回。
     - 放下了：算外框宽度 `used`。若 `used < width * .85`、`scale < 1.35` 且还不是第 3 轮，则 `scale = min(1.35, scale * width * .92 / used)`，进入下一轮。
     - 否则采用：高度取 `max(最高词高 + 2*margin, 外框高 + 2*margin)`，按外框水平、垂直居中写入各词的 `left`、`top`，`fitHover()` 后返回。

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest -q tests/test_keyword_cloud.py && cd desktop && node --test tests/keywordLayout.test.mjs`
Expected: PASS

- [ ] **Step 5: 用真实浏览器检查**

用临时目录里的样张（Task 2 的方式），关键词取 7 个和 14 个、中英文各一组：

Run: 无头 Chrome 分别在 `--window-size=1060,1150` 和 `--window-size=720,620` 下对 `…html#keywords` 截图
Expected（逐张查看）：词语不重叠、不截断、全部横排；最大的在中间；词云左右铺到卡片宽度的约 85% 以上（已到 1.35 倍上限的除外）；窄窗口下同样成立。

再用 60 个关键词的样张执行 `--dump-dom`：
Expected: `.keyword-cloud` 没有 `is-positioned`（回到流式排布）。

---

### Task 7: 文档与整体验证

**Files:**
- Modify: `desktop/README.md`（约第 149 行阅读缓存说明；约第 170–178 行阅读页功能列表）
- Modify: `docs/rendering.md`（「设计约束」列表）

- [ ] **Step 1: 更新文档**

- `desktop/README.md` 阅读页功能列表：
  - 把「Keywords 按已保存的关键词顺序排成词云……」改写为：关键词按保存顺序排成紧凑、铺满卡片的横排词云，排在越靠前字号越大，最大的在中间；词很少时整体放大，最多 1.35 倍；超过 52 个词或放不下时退回流式排布。悬停、键盘聚焦和点击搜索的行为不变。
  - 新增一条：阅读页标题左侧显示封面，视频 16:9、播客方形，窄窗口自动缩小。归档里有本地封面时内嵌进页面，离线可用并随 HTML 导出带走；没有本地封面时使用三个白名单图片域名上的在线封面，离线或加载失败时恢复纯标题排版。封面不打印。
  - 阅读缓存说明补一句：重建缓存只读取已保存的封面文件和网址，不联网。
- `docs/rendering.md`「设计约束」新增一条：封面优先内嵌归档中的本地图片，否则引用三个白名单图片域名（`i.ytimg.com`、`*.hdslb.com`、`*.mzstatic.com`）上的在线封面，加载失败即隐藏；除此之外页面不请求任何网络资源。

- [ ] **Step 2: 全量验证**

Run: `.venv/bin/python -m pytest -q`
Expected: 全部 PASS，无失败、无错误。

Run: `cd desktop && npm run build && node --test tests/*.test.mjs`
Expected: 构建成功，Node 测试 `fail 0`。

Run: `cd desktop/src-tauri && cargo test`
Expected: `test result: ok`。

- [ ] **Step 3: 封面的整页截图检查**

样张覆盖：视频封面、播客封面、无封面、很长的标题，各在 1060 和 720 两种宽度、浅色和深色下截图（Task 2 的方式）。
Expected（逐张查看）：封面在标题左侧、不变形；窄窗口下正文没有被推到很靠下；无封面的页面与改动前一致；深色下封面边框可见但不突兀；打印样式里封面被隐藏（检查 `@media print` 规则包含 `.article-cover`）。

- [ ] **Step 4: 清理**

删除临时目录里的样张和无头浏览器配置。仓库里只留下本计划列出的改动，全部保持未提交。
