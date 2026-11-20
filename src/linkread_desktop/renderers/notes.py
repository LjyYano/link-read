"""Map analysis to the html-library summary, keyword and quote layouts."""

from html import escape

from ..ai.models import NoteDocument
from ..models import TranscriptDocument
from ..quotes import QuoteCard, context_bounds, cue_text
from .emphasis import render_emphasis
from .extras import ReadingOverview
from .formatting import display_title, source_link, time_label, video_link
from .templates import fill_template


def _panel(name: str, content: str) -> str:
    return (f'<section class="panel" id="panel-{name}" data-panel="{name}" '
            f'role="tabpanel" aria-labelledby="tab-{name}" hidden>'
            f'<div class="panel-inner">{content}</div></section>')


def overview_panel(overview: ReadingOverview) -> str:
    summary = [f'<p class="summary-lead">{escape(overview.lead)}</p>']
    summary.extend(
        f'<a class="summary-line" href="#chapter-{section.start_segment_index}" '
        f'data-go-section="chapter-{section.start_segment_index}" '
        f'title="Read section: {escape(section.title, quote=True)}">'
        f'<strong>{escape(section.label or section.title)}</strong>'
        f'<p>{escape(section.summary)}</p></a>' for section in overview.sections
    )
    if overview.source_note:
        summary.append(f'<p class="source-caveat">{escape(overview.source_note)}</p>')
    return _panel("summary", "".join(summary))


def note_fragments(note: NoteDocument) -> dict[str, str]:
    analysis = note.analysis
    panels = {}

    words = [word.strip() for word in analysis.keywords if word.strip()]
    if words:
        # These are editorial topics, not literal word-frequency measurements.
        # Use their existing order for typographic hierarchy without inventing counts.
        keywords = "".join(
            f'<button type="button" class="cloud-word cloud-tone-{index % 4}" '
            f'style="--weight:{1 / (1 + index * .28):.3f}" data-keyword="{escape(word, quote=True)}" '
            f'title="Search transcript: {escape(word, quote=True)}">{escape(word)}</button>'
            for index, word in enumerate(words)
        )
        content = (
            '<div class="keyword-atlas">'
            f'<div class="keyword-cloud" role="group" aria-label="Keywords">{keywords}</div>'
            '</div>'
        )
        panels["keywords"] = _panel("keywords", content)
    return panels


def note_quotes(note: NoteDocument) -> list[QuoteCard]:
    """Render existing exact excerpts with the same cards when no sidecar is present."""
    raw = note.transcript
    return [QuoteCard(
        title="Excerpt", start_segment_index=item.start_segment_index, end_segment_index=item.end_segment_index,
        front=item.text, back=cue_text(raw, *context_bounds(raw, item.start_segment_index, item.end_segment_index)),
    ) for item in note.analysis.highlights]


def highlights_panel(document: TranscriptDocument, cards: list[QuoteCard], *, title: str | None = None) -> str:
    rendered = []
    for index, card in enumerate(cards, 1):
        start_ms = document.segments[card.start_segment_index].start_ms
        end_ms = document.segments[card.end_segment_index].end_ms
        time = time_label(start_ms)
        if end_ms is not None:
            time += f"–{time_label(end_ms)}"
        rendered.append(fill_template("quote-card.html", {
            "COPY_TITLE": escape(title if title is not None else display_title(document), quote=True),
            "COPY_AUTHOR": escape(document.source.author or "", quote=True),
            "COPY_SOURCE": escape(source_link(document, start_ms) or "", quote=True),
            "COPY_SOURCE_LABEL": "Original episode" if document.source.platform == "apple_podcasts" else "Original video",
            "COPY_MENU_ID": f"quote-copy-menu-{index}",
            "TITLE": f"{index:02d} · {escape(card.title)}",
            "TIME": escape(time),
            "QUOTE_SOURCE": (f'<div class="small-time quote-source">{video_link(document, "Original episode ↗" if document.source.platform == "apple_podcasts" else "Original video ↗", start_ms)}</div>'
                             if source_link(document, start_ms) else ""),
            "FRONT": render_emphasis(card.front, card.bold, card.red),
            "BACK": render_emphasis(card.back, card.bold, card.red),
            "CUE": str(card.start_segment_index),
        }))
    return _panel("highlights", f'<div class="anki-cards">{"".join(rendered)}</div>')
