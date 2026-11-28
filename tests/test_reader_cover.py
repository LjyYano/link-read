"""Offline cover rendering: validation, markup, callers and the unchanged text-only header."""

import base64
import fnmatch
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import pytest
import requests

from linkread_desktop import covers, desktop
from linkread_desktop.article_details import linked_source_value, read_article_details
from linkread_desktop.errors import LinkReadError
from linkread_desktop.metadata import VideoMetadata, thumbnail_url
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.renderers.html import render_html
from test_article_details import ARTWORK, VIDEO, make_local_archive, video_preview
from test_local_cover import JPEG_BYTES
from test_reader_cache import archive

VIDEO_COVER = "https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg"
INLINE_COVER = "data:image/jpeg;base64,/9j/4AAQSkZJRg"
EPISODE = "https://podcasts.apple.com/us/podcast/id1234567890?i=1000000000001"
EPISODE_ARTWORK = "https://is1-ssl.mzstatic.com/image/thumb/podcast/600x600bb.jpg"


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


def has_cover(html):
    """Only a rendered cover adds this header state; CSS and scripts name the class anyway."""
    return '<header class="hero has-cover">' in html


def page_markup(cover_url):
    return render_html(document(), cover_url=cover_url)


@pytest.mark.parametrize("bad", [
    None, "", "https://evil.example/cover.jpg", "javascript:alert(1)",
    "data:image/svg+xml,<svg onload=alert(1)>", 'data:image/png;base64,AAAA" onerror="alert(1)',
    "http://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg",
])
def test_untrusted_cover_values_keep_the_plain_text_header(bad):
    page = CoverParser(render_html(document(), cover_url=bad))
    # The stylesheet and script name this class regardless; judge the rendered elements.
    assert page.images == [] and page.header_class == "hero"
    assert "alert(1)" not in page_markup(bad)


def test_without_a_cover_the_header_markup_is_unchanged():
    markup = render_html(document())
    assert '<header class="hero"><h1>' in markup
    assert "<img" not in markup
    assert markup == render_html(document())


def cached(article):
    return article.with_name(".reader.html").read_text(encoding="utf-8")


def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise pytest.fail("opening an article must stay offline")
    # desktop.py 按名字导入了 fetch_cover_bytes，两处都要替换才拦得住。
    monkeypatch.setattr(covers, "fetch_cover_bytes", refuse)
    monkeypatch.setattr(desktop, "fetch_cover_bytes", refuse)
    # Guard the rule itself, not only the call site that is known today.
    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests.Session, "get", refuse)


def test_prepared_reader_prefers_the_local_cover_and_never_fetches(tmp_path, monkeypatch):
    no_network(monkeypatch)
    article = archive(tmp_path)
    original = article.read_bytes()
    desktop._prepared_reader(article)
    assert 'src="https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg"' in cached(article)
    covers.save_cover_bytes(article.parent, ".jpg", JPEG_BYTES)
    desktop._prepared_reader(article)
    html = cached(article)
    assert 'src="data:image/jpeg;base64,' + base64.b64encode(JPEG_BYTES).decode("ascii") + '"' in html
    assert has_cover(html)
    assert article.read_bytes() == original


def test_prepared_reader_links_online_when_the_local_cover_is_too_large_to_inline(tmp_path, monkeypatch):
    no_network(monkeypatch)
    article = archive(tmp_path)
    covers.save_cover_bytes(article.parent, ".jpg", JPEG_BYTES)
    monkeypatch.setattr(covers, "MAX_EMBEDDED_COVER_BYTES", 16)
    desktop._prepared_reader(article)
    html = cached(article)
    assert 'src="https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg"' in html
    assert "data:image" not in html and has_cover(html)


def test_prepared_reader_uses_saved_thumbnail_urls_and_never_the_network(tmp_path, monkeypatch):
    no_network(monkeypatch)
    article = archive(tmp_path)          # 旧的 YouTube 归档：没有保存封面网址
    desktop._prepared_reader(article)
    assert 'src="https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg"' in cached(article)
    assert "data:image" not in cached(article) and has_cover(cached(article))

    saved = tmp_path / "saved"
    saved.mkdir()
    raw = TranscriptDocument(
        source=SourceInfo(platform="bilibili", title="保存过封面网址的归档", video_id="BV1rxqmBhE91",
                          url="https://www.bilibili.com/video/BV1rxqmBhE91",
                          thumbnail_url="https://i0.hdslb.com/bfs/archive/saved.jpg"),
        transcript_source="subtitle", segments=[TranscriptSegment(start_ms=0, end_ms=1000, text="原文。")])
    (saved / "transcript.json").write_text(raw.model_dump_json(), encoding="utf-8")
    (saved / "article.html").write_text("<!doctype html><title>Original</title>", encoding="utf-8")
    desktop._prepared_reader(saved / "article.html")
    assert 'src="https://i0.hdslb.com/bfs/archive/saved.jpg"' in cached(saved / "article.html")


