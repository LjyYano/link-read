"""Reader shortcuts resolve to verified source cues, never fabricated timestamps."""

from html.parser import HTMLParser
import json
import re

import pytest

from linkread_desktop.ai.models import NoteAnalysis, NoteDocument
from linkread_desktop.ai.publication import _FIELDS, _make_mindmap, _make_mindmap_links, complete_extras
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.renderers.extras import (
    ArticleExtras, MindmapLink, MindmapLinks, ReadingOverview, ReadingSection,
    is_extra_json, load_extras, save_missing_extras,
)
from linkread_desktop.renderers.html import render_html
from linkread_desktop.renderers.shownotes import description_html, description_markdown


class Links(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.anchors = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.anchors.append(dict(attrs))


def raw(**source):
    return TranscriptDocument(
        source=SourceInfo(platform="apple_podcasts", title="原文节目", duration_ms=140_000,
                          url="https://podcasts.apple.com/cn/podcast/id1603392589?i=1000757475500",
                          video_id="1000757475500", **source),
        transcript_source="subtitle",
        segments=[
            TranscriptSegment(start_ms=5_000, end_ms=20_000, text="开场的原始内容。"),
            TranscriptSegment(start_ms=30_000, end_ms=60_000, text="第一部分完整原文。"),
            TranscriptSegment(start_ms=60_000, end_ms=80_000, text="第一部分后续论据。"),
            TranscriptSegment(start_ms=90_000, end_ms=120_000, text="第二部分结论。"),
        ],
    )


def note():
    return NoteDocument(transcript=raw(), provider="codex", analysis=NoteAnalysis(summary="概括整篇节目。"))


def internal(markup):
    return [anchor for anchor in Links(markup).anchors if anchor.get("href", "").startswith("#cue-")]


def test_summary_rows_link_to_the_same_validated_chapters_as_the_transcript():
    overview = ReadingOverview(lead="主题概览。", sections=[
        ReadingSection(start_segment_index=0, title='开场 "主题"', summary="第一节内容。"),
        ReadingSection(start_segment_index=3, title="结论", summary="末节内容。"),
    ])
    markup = render_html(raw(), extras=ArticleExtras(reading=overview))
    rows = [link for link in Links(markup).anchors if link.get("class") == "summary-line"]
    assert [row["href"] for row in rows] == ["#chapter-0", "#chapter-3"]
    assert rows[0]["title"] == 'Read section: 开场 "主题"'
    for row in rows:
        assert f'id="{row["href"][1:]}"' in markup


def test_plain_timestamps_resolve_real_cues_including_intro_and_silent_gaps():
    doc = raw(description="00:00 开场\n00:30第一部分\n【01:00】论据\n01:25 静音之后\n01:30 结论")
    original = doc.model_dump_json()
    markup = description_html(doc.source, transcript=doc)
    assert [link["data-source-cue"] for link in internal(markup)] == ["0", "1", "2", "3", "3"]
    assert all(link["href"] == "#cue-" + link["data-source-cue"] for link in internal(markup))
    assert "00:30</a>第一部分" in markup
    assert doc.model_dump_json() == original


@pytest.mark.parametrize("time", ["00:60", "00:99", "00:60:00", "99:00:00", "02:20", "02:30", "02:05",
                                 "1000:00", "00:01:02:03", "-00:30", "12.00:30", "2026-11-18T00:00:30Z"])
def test_invalid_or_outside_available_source_timestamps_remain_plain_text(time):
    doc = raw(description=f"{time} 不应被转换")
    markup = description_html(doc.source, transcript=doc)
    assert internal(markup) == []
    assert time in markup


def test_hours_and_total_minutes_are_supported_when_inside_original_timing():
    doc = raw(description="01:01:01 经过一小时\n61:01 相同时间")
    doc.source.duration_ms = 4_000_000
    doc.segments = [TranscriptSegment(start_ms=3_600_000, end_ms=3_900_000, text="一小时之后的原文。")]
    assert [link["data-source-cue"] for link in internal(description_html(doc.source, transcript=doc))] == ["0", "0"]


def test_unknown_duration_and_final_open_ended_cue_never_invent_later_coverage():
    doc = raw(description="01:30 真实开始\n01:31 未知后续")
    doc.source.duration_ms = None
    doc.segments[-1].end_ms = None
    links = internal(description_html(doc.source, transcript=doc))
    assert len(links) == 1 and links[0]["href"] == "#cue-3"


def test_overlapping_dialogue_maps_to_a_real_active_cue():
    doc = raw(description="00:25 仍在讲话")
    doc.segments = [TranscriptSegment(start_ms=0, end_ms=30_000, text="较长的讲话。"),
                    TranscriptSegment(start_ms=10_000, end_ms=20_000, text="插话。")]
    assert internal(description_html(doc.source, transcript=doc))[0]["href"] == "#cue-0"


def test_publisher_links_code_and_urls_keep_original_meaning_and_never_nest_anchors():
    doc = raw(description='<ul><li>00:30 正文</li><li><a href="https://example.org/00:30">00:30 外链</a></li>'
                          '<li><code>00:30</code><pre>01:00</pre></li></ul>'
                          '<script>00:30</script><img onerror="steal()" src="https://evil.test/pixel">',
              description_format="html")
    markup = description_html(doc.source, transcript=doc)
    assert len(internal(markup)) == 1
    assert 'href="https://example.org/00:30"' in markup
    assert '<code>00:30</code><pre>01:00</pre>' in markup
    assert "steal()" not in markup and "evil.test" not in markup
    markdown = description_markdown(doc.source)
    assert "#cue-" not in markdown and "https://example.org/00:30" in markdown
    doc.source.description_format = "text"
    doc.source.description = "https://example.org/watch?t=00:30\n00:30 正文"
    plain = description_html(doc.source, transcript=doc)
    assert len(internal(plain)) == 1 and 'href="https://example.org/watch?t=00:30"' in plain


def map_value():
    # Breadth-first model order deliberately differs from rendered heading order.
    return {"root": "文章主线", "nodes": [
        {"parent": 0, "text": "第一个观点", "index": 0},
        {"parent": 0, "text": "最终结论", "index": 1},
        {"parent": 1, "text": "跨章节综合", "index": None},
    ]}


def test_mindmap_mapping_resolves_candidate_ids_to_exact_cues_and_tree_paths():
    value = map_value()
    markdown = _make_mindmap(value)
    links = _make_mindmap_links(value, [1, 3], note(), markdown)
    assert links.verified(markdown, raw()) == {"0.0": 1, "0.1": 3}
    assert "0.0.0" not in links.verified(markdown, raw())
    markup = render_html(raw(), extras=ArticleExtras(mindmap=markdown, mindmap_links=links))
    data = re.search(r'id="linkread-mindmap-links">(.*?)</script>', markup, re.S)[1]
    assert json.loads(data) == {"0.0": 1, "0.1": 3}
    assert _FIELDS["mindmap"]["properties"]["nodes"]["items"]["properties"]["index"]["type"] == ["integer", "null"]


@pytest.mark.parametrize("index", [-1, True, 0.5, "0", 2])
def test_invalid_model_source_indices_fail_instead_of_pointing_to_an_unrelated_cue(index):
    value = map_value()
    value["nodes"][0]["index"] = index
    with pytest.raises(ValueError):
        markdown = _make_mindmap(value)
        _make_mindmap_links(value, [1, 3], note(), markdown)


def test_old_unmapped_mindmaps_do_not_gain_guessed_source_links():
    value = map_value()
    for item in value["nodes"]:
        del item["index"]
    markdown = _make_mindmap(value)
    assert _make_mindmap_links(value, [1, 3], note(), markdown) is None
    markup = render_html(raw(), extras=ArticleExtras(mindmap=markdown))
    assert 'id="linkread-mindmap-links">{}</script>' in markup


def test_mapping_invalidates_for_changed_text_times_source_or_map_but_not_display_metadata():
    value = map_value()
    markdown = _make_mindmap(value)
    links = _make_mindmap_links(value, [1, 3], note(), markdown)
    for change in ("text", "time", "source"):
        doc = raw()
        if change == "text":
            doc.segments[0].text = "替换的原文。"
        elif change == "time":
            doc.segments[0].end_ms += 1
        else:
            doc.source.url = doc.source.url.replace("1000757475500", "1000757475501")
            doc.source.video_id = "1000757475501"
        assert links.verified(markdown, doc) == {}
    assert links.verified(markdown.replace("最终结论", "另一个结论"), raw()) == {}
    doc = raw(description="后来补充的简介")
    doc.source.title = "展示标题修改"
    assert links.verified(markdown, doc) == {"0.0": 1, "0.1": 3}
    invalid_path = links.model_copy(update={"links": [MindmapLink(path="0.77", start_segment_index=1)]})
    assert invalid_path.verified(markdown, raw()) == {}
    invalid_cue = links.model_copy(update={"links": [MindmapLink(path="0.0", start_segment_index=999)]})
    assert invalid_cue.verified(markdown, raw()) == {}


def test_new_publication_persists_mapping_without_an_extra_model_call(tmp_path):
    class Provider:
        name = "codex"
        calls = 0

        def generate(self, prompt, schema, *, timeout_seconds):
            self.calls += 1
            assert "section_candidates" in prompt and "无法确定" in prompt
            return {"mindmap": {"root": "主线", "nodes": [{"parent": 0, "text": "真实原文", "index": 0}]}}

    from linkread_desktop.quotes import QuoteCards
    from linkread_desktop.renderers.catalog import CatalogMetadata
    provider = Provider()
    previous = ArticleExtras(reading=ReadingOverview(lead="导语。"), quotes=QuoteCards(cards=[]),
                             catalog=CatalogMetadata(category="测试", tags=["主题"]))
    completed = complete_extras(note(), previous, provider, timeout_seconds=20)
    assert provider.calls == 1 and completed.mindmap_links is not None
    source = tmp_path / "note.json"
    save_missing_extras(source, previous, completed)
    loaded = load_extras(source)
    assert loaded.mindmap_links.verified(loaded.mindmap, raw()) == {"0.0": 0}
    assert is_extra_json(tmp_path / "mindmap-links.json")
    assert is_extra_json(tmp_path / "episode.mindmap-links.json")
    # Invalid optional navigation cannot stop reading the saved article itself.
    (tmp_path / "mindmap-links.json").write_text('{"invalid":true}', encoding="utf-8")
    assert load_extras(source).mindmap == completed.mindmap
    assert load_extras(source).mindmap_links is None


def test_duplicate_mapping_paths_are_rejected():
    value = map_value()
    markdown = _make_mindmap(value)
    links = _make_mindmap_links(value, [1, 3], note(), markdown)
    with pytest.raises(ValueError):
        MindmapLinks.model_validate({**links.model_dump(), "links": [links.links[0].model_dump()] * 2})
