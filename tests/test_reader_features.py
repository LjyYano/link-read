"""Reader navigation, untrusted publisher HTML and stable reading identities."""

from html.parser import HTMLParser
import re

import pytest

from linkread_desktop.ai.models import NoteAnalysis, NoteDocument
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.quotes import QuoteCard, QuoteCards
from linkread_desktop.renderers.extras import ArticleExtras
from linkread_desktop.renderers.html import render_html
from linkread_desktop.renderers.markdown import render_markdown
from linkread_desktop.renderers.reading_state import reading_state_key
from linkread_desktop.renderers.shownotes import description_html, shownotes_panel


class Markup(HTMLParser):
    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.elements, self.tabs, self.current = [], [], None
        self.feed(value)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs))
        if tag == "button" and "data-tab" in attrs:
            self.current = {"attrs": attrs, "text": "", "icons": 0}
            self.tabs.append(self.current)
        if tag == "svg" and self.current is not None:
            self.current["icons"] += 1
            assert attrs.get("aria-hidden") == "true"

    def handle_data(self, text):
        if self.current is not None:
            self.current["text"] += text

    def handle_endtag(self, tag):
        if tag == "button":
            self.current = None


def raw(platform="youtube", **source):
    return TranscriptDocument(
        source=SourceInfo(platform=platform, title="中文节目标题",
                          video_id="1-Fo8McCD9w" if platform == "youtube" else None, **source),
        transcript_source="subtitle" if platform != "local" else "local",
        segments=[TranscriptSegment(start_ms=0, end_ms=1000, text="原始内容完整保留。")],
    )


def test_english_tabs_have_icons_and_shownotes_follow_highlights_without_notes():
    transcript = raw(description="播客原始简介", description_format="text")
    note = NoteDocument(transcript=transcript, provider="codex", analysis=NoteAnalysis(
        summary="原语言摘要。", keywords=["中文主题"],
    ))
    extras = ArticleExtras(
        mindmap="# 根主题\n## 主要观点\n### 机制\n#### 证据\n## 结论\n",
        quotes=QuoteCards(cards=[QuoteCard(title="原文", start_segment_index=0, end_segment_index=0,
                                         front="原始内容完整保留。", back="原始内容完整保留。")]),
    )
    page = Markup(render_html(note, extras=extras))
    assert [tab["text"] for tab in page.tabs] == [
        "Summary", "Mindmap", "Transcript", "Keywords", "Highlights", "Shownotes",
    ]
    assert all(tab["icons"] == 1 for tab in page.tabs)
    assert all(tab["attrs"]["role"] == "tab" for tab in page.tabs)
    assert [tab["attrs"]["tabindex"] for tab in page.tabs] == ["-1", "-1", "0", "-1", "-1", "-1"]
    assert not any(attrs.get("id") in {"tab-notes", "panel-notes", "notes-area"} for _, attrs in page.elements)
    markup = render_html(note, extras=extras)
    assert "原始内容完整保留。" in markup and "原语言摘要。" in markup and "播客原始简介" in markup


def test_publisher_html_retains_semantic_content_and_links_without_active_markup():
    source = raw(description='''<h1>本期节目</h1><p>介绍 <b>关键观点</b> &amp; 资料</p>
        <ul><li>00:01 开场</li><li><a href="https://example.org/notes?a=1&amp;b=2" onclick="steal()">参考资料</a></li></ul>
        <script>steal()</script><style>@import "https://evil.test";</style><iframe src="https://evil.test">hidden</iframe>
        <svg onload="steal()"><text>hidden</text></svg><img src="https://evil.test/pixel" onerror="steal()">
        <p style="background:url(https://evil.test)"><a href="javascript:steal()">危险链接的文字</a></p>''', description_format="html").source
    before = source.model_dump_json()
    html = description_html(source)
    parser = Markup(html)
    assert '<h2>本期节目</h2>' in html and '<strong>关键观点</strong>' in html
    assert '<li>00:01 开场</li>' in html and '危险链接的文字' in html
    assert 'href="https://example.org/notes?a=1&amp;b=2"' in html
    assert 'rel="noopener noreferrer"' in html
    assert not any(tag in {"script", "style", "iframe", "svg", "img", "object"} for tag, _ in parser.elements)
    assert not any(key.startswith("on") or key == "style" for _, attrs in parser.elements for key in attrs)
    assert "steal()" not in html and "evil.test" not in html and "hidden" not in html
    assert source.model_dump_json() == before


def test_embedded_player_does_not_hide_the_shownotes_that_follow_it():
    source = raw(description='<p>节目简介</p><object data="https://example.org/player">'
                             '<embed src="https://example.org/player"></object>'
                             '<p>后续时间轴与参考资料</p>', description_format="html").source
    markup = description_html(source)
    assert '<p>节目简介</p>' in markup and '<p>后续时间轴与参考资料</p>' in markup
    assert '<object' not in markup and '<embed' not in markup


@pytest.mark.parametrize("href", [
    "javascript:alert(1)", "java&#10;script:alert(1)", "data:text/html,hello", "file:///etc/passwd",
    "https://user:pass@example.org/", "https://example.org:bad/", "http://[invalid/", "vbscript:alert(1)",
])
def test_unsafe_source_link_targets_are_removed_but_visible_text_survives(href):
    source = raw(description=f'<p><a href="{href}">阅读资料</a></p>', description_format="html").source
    parser = Markup(description_html(source))
    assert not any("href" in attrs for _, attrs in parser.elements)
    assert "阅读资料" in description_html(source)


