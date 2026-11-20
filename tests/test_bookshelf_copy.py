from html.parser import HTMLParser
import json
from pathlib import Path
import re

import pytest

from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.renderers.html import render_html
from linkread_desktop.renderers.site import article_metadata, build_site, render_home


def transcript(platform):
    return TranscriptDocument(
        source=SourceInfo(platform=platform, title="文章标题", published_at="2026-11-15"),
        transcript_source="local" if platform == "local" else "subtitle",
        language="zh-Hans",
        segments=[TranscriptSegment(start_ms=0, text="完整原文。")],
    )


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ignored = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignored += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.ignored -= 1

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


@pytest.mark.parametrize("platform", ["local", "youtube", "bilibili"])
def test_source_type_and_language_are_not_invented_content_metadata(tmp_path, platform):
    raw = transcript(platform)
    metadata = article_metadata(raw, tmp_path / "transcript.json")
    assert metadata["category"] == ""
    assert metadata["tags"] == []
    assert metadata["description"] == raw.source.title
    assert metadata["date"] == "2026-11-15"
    assert metadata["source"]  # The actual source remains available in metadata.
    page = render_home([{"slug": "article", "metadata": metadata}])
    visible = VisibleText()
    visible.feed(page)
    text = "".join(visible.parts)
    assert "本地文件" not in text and "本地字幕" not in text
    assert "YouTube" not in text and "Bilibili" not in text and "zh-Hans" not in text
    assert '<span class="category">' not in page
    assert '<span class="tag">' not in page
    assert 'title="发布日期"' in page


def test_explicit_user_categories_and_tags_are_preserved_even_if_they_name_sources(tmp_path):
    manual = {"category": "本地字幕", "tags": ["YouTube", "zh-Hans"], "date": "2025-04-08"}
    path = tmp_path / "article.json"
    original = json.dumps(manual, ensure_ascii=False)
    path.write_text(original, encoding="utf-8")
    metadata = article_metadata(transcript("local"), tmp_path / "transcript.json")
    assert metadata["category"] == manual["category"]
    assert metadata["tags"] == manual["tags"]
    assert metadata["date"] == manual["date"]
    assert metadata["date_source"] == "custom"
    assert path.read_text(encoding="utf-8") == original


def test_bookshelf_has_one_count_without_removing_filter_empty_state():
    metadata = article_metadata(transcript("local"), Path("missing-transcript.json"))
    page = render_home([{"slug": "article", "metadata": metadata}])
    assert '<span class="count" id="article-count">1</span>' in page
    assert "result-count" not in page and "共 1 篇" not in page
    assert 'id="empty">没有找到匹配的文章' in page
    assert "getElementById('article-count').textContent=matched" in page
    assert 'aria-label="阅读文章表"' in page


def test_local_export_suffix_is_only_removed_from_default_display_title(tmp_path):
    raw = transcript("local")
    raw.source.title = "课程标题_哔哩哔哩_bilibili_BV14uW36uEdD_字幕"
    original = raw.model_dump_json()
    source = tmp_path / "transcript.json"
    source.write_text(original, encoding="utf-8")
    metadata = article_metadata(raw, source)
    assert metadata["title"] == metadata["description"] == "课程标题"
    assert raw.model_dump_json() == original
    assert source.read_text(encoding="utf-8") == original


def test_explicit_title_is_not_stripped_again_in_bookshelf_article(tmp_path):
    raw = transcript("local")
    raw.source.title = "原始标题_哔哩哔哩_bilibili_BV14uW36uEdD_字幕"
    source = tmp_path / "transcript.json"
    source.write_text(raw.model_dump_json(), encoding="utf-8")
    explicit = "用户标题_哔哩哔哩_bilibili_BV14uW36uEdD_字幕"
    (tmp_path / "article.json").write_text(json.dumps({"title": explicit}), encoding="utf-8")
    output = tmp_path / "site"
    assert build_site(source, output) == 1
    page = next((output / "pages").glob("*/index.html")).read_text(encoding="utf-8")
    assert f"<h1>{explicit}</h1>" in page
    assert explicit in (output / "index.html").read_text(encoding="utf-8")


def test_changing_display_title_keeps_reading_state_identity(tmp_path):
    raw = transcript("local")
    raw.source.title = "原始标题_哔哩哔哩_bilibili_BV14uW36uEdD_字幕"
    original = raw.model_dump_json()
    source = tmp_path / "transcript.json"
    source.write_text(original, encoding="utf-8")
    output = tmp_path / "site"

    def storage_key(page):
        return re.search(r'data-reading-key="([^"]+)"', page)[1]

    expected = storage_key(render_html(raw))
    build_site(source, output)
    page_path = next((output / "pages").glob("*/index.html"))
    assert storage_key(page_path.read_text(encoding="utf-8")) == expected
    (tmp_path / "article.json").write_text('{"title":"新的显示标题"}', encoding="utf-8")
    build_site(source, output)
    page = page_path.read_text(encoding="utf-8")
    assert "<h1>新的显示标题</h1>" in page
    assert storage_key(page) == expected
    assert source.read_text(encoding="utf-8") == original
