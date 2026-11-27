"""Proofread before analysis finishes, then split at real semantic boundaries."""
from .models import Chapter, NoteAnalysis, NoteDocument, ReadingParagraph, lexical_content, lexical_character, source_emphasis


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
            used += len(lexical_content(original))
            cut = lexical_positions[used] if used < len(lexical_positions) else len(paragraph.text)
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