def test_plain_description_is_escaped_linkified_and_not_treated_as_generated_markdown():
    source = raw(description='<script>not executable</script>\n\n00:00 开场\n资料 https://example.org/a?q=1&b=2。').source
    markup = description_html(source)
    assert '&lt;script&gt;not executable&lt;/script&gt;' in markup
    assert '\n\n00:00 开场\n' in markup
    assert 'href="https://example.org/a?q=1&amp;b=2"' in markup
    assert '<script>' not in markup


def test_markdown_export_preserves_source_notes_and_safe_links_without_active_html():
    transcript = raw(description='<h2>本期话题</h2><p>Source notes <strong>完整保留</strong>。'
                                '<a href="https://example.org/reference?a=1&amp;b=2">资料链接</a></p>'
                                '<ol><li>第一个主题</li><li>第二个主题</li></ol>'
                                '<script>alert(1)</script><img src="https://evil.test/pixel">', description_format="html")
    markdown = render_markdown(transcript)
    assert "## Shownotes" in markdown and "Source notes 完整保留" in markdown
    assert "### 本期话题" in markdown and "1. 第一个主题" in markdown and "2. 第二个主题" in markdown
    assert "资料链接 <https://example.org/reference?a=1&b=2>" in markdown
    assert "<script>" not in markdown and "<img" not in markdown and "alert(1)" not in markdown
    assert "原始内容完整保留。" in markdown


def test_literal_markdown_in_source_description_cannot_create_active_links_or_images():
    transcript = raw(description='[click](javascript:alert(1)) ![tracking](https://evil.test/pixel) <script>bad()</script>')
    markdown = render_markdown(transcript)
    assert r'\[click\](javascript:alert(1))' in markdown
    assert r'\!\[tracking\]' in markdown
    assert '&lt;script&gt;bad()&lt;/script&gt;' in markdown
    assert '<script>' not in markdown


def test_missing_description_is_explicit_for_online_sources_without_inventing_local_metadata():
    markup = shownotes_panel(raw().source)
    assert "No shownotes saved" in markup and "View original" in markup
    assert 'data-refresh-shownotes hidden' in markup
    assert shownotes_panel(raw("local").source) == ""
    source = raw(description="<script>not a description</script>", description_format="html").source
    assert "No shownotes saved" in shownotes_panel(source)


def test_local_reading_identity_survives_title_and_description_changes_but_not_different_content():
    transcript = raw("local")
    key = reading_state_key(transcript)
    transcript.source.title = "显示标题调整"
    transcript.source.description = "新增介绍"
    transcript.source.description_format = "text"
    transcript.source.thumbnail_url = "https://example.org/new.png"
    assert reading_state_key(transcript) == key
    transcript.segments[0].text = "另一份本地字幕。"
    assert reading_state_key(transcript) != key


def test_online_reading_identity_uses_exact_source_including_bilibili_part():
    one = raw("bilibili", url="https://www.bilibili.com/video/BV1H4sJ6XEF5/?p=1")
    one.source.video_id = "BV1H4sJ6XEF5"
    two = one.model_copy(deep=True)
    two.source.url = "https://www.bilibili.com/video/BV1H4sJ6XEF5/?p=2&share_source=copy_web"
    assert reading_state_key(one) != reading_state_key(two)
    assert reading_state_key(two).endswith("/?p=2")
    podcast = raw("apple_podcasts", url="https://podcasts.apple.com/cn/podcast/id1603392589?i=1000757475500")
    podcast.source.video_id = "1000757475500"
    previous = reading_state_key(podcast)
    podcast.source.url = podcast.source.url.replace("/cn/", "/us/") + "&share=copy"
    assert reading_state_key(podcast) == previous


def test_reading_flags_are_explicit_and_do_not_mean_generation_finished():
    transcript = raw()
    note = NoteDocument(transcript=transcript, provider="codex", analysis=NoteAnalysis(summary="已生成摘要"))
    default = render_html(note)
    assert 'data-is-read="false" data-is-starred="false"' in default
    markup = render_html(note, reading_state={"isRead": True, "isStarred": True})
    assert 'data-is-read="true" data-is-starred="true"' in markup
    assert 'aria-label="Mark Unread" title="Mark Unread"' in markup and '>Starred</span>' in markup
    assert 'linkread-reading-state-v1' in markup
    assert 'localStorage.removeItem' not in markup
    assert re.search(r'data-reading-key="([^"]+)"', default)[1] == re.search(r'data-reading-key="([^"]+)"', markup)[1]


def test_removing_notes_interface_does_not_modify_stored_analysis_or_clear_personal_notes():
    note = NoteDocument(transcript=raw(), provider="codex", analysis=NoteAnalysis(
        summary="原有摘要", takeaways=["既有核心观点一", "既有核心观点二"], keywords=["原有关键词"],
    ))
    before = note.model_dump_json()
    markup = render_html(note)
    assert note.model_dump_json() == before
    assert note.analysis.takeaways == ["既有核心观点一", "既有核心观点二"]
    assert 'id="tab-notes"' not in markup and 'id="notes-area"' not in markup
    assert "localStorage.removeItem" not in markup and "localStorage.clear" not in markup
    assert "linkread-notes-" not in markup
