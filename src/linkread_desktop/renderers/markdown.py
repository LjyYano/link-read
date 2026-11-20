"""Portable Markdown, with source-linked AI insights when available."""

from html import escape
from typing import Literal

from ..ai.models import NoteDocument
from ..ai.polish import group_source_blocks
from ..models import TranscriptDocument
from .formatting import display_title, duration_label, source_link, time_label, group_paragraphs
from .shownotes import description_markdown


def render_markdown(document: TranscriptDocument | NoteDocument, *,
                    content: Literal["readable", "full"] = "full",
                    include_timestamped: bool = True,
                    title: str | None = None, category: str | None = None) -> str:
    """CLI defaults remain complete; the GUI can request a clean reading copy.

    Both modes retain every paragraph of the reading transcript. The subtitle
    appendix is independent from whether analysis sections are included.
    """
    if content not in {"readable", "full"} or type(include_timestamped) is not bool:
        raise ValueError("无效的 Markdown 导出选项")
    note = document if isinstance(document, NoteDocument) else None
    document = note.transcript if note is not None else document
    title = (title if title is not None else display_title(document)).replace("\n", " ").strip()
    source = document.source
    metadata = [f"# {escape(title, quote=False)}", ""]
    if document.transcript_source == "asr":
        metadata.extend(["> 本文稿来自语音识别，可能存在识别误差；请结合原始音视频核对。", ""])
    fallback = sum(not paragraph.edited for paragraph in note.analysis.reading_paragraphs) if note else 0
    if fallback:
        metadata.extend([f"> {fallback} 段沿用原字幕：为避免 AI 改写，已保留原文。", ""])
    if source.author:
        metadata.append(f"- **作者**：{source.author}")
    if category:
        metadata.append(f"- **分类**：{escape(category, quote=False)}")
    if source.published_at:
        metadata.append(f"- **发布日期**：{source.published_at}")
    if source.duration_ms is not None:
        metadata.append(f"- **时长**：{duration_label(source.duration_ms)}")
    link = source_link(document)
    if link:
        source_label = "原节目" if source.platform == "apple_podcasts" else "原视频"
        metadata.append(f"- **{source_label}**：{link}")
    if note:
        analysis = note.analysis
        metadata.extend(["", "## 摘要", "", analysis.summary])
        if content == "full" and analysis.takeaways:
            metadata.extend(["", "## 核心观点", ""])
            metadata.extend(f"- {x}" for x in analysis.takeaways)
        if content == "full" and analysis.chapters:
            metadata.extend(["", "## 章节导读", ""])
            for chapter in analysis.chapters:
                clock = time_label(chapter.start_ms)
                metadata.extend([f"### [{clock}] {chapter.title}", "", chapter.summary, ""])
        if content == "full" and analysis.highlights:
            metadata.extend(["", "## 金句", ""])
            for highlight in analysis.highlights:
                metadata.append(f"> **[{time_label(highlight.start_ms)}]** {highlight.text}")
                metadata.append("")
        if content == "full" and analysis.keywords:
            metadata.extend(["", "## 关键词", "", "、".join(analysis.keywords)])
    shownotes = description_markdown(source)
    if content == "full" and shownotes:
        metadata.extend(["", "## Shownotes", "", shownotes])
    # The human-readable body is not a cue list: H1 video title, H2 chapters,
    # H3 topical subheadings, followed by a separately marked raw appendix.
    metadata.extend(["", "---", ""])
    if note and note.analysis.reading_paragraphs:
        blocks = [(p.start_segment_index, p.text, p.subheading)
                  for p in note.analysis.reading_paragraphs]
    elif note:
        blocks = [(p.start, p.text, None) for p in group_source_blocks(note)]
    else:
        blocks = [(i, p.text, None) for i, p in enumerate(group_paragraphs(document.segments))]
    headings = {c.start_segment_index: c.title for c in note.analysis.chapters} if note else {}
    if not headings:
        metadata.extend(["## 阅读正文", ""])
    for start, body, subheading in blocks:
        if start in headings:
            metadata.extend([f"## {headings[start]}", ""])
        if subheading:
            metadata.extend([f"### {subheading}", ""])
        for paragraph in body.split("\n\n"):
            if paragraph.strip():
                metadata.extend([paragraph.strip(), ""])
    if include_timestamped:
        metadata.extend(["---", "", "## 完整字幕", ""])
        for cue in document.segments:
            label = time_label(cue.start_ms)
            metadata.append(f"**[{label}]** {cue.text}")
            metadata.append("")
    return "\n".join(metadata).rstrip() + "\n"
