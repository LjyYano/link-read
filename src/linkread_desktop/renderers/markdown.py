"""Portable Markdown, with source-linked AI insights when available."""

from html import escape
import re
from typing import Literal

from ..ai.models import NoteDocument
from ..ai.polish import group_source_blocks
from ..models import TranscriptDocument
from ..translation import ReadingEdition, page_locale, reading_translation
from .formatting import display_title, duration_label, source_link, time_label, group_paragraphs
from .shownotes import description_markdown

_EN = {
    "本文稿来自语音识别，可能存在识别误差；请结合原始音视频核对。": "This transcript was produced by speech recognition. Check uncertain wording against the original audio.",
    "{count} 段沿用原字幕：为避免 AI 改写，已保留原文。": "Original wording retained in {count} paragraph(s).",
    "作者": "Author", "分类": "Category", "发布日期": "Published", "时长": "Duration",
    "原节目": "Original episode", "原视频": "Original video", "摘要": "Summary", "核心观点": "Takeaways",
    "章节导读": "Chapters", "金句": "Highlights", "关键词": "Keywords", "节目简介": "Shownotes",
    "阅读正文": "Transcript", "完整字幕": "Full transcript", "原始字幕": "Original subtitles", "译文": "Translation",
}


def _single_line(value: str) -> str:
    return re.sub(r"[\r\n]+", " ", value).strip()


def render_markdown(document: TranscriptDocument | NoteDocument, *,
                    content: Literal["readable", "full"] = "full",
                    include_timestamped: bool = True,
                    title: str | None = None, category: str | None = None, edition: ReadingEdition | None = None) -> str:
    """CLI defaults remain complete; the GUI can request a clean reading copy.

    Both modes retain every paragraph of the reading transcript. The subtitle
    appendix is independent from whether analysis sections are included.
    """
    if content not in {"readable", "full"} or type(include_timestamped) is not bool:
        raise ValueError("无效的 Markdown 导出选项")
    note = document if isinstance(document, NoteDocument) else None
    document = note.transcript if note is not None else document
    if edition:
        edition.verify(document)
    language = page_locale(document, edition.language) if edition else "zh-CN"

    def label(text: str) -> str:
        return _EN.get(text, text) if language == "en" else text

    translations = edition.cues if edition else None
    colon = ": " if language == "en" else "："
    title = _single_line(title if title is not None else display_title(document))
    source = document.source
    metadata = [f"# {escape(title, quote=False)}", ""]
    if document.transcript_source == "asr":
        metadata.extend(["> " + label("本文稿来自语音识别，可能存在识别误差；请结合原始音视频核对。"), ""])
    fallback = sum(not paragraph.edited for paragraph in note.analysis.reading_paragraphs) if note else 0
    if fallback and translations is None:
        metadata.extend(["> " + label("{count} 段沿用原字幕：为避免 AI 改写，已保留原文。").format(count=fallback), ""])
    if source.author:
        metadata.append(f"- **{label('作者')}**{colon}{_single_line(source.author)}")
    if category:
        safe_category = _single_line(category)
        if safe_category:
            metadata.append(f"- **{label('分类')}**{colon}{escape(safe_category, quote=False)}")
    if source.published_at:
        metadata.append(f"- **{label('发布日期')}**{colon}{source.published_at}")
    if source.duration_ms is not None:
        metadata.append(f"- **{label('时长')}**{colon}{duration_label(source.duration_ms)}")
    link = source_link(document)
    if link:
        source_label = "原节目" if source.platform == "apple_podcasts" else "原视频"
        metadata.append(f"- **{label(source_label)}**{colon}{link}")
    if note:
        analysis = note.analysis
        metadata.extend(["", "## " + label("摘要"), "", analysis.summary])
        if content == "full" and analysis.takeaways:
            metadata.extend(["", "## " + label("核心观点"), ""])
            metadata.extend(f"- {x}" for x in analysis.takeaways)
        if content == "full" and analysis.chapters:
            metadata.extend(["", "## " + label("章节导读"), ""])
            for chapter in analysis.chapters:
                clock = time_label(chapter.start_ms)
                metadata.extend([f"### [{clock}] {chapter.title}", "", chapter.summary, ""])
        if content == "full" and analysis.highlights:
            metadata.extend(["", "## " + label("金句"), ""])
            for highlight in analysis.highlights:
                metadata.append(f"> **[{time_label(highlight.start_ms)}]** {highlight.text}")
                metadata.append("")
        if content == "full" and analysis.keywords:
            metadata.extend(["", "## " + label("关键词"), "", (", " if language == "en" else "、").join(analysis.keywords)])
    shownotes = description_markdown(source)
    if content == "full" and shownotes:
        metadata.extend(["", "## " + (label("节目简介") if edition else "Shownotes"), "", shownotes])
    # The human-readable body is not a cue list: H1 video title, H2 chapters,
    # H3 topical subheadings, followed by a separately marked raw appendix.
    metadata.extend(["", "---", ""])
    if note and note.analysis.reading_paragraphs:
        blocks = [(p.start_segment_index, p.end_segment_index, p.text, p.subheading)
                  for p in note.analysis.reading_paragraphs]
    elif note:
        blocks = [(p.start, p.end, p.text, None) for p in group_source_blocks(note)]
    else:
        blocks = []
        start = 0
        for paragraph in group_paragraphs(document.segments):
            end = start + paragraph.segment_count - 1
            blocks.append((start, end, paragraph.text, None))
            start = end + 1
    chapters = note.analysis.chapters if note else []
    if translations is not None:
        metadata.extend(["## " + label("译文"), ""])
    elif not chapters:
        metadata.extend(["## " + label("阅读正文"), ""])
    next_chapter = 0
    for start, end, body, subheading in blocks:
        while next_chapter < len(chapters) and chapters[next_chapter].start_segment_index <= end:
            metadata.extend([f"## {_single_line(chapters[next_chapter].title)}", ""])
            next_chapter += 1
        if subheading and translations is None:
            metadata.extend([f"### {_single_line(subheading)}", ""])
        if translations is not None:
            body = reading_translation(
                [cue.text for cue in document.segments[start:end + 1]],
                translations[start:end + 1], language, body,
            )
        for paragraph in body.split("\n\n"):
            if paragraph.strip():
                metadata.extend([paragraph.strip(), ""])
    if include_timestamped:
        metadata.extend(["---", "", "## " + label("原始字幕" if translations is not None else "完整字幕"), ""])
        for cue in document.segments:
            label = time_label(cue.start_ms)
            metadata.append(f"**[{label}]** {cue.text}")
            metadata.append("")
    return "\n".join(metadata).rstrip() + "\n"
