import pytest

from linkread_desktop.ai.models import Chapter, Highlight, NoteAnalysis, NoteDocument, ReadingParagraph, lexical_content
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.renderers.markdown import render_markdown


def note_document(*, paragraphs=True):
    transcript = TranscriptDocument(
        source=SourceInfo(platform="youtube", title="原始标题", video_id="1-Fo8McCD9w",
                          url="https://evil.example/pretend-source", author="作者",
                          description="来源的节目简介", description_format="text"),
        transcript_source="subtitle",
        segments=[TranscriptSegment(start_ms=i * 1000, end_ms=i * 1000 + 900, text=text)
                  for i, text in enumerate(("第一句保留", "第二句也保留", "最后一句仍然保留"))],
    )
    return NoteDocument(transcript=transcript, provider="codex", analysis=NoteAnalysis(
        summary="保存的内容摘要", takeaways=["保存的核心观点"], keywords=["保存的关键词"],
        chapters=[Chapter(start_segment_index=0, start_ms=0, title="第一章", summary="第一章导读"),
                  Chapter(start_segment_index=2, start_ms=2000, title="第二章", summary="第二章导读")],
        highlights=[Highlight(start_segment_index=0, end_segment_index=0, start_ms=0,
                              text=transcript.segments[0].text)],
        reading_paragraphs=[
            ReadingParagraph(start_segment_index=0, end_segment_index=1,
                             text="第一句保留。第二句也保留！", subheading="阅读小节"),
            ReadingParagraph(start_segment_index=2, end_segment_index=2, text="最后一句仍然保留。", edited=False),
        ] if paragraphs else [],
    ))


@pytest.mark.parametrize("content", ["readable", "full"])
@pytest.mark.parametrize("include_timestamped", [False, True])
def test_scope_and_subtitle_appendix_are_independent_and_never_drop_body(content, include_timestamped):
    note = note_document()
    before = note.model_dump_json()
    markdown = render_markdown(note, content=content, include_timestamped=include_timestamped)
    assert "## 摘要\n\n保存的内容摘要" in markdown
    assert "1 段沿用原字幕" in markdown
    for heading in ("核心观点", "章节导读", "金句", "关键词", "Shownotes"):
        assert (f"## {heading}\n" in markdown) == (content == "full")
    assert ("## 完整字幕\n" in markdown) == include_timestamped
    reading_body = markdown.split("\n---\n", 1)[1].split("\n---\n", 1)[0]
    for paragraph in note.analysis.reading_paragraphs:
        assert reading_body.count(paragraph.text) == 1
    assert "## 第一章\n" in reading_body and "## 第二章\n" in reading_body
    assert "### 阅读小节\n" in reading_body
    if include_timestamped:
        appendix = markdown.split("## 完整字幕\n", 1)[1]
        for index, cue in enumerate(note.transcript.segments):
            assert appendix.count(f"**[00:0{index}]** {cue.text}") == 1
    assert "https://www.youtube.com/watch?v=1-Fo8McCD9w" in markdown
    assert "evil.example" not in markdown
    assert note.model_dump_json() == before


def test_omitted_export_options_keep_the_existing_complete_cli_output():
    note = note_document()
    assert render_markdown(note) == render_markdown(note, content="full", include_timestamped=True)
    markdown = render_markdown(note)
    assert "保存的核心观点" in markdown and "来源的节目简介" in markdown and "## 完整字幕" in markdown


@pytest.mark.parametrize("content", ["readable", "full"])
def test_legacy_notes_without_reading_paragraphs_keep_every_cue(content):
    note = note_document(paragraphs=False)
    markdown = render_markdown(note, content=content, include_timestamped=False)
    body = markdown.split("\n---\n", 1)[1]
    body_text = "".join(line for line in body.splitlines() if not line.startswith("#"))
    assert lexical_content(body_text) == lexical_content("".join(cue.text for cue in note.transcript.segments))


def test_raw_long_local_transcript_has_no_fabricated_sections_or_local_path():
    raw = TranscriptDocument(
        source=SourceInfo(platform="local", title="本地字幕", url="file:///Users/private/captions.srt"),
        transcript_source="local",
        segments=[TranscriptSegment(start_ms=i * 1000, text=f"第 {i} 行字幕。") for i in range(1500)],
    )
    markdown = render_markdown(raw, content="readable", include_timestamped=False)
    body = markdown.split("## 阅读正文\n", 1)[1]
    assert lexical_content(body) == lexical_content("".join(cue.text for cue in raw.segments))
    assert all(label not in markdown for label in ("## 摘要", "## 核心观点", "## 完整字幕", "file://", "/Users/private"))


def test_asr_provenance_and_display_metadata_survive_reading_export():
    note = note_document()
    note.transcript.transcript_source = "asr"
    markdown = render_markdown(note, content="readable", include_timestamped=False,
                               title="显示 <标题>", category="产品 & 设计")
    assert markdown.startswith("# 显示 &lt;标题&gt;\n")
    assert "**分类**：产品 &amp; 设计" in markdown
    assert "来自语音识别" in markdown
    assert "## 完整字幕" not in markdown


@pytest.mark.parametrize("options", [{"content": "unknown"}, {"include_timestamped": "false"},
                                     {"include_timestamped": 0}])
def test_invalid_options_are_rejected(options):
    with pytest.raises(ValueError, match="无效的 Markdown"):
        render_markdown(note_document(), **options)