def test_local_subtitle_reader_follows_the_linked_source_cover(tmp_path, monkeypatch):
    library = tmp_path / "library"
    article = make_local_archive(library) / "article.html"
    monkeypatch.setattr(desktop, "video_metadata", video_preview)
    monkeypatch.setattr(desktop, "fetch_cover_bytes", lambda *a, **k: None)     # 不保存本地封面，走在线网址
    desktop._prepared_reader(article)
    assert not has_cover(cached(article))
    desktop._update_article_details(library, article.parent.name, {"title": None, "category": None, "sourceUrl": VIDEO})
    desktop._prepared_reader(article)
    assert f'src="{ARTWORK}"' in cached(article) and has_cover(cached(article))
    desktop._update_article_details(library, article.parent.name, {"title": None, "category": None, "sourceUrl": None})
    desktop._prepared_reader(article)
    assert not has_cover(cached(article))


def link_episode(url, source, artwork):
    def video_metadata(link, **_):
        assert link == url
        return VideoMetadata(source=source, thumbnail_url=artwork)
    return video_metadata


def test_linked_podcast_episode_keeps_its_square_cover_for_imported_subtitles(tmp_path, monkeypatch):
    library = tmp_path / "library"
    article = make_local_archive(library) / "article.html"
    source = SourceInfo(platform="apple_podcasts", video_id="1000000000001", title="单集标题", url=EPISODE)
    monkeypatch.setattr(desktop, "video_metadata", link_episode(EPISODE, source, EPISODE_ARTWORK))
    monkeypatch.setattr(desktop, "fetch_cover_bytes", lambda *a, **k: None)
    desktop._update_article_details(library, article.parent.name, {"title": None, "category": None, "sourceUrl": EPISODE})
    assert read_article_details(article.parent)["linkedSource"]["platform"] == "apple_podcasts"
    desktop._prepared_reader(article)
    img = CoverParser(cached(article)).images[0]
    assert (img["class"], (img["width"], img["height"]), img["src"]) == (
        "article-cover is-square", ("600", "600"), EPISODE_ARTWORK)


def test_linked_cover_without_a_saved_platform_keeps_the_video_shape(tmp_path, monkeypatch):
    library = tmp_path / "library"
    article = make_local_archive(library) / "article.html"
    # Overlays saved before the platform was recorded can only assume a video cover.
    details = {"version": 1, "title": None, "category": None,
               "linkedSource": {"url": EPISODE, "title": "单集标题", "thumbnailUrl": EPISODE_ARTWORK}}
    (article.parent / ".article-details.json").write_text(json.dumps(details), encoding="utf-8")
    monkeypatch.setattr(desktop, "fetch_cover_bytes", lambda *a, **k: pytest.fail("rebuilding must stay offline"))
    desktop._prepared_reader(article)
    img = CoverParser(cached(article)).images[0]
    assert (img["class"], (img["width"], img["height"])) == ("article-cover", ("480", "270"))


def test_linked_source_rejects_an_unknown_platform():
    linked = {"url": VIDEO, "title": "原视频", "thumbnailUrl": ARTWORK, "platform": "evil"}
    with pytest.raises(LinkReadError, match="已关联的来源信息无效"):
        linked_source_value(linked)


# The same cases drive the frontend's thumbnailSource and the native reader's image policy.
COVER_CASES = json.loads((Path(__file__).parent / "fixtures" / "cover_hosts.json").read_text(encoding="utf-8"))


def test_cover_host_allowlist_matches_the_shared_cases():
    for url in COVER_CASES["accepted"]:
        assert covers.valid_cover_source(url) == url, url
    for url in COVER_CASES["rejected"]:
        assert covers.valid_cover_source(url) is None, url
    for case in COVER_CASES["upgraded"]:
        assert thumbnail_url(case["input"]) == case["expected"]
    # A host the code accepts must be one the reader page is allowed to load from,
    # and every allowed pattern must still correspond to an accepted host.
    patterns = [urlsplit(source).netloc for source in COVER_CASES["csp"]]
    hosts = [urlsplit(url).hostname for url in COVER_CASES["accepted"]]
    assert all(urlsplit(source).scheme == "https" for source in COVER_CASES["csp"])
    assert all(any(fnmatch.fnmatchcase(host, pattern) for pattern in patterns) for host in hosts), hosts
    assert all(any(fnmatch.fnmatchcase(host, pattern) for host in hosts) for pattern in patterns), patterns


def test_the_cover_is_only_hidden_while_scripts_can_reveal_it():
    # A cover is invisible until it has loaded, but that rule must depend on the marker a
    # script sets, so a page opened without JavaScript still shows its cover.
    markup = render_html(document(), cover_url=INLINE_COVER)
    assert "document.documentElement.classList.add('js')" in markup
    rules = re.sub(r"/\*.*?\*/", "", markup, flags=re.S)
    hiding = [selector.strip() for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", rules)
              if ".article-cover" in selector and re.search(r"opacity\s*:\s*0\b", body)]
    assert hiding and all(selector.startswith(".js ") for selector in hiding), hiding
