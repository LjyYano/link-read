"""Map analysis to the html-library summary, keyword and quote layouts."""

from html import escape

from ..ai.models import NoteDocument
from ..models import TranscriptDocument
from ..quotes import QuoteCard, context_bounds, cue_text
from ..translation import ReadingEdition, join_translation
from .emphasis import render_emphasis
from .extras import ReadingOverview
from .formatting import display_title, source_link, time_label, video_link
from .labels import page_label
from .templates import fill_template


def _panel(name: str, content: str, *, active: bool = False) -> str:
    css = "panel active" if active else "panel"
    hidden = "" if active else " hidden"
    return (f'<section class="{css}" id="panel-{name}" data-panel="{name}" '
            f'role="tabpanel" aria-labelledby="tab-{name}"{hidden}>'
            f'<div class="panel-inner">{content}</div></section>')


def overview_panel(overview: ReadingOverview, *, language: str = "en", active: bool = False) -> str:
    summary = [f'<p class="summary-lead">{escape(overview.lead)}</p>']
    summary.extend(
        f'<a class="summary-line" href="#chapter-{section.start_segment_index}" '
        f'data-go-section="chapter-{section.start_segment_index}" '
        f'title="{page_label(language, "Read section: ")}{escape(section.title, quote=True)}">'
        f'<strong>{escape(section.label or section.title)}</strong>'
        f'<p>{escape(section.summary)}</p></a>' for section in overview.sections
    )
    if overview.source_note:
        summary.append(f'<p class="source-caveat">{escape(overview.source_note)}</p>')
    return _panel("summary", "".join(summary), active=active)


def note_fragments(note: NoteDocument, *, language: str = "en") -> dict[str, str]:
    analysis = note.analysis
    panels = {}

    words = [word.strip() for word in analysis.keywords if word.strip()]
    if words:
        # These are editorial topics, not literal word-frequency measurements.
        # Use their existing order for typographic hierarchy without inventing counts:
        # the size falls steeply with rank, so the cloud reads as a cloud, not as rows.
        keywords = []
        for index, word in enumerate(words):
            rank_weight = (1 + index * .6) ** -1.15
            weight = rank_weight * min(1, 22 / len(word))
            keywords.append(
                f'<button type="button" class="cloud-word cloud-tone-{index % 6}" '
                f'style="--weight:{weight:.3f}" data-keyword="{escape(word, quote=True)}" '
                f'title="{page_label(language, "Search article")}"><span class="cloud-label">{escape(word)}</span></button>'
            )
        content = (
            '<div class="keyword-atlas">'
            f'<div class="keyword-cloud" role="group" aria-label="{page_label(language, "Keywords")}">{"".join(keywords)}</div>'
            '</div>'
        )
        panels["keywords"] = _panel("keywords", content)
    return panels


def note_quotes(note: NoteDocument, *, language: str = "en") -> list[QuoteCard]:
    """Render existing exact excerpts with the same cards when no sidecar is present."""
    raw = note.transcript
    return [QuoteCard(
        title=page_label(language, "Excerpt"), start_segment_index=item.start_segment_index, end_segment_index=item.end_segment_index,
        front=item.text, back=cue_text(raw, *context_bounds(raw, item.start_segment_index, item.end_segment_index)),
    ) for item in note.analysis.highlights]


def highlights_panel(document: TranscriptDocument, cards: list[QuoteCard], *, title: str | None = None,
                     language: str = "en", edition: ReadingEdition | None = None) -> str:
    rendered = []
    for index, card in enumerate(cards, 1):
        start_ms = document.segments[card.start_segment_index].start_ms
        end_ms = document.segments[card.end_segment_index].end_ms
        time = time_label(start_ms)
        if end_ms is not None:
            time += f"–{time_label(end_ms)}"
        source_label = page_label(language, "Original episode" if document.source.platform == "apple_podcasts" else "Original video")
        translation = ""
        if edition and edition.cues is not None:
            excerpt = join_translation(edition.cues[card.start_segment_index:card.end_segment_index + 1], edition.language)
            translation = (f'<p class="quote-translation" lang="{edition.language}">'
                           f'<span>{page_label(language, "Translation")}</span>{escape(excerpt)}</p>')
        rendered.append(fill_template("quote-card.html", {
            "COPY_TITLE": escape(title if title is not None else display_title(document), quote=True),
            "COPY_AUTHOR": escape(document.source.author or "", quote=True),
            "COPY_SOURCE": escape(source_link(document, start_ms) or "", quote=True),
            "COPY_SOURCE_LABEL": source_label,
            "COPY_MENU_ID": f"quote-copy-menu-{index}",
            "TITLE": f"{index:02d} · {escape(card.title)}",
            "TIME": escape(time),
            "QUOTE_SOURCE": (f'<div class="small-time quote-source">{video_link(document, source_label + " ↗", start_ms)}</div>'
                             if source_link(document, start_ms) else ""),
            "FRONT": render_emphasis(card.front, card.bold, card.red),
            "BACK": render_emphasis(card.back, card.bold, card.red),
            "QUOTE_TRANSLATION": translation,
            "CUE": str(card.start_segment_index),
        }, language=language))
    return _panel("highlights", f'<div class="anki-cards">{"".join(rendered)}</div>')
