"""Keep an HTML article overview compact even when a note has many chunks."""

import re

from ..ai.models import NoteDocument
from .extras import MAX_READING_SECTIONS, ReadingOverview, ReadingSection


_ORDINALS = "一二三四五六"
_CHAPTER_PREFIX = re.compile(r"^(?:[一二三四五六七八九十百零两\d]+[、.．]\s*|第[一二三四五六七八九十百零两\d]+[章节部分]+\s*[：:、.．]?\s*)")


def section_title(position: int, title: str) -> str:
    title = _CHAPTER_PREFIX.sub("", title).strip() or title
    return f"{_ORDINALS[position - 1]}、{title}"


def _excerpt(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    boundary = max(text.rfind(mark, 0, limit) for mark in "。！？!?")
    if boundary >= 0:
        return text[:boundary + 1]
    return text[:limit - 1].rstrip() + "…"


def note_overview(note: NoteDocument) -> ReadingOverview:
    analysis = note.analysis
    paragraphs = [text for part in analysis.summary.split("\n\n") if (text := part.strip())]
    lead = _excerpt(paragraphs[0] if paragraphs else note.transcript.source.title, 150) or "观点摘要"
    chapters = analysis.chapters
    # Spread the representative sections across the whole note instead of
    # taking its first chunk. The article itself still includes every paragraph.
    total = len(chapters)
    if total > MAX_READING_SECTIONS:
        chapters = [chapters[i * (total - 1) // (MAX_READING_SECTIONS - 1)] for i in range(MAX_READING_SECTIONS)]
    return ReadingOverview(lead=lead, sections=[
        ReadingSection(start_segment_index=chapter.start_segment_index, title=chapter.title.strip() or "章节",
                       label=_excerpt(chapter.title, 12) or None,
                       summary=_excerpt(chapter.summary, 115) or lead)
        for chapter in chapters
    ])
