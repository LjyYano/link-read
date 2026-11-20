"""Copyable attribution uses the display title and only trustworthy source identities."""

from html.parser import HTMLParser

import pytest

from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.quotes import QuoteCard, QuoteCards
from linkread_desktop.renderers.extras import ArticleExtras
from linkread_desktop.renderers.html import render_html


class Cards(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.cards = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "details" and attrs.get("class") == "anki-card":
            self.cards.append(attrs)


def rendered(source, *, title=None):
    raw = TranscriptDocument(source=source, transcript_source="local", segments=[
        TranscriptSegment(start_ms=754_678, end_ms=755_999, text="保留原句，不丢上下文。"),
    ])
    before = raw.model_dump_json()
    extras = ArticleExtras(quotes=QuoteCards(cards=[QuoteCard(
        title="原文", start_segment_index=0, end_segment_index=0,
        front=raw.segments[0].text, back=raw.segments[0].text,
    )]))
    markup = render_html(raw, extras=extras, title=title)
    assert raw.model_dump_json() == before
    assert markup.count("12:34–12:35") == 1
    assert 'data-copy-side="front"' in markup and 'data-copy-side="back"' in markup
    assert 'data-copy-side="source"' in markup
    return Cards(markup).cards[0], markup


@pytest.mark.parametrize("source,expected", [
    (SourceInfo(platform="youtube", title="原节目", video_id="1-Fo8McCD9w",
                url="https://evil.test/pretend"), "https://www.youtube.com/watch?v=1-Fo8McCD9w&t=754s"),
    (SourceInfo(platform="bilibili", title="原节目", video_id="BV1H4sJ6XEF5",
                url="https://www.bilibili.com/video/BV1H4sJ6XEF5?p=2"),
     "https://www.bilibili.com/video/BV1H4sJ6XEF5/?p=2&t=754"),
    (SourceInfo(platform="apple_podcasts", title="原节目", video_id="1000757475500",
                url="https://podcasts.apple.com/cn/podcast/example/id1603392589?i=1000757475500&t=754"),
     "https://podcasts.apple.com/cn/podcast/id1603392589?i=1000757475500"),
    (SourceInfo(platform="local", title="本地字幕", url="file:///Users/yano/private/subtitles.srt"), ""),
    (SourceInfo(platform="youtube", title="无效来源", url="javascript:alert(1)"), ""),
    (SourceInfo(platform="apple_podcasts", title="身份冲突", video_id="1000757475501",
                url="https://podcasts.apple.com/cn/podcast/id1603392589?i=1000757475500"), ""),
])
def test_attribution_uses_real_identity_and_floor_timestamp_without_local_paths(source, expected):
    attributes, markup = rendered(source)
    assert attributes["data-copy-source"] == expected
    assert attributes["data-copy-author"] == ""
    assert "file:///Users/yano/private" not in markup
    assert "https://evil.test/pretend" not in markup


def test_manual_title_and_author_are_escaped_as_text_without_changing_the_original():
    source = SourceInfo(platform="youtube", title="原始标题", video_id="1-Fo8McCD9w",
                        author='作者 <img src=x onerror="alert(1)"> & Co')
    title = '手动标题 </script><script>alert(1)</script> "测试"'
    attributes, markup = rendered(source, title=title)
    assert attributes["data-copy-title"] == title
    assert attributes["data-copy-author"] == source.author
    assert source.title == "原始标题"
    assert '<script>alert(1)</script>' not in markup
    assert '<img src=x onerror="alert(1)">' not in markup


def test_local_display_cleanup_is_used_when_no_manual_title_exists():
    source = SourceInfo(platform="local", title="读懂软件开发_哔哩哔哩_bilibili_BV14uW36uEdD_字幕")
    attributes, _ = rendered(source)
    assert attributes["data-copy-title"] == "读懂软件开发"

