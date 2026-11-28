"""Proofread before analysis finishes, then split at real semantic boundaries."""
import unicodedata

from .models import Chapter, NoteAnalysis, NoteDocument, ReadingParagraph, lexical_content, lexical_character, source_emphasis


def _is_opening_punct(char: str) -> bool:
    return unicodedata.category(char) in {"Ps", "Pi"} or char in "([{"


def _safe_split_cut(text: str, pos: int, prev: int) -> int:
    """Keep opening brackets/quotes attached to the following segment rather than trailing the prior one."""
    cut = pos
    while cut > prev:
        ch = text[cut - 1]
        if _is_opening_punct(ch):
            cut -= 1
            continue
        if ch in {'"', "'"}:
            before = text[cut - 2] if cut - 2 >= prev else ""
            if not before or before.isspace() or before in "。！？.!?,，、；;：:" or unicodedata.category(before) in {"Ps", "Pi", "Pe", "Pf"}:
                cut -= 1
                continue
        break
    return cut


def reading_seed(raw, provider):
    # Transient context only; never written as the article's analysis.
    # Keep a short placeholder chapter title so every block does not repeat a long video title.
    declared = (raw.language or "").lower().replace("_", "-").removeprefix("ai-")
    label = "Full" if (declared == "en" or declared.startswith("en-")) else "全文"
    return NoteDocument(transcript=raw, provider=provider.name,
        analysis=NoteAnalysis(summary="等待内容提炼", chapters=[Chapter(
            start_segment_index=0, start_ms=raw.segments[0].start_ms,
            title=label, summary=label)]))


def attach_reading(note: NoteDocument, polished: NoteDocument) -> NoteDocument:
    if note.transcript != polished.transcript:
        raise ValueError("校对结果与原始字幕不一致")
    segments = note.transcript.segments
    chapters = {x.start_segment_index for x in note.analysis.chapters}
    paragraphs = []
    for paragraph in polished.analysis.reading_paragraphs:
        a, b = paragraph.start_segment_index, paragraph.end_segment_index
        boundaries = [a, *sorted(i for i in chapters if a < i <= b), b + 1]
        # Every cut refers to original cue boundaries. Consume the same number
        # of lexical characters; punctuation is retained beside its sentence.
        lexical_positions = [i for i, c in enumerate(paragraph.text) if lexical_character(c)]
        used, previous = 0, 0
        for start, end in zip(boundaries, boundaries[1:]):
            original = "".join(x.text for x in segments[start:end])
            seg_lex_len = len(lexical_content(original))
            if seg_lex_len == 0:
                cut = previous
                text = original
            else:
                used += seg_lex_len
                if used < len(lexical_positions):
                    cut = _safe_split_cut(paragraph.text, lexical_positions[used], previous)
                else:
                    cut = len(paragraph.text)
                text = paragraph.text[previous:cut].strip() or original
                previous = cut
            paragraphs.append(ReadingParagraph(
                start_segment_index=start, end_segment_index=end - 1, text=text,
                edited=paragraph.edited, subheading=(paragraph.subheading if start == a and start not in chapters else None),
                bold=source_emphasis(paragraph.bold, text, limit=1), red=source_emphasis(paragraph.red, text, limit=1)))
    result = note.model_copy(deep=True)
    result.analysis.reading_paragraphs = paragraphs
    result.prompt_version = "2"
    return NoteDocument.model_validate(result.model_dump())
