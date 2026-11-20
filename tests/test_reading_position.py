"""Source-cue anchors survive rendering and a raw-to-analysis upgrade."""

from html.parser import HTMLParser
import re

from linkread_desktop.ai.models import NoteDocument, lexical_content
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.renderers.html import render_html


class Paragraphs(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.items = []
        self.current = None
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "p" and "reading-paragraph" in attrs.get("class", "").split():
            self.current = [attrs, ""]
            self.items.append(self.current)

    def handle_data(self, text):
        if self.current is not None:
            self.current[1] += text

    def handle_endtag(self, tag):
        if tag == "p":
            self.current = None


def test_raw_and_reorganized_reader_share_identity_without_losing_cues_or_source_symbols():
    raw = TranscriptDocument(
        source=SourceInfo(platform="youtube", video_id="1-Fo8McCD9w", title="来源标题"),
        transcript_source="subtitle",
        segments=[TranscriptSegment(start_ms=index * 1000, text=f"第 {index} 个片段，金额 ¥100，保留 😀。")
                  for index in range(12)],
    )
    note = NoteDocument.model_validate({
        "transcript": raw, "provider": "codex",
        "analysis": {
            "summary": "离线回归测试。",
            "reading_paragraphs": [
                {"start_segment_index": index, "end_segment_index": index + 2,
                 "text": "\n\n".join(segment.text for segment in raw.segments[index:index + 3])}
                for index in range(0, 12, 3)
            ],
        },
    })
    before = note.model_dump_json()
    original_html, complete_html = render_html(raw), render_html(note)
    assert note.model_dump_json() == before
    assert re.search(r'data-reading-key="([^"]+)"', original_html)[1] == re.search(
        r'data-reading-key="([^"]+)"', complete_html)[1]
    original_text = lexical_content("".join(segment.text for segment in raw.segments))
    for markup in (original_html, complete_html):
        paragraphs = Paragraphs(markup).items
        assert lexical_content("".join(text for _, text in paragraphs)) == original_text
        covered = set()
        for attrs, _ in paragraphs:
            covered.update(range(int(attrs["data-cue-start"]), int(attrs["data-cue-end"]) + 1))
        assert covered == set(range(12))
        assert "LinkReadReadingPosition" in markup
        assert 'data-reading-resume role="status"' in markup
        assert 'data-reading-start-top>Start from top</button>' in markup
        assert '<script src=' not in markup
    # Splitting a display paragraph does not invent another source-cue span.
    complete = Paragraphs(complete_html).items
    assert complete[0][0]["data-cue-start"] == complete[1][0]["data-cue-start"] == "0"
    assert complete[0][0]["data-cue-end"] == complete[2][0]["data-cue-end"] == "2"
