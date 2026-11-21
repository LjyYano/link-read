"""Render the html-library article layout with validated LinkRead content."""

from html import escape
from importlib.resources import files

from ..ai.models import NoteDocument
from ..ai.polish import group_source_blocks
from ..errors import LinkReadError
from ..models import TranscriptDocument
from ..quotes import QuoteCard, context_bounds
from .emphasis import render_emphasis
from .extras import ArticleExtras, ReadingSection
from .formatting import display_title, duration_label, group_paragraphs, source_link, video_link
from .notes import highlights_panel, note_fragments, note_quotes, overview_panel
from .icons import icon
from .overview import note_overview, section_title
from .reading_state import reading_state_key
from .shownotes import shownotes_panel
from .templates import fill_template, json_data


_LABELS = {"bilibili": "Bilibili", "youtube": "YouTube", "apple_podcasts": "Apple Podcasts"}


def _metadata(document: TranscriptDocument) -> str:
    src = document.source
    parts = [f"<span>{escape(src.author)}</span>"] if src.author else []
    if src.published_at:
        parts.append(f'<span title="Published">{escape(src.published_at[:10])}</span>')
    if src.duration_ms is not None:
        parts.append(f'<span title="Duration">{duration_label(src.duration_ms)}</span>')
    if source_link(document):
        parts.append(f'<span>{video_link(document, _LABELS[src.platform])}</span>')
    return '<div class="meta">' + "".join(parts) + '</div>' if parts else ""


def _reading_notice(note: NoteDocument | None) -> str:
    fallback = sum(not item.edited for item in note.analysis.reading_paragraphs) if note else 0
    return (f'<details class="callout reading-fallback"><summary>Original wording kept in {fallback} paragraph(s)</summary>'
            '<p>The punctuation pass changed some words, so these paragraphs retain the original text. '
            'Nothing was removed, but punctuation may still need attention. This checks wording, not factual accuracy.</p></details>'
            if fallback else "")


def _reading_paragraphs(document: TranscriptDocument, note: NoteDocument | None):
    if note and note.analysis.reading_paragraphs:
        for paragraph in note.analysis.reading_paragraphs:
            yield (paragraph.start_segment_index, paragraph.end_segment_index, paragraph.text,
                   paragraph.subheading, not paragraph.edited, paragraph.bold, paragraph.red)
        return
    if note:
        for block in group_source_blocks(note):
            yield block.start, block.end, block.text, None, False, [], []
        return
    start = 0
    for paragraph in group_paragraphs(document.segments):
        end = start + paragraph.segment_count - 1
        yield start, end, paragraph.text, None, False, [], []
        start = end + 1


def _article(
    document: TranscriptDocument, note: NoteDocument | None, chapters: list[ReadingSection],
    cards: list[QuoteCard],
) -> tuple[str, str]:
    next_chapter = 0
    sections = []
    toc = []
    marked = [(context_bounds(document, card.start_segment_index, card.end_segment_index), card)
              for card in cards if card.bold or card.red]
    for index, (start, end, text, subheading, fallback, bold, red) in enumerate(_reading_paragraphs(document, note), 1):
        # A stored paragraph is indivisible. Put a heading before the first
        # paragraph that reaches its source index, even for older notes.
        while next_chapter < len(chapters) and chapters[next_chapter].start_segment_index <= end:
            chapter = chapters[next_chapter]
            next_chapter += 1
            if sections:
                sections.append("</section>")
            target = f"chapter-{chapter.start_segment_index}"
            title = escape(section_title(next_chapter, chapter.title))
            sections.append(f'<section class="transcript-section" id="{target}"><h2>{title}</h2>')
            toc.append(f'<button type="button" data-go-section="{target}">{title}</button>')
        if not sections:
            sections.append('<section class="transcript-section" id="opening">')
        if subheading:
            sections.append(f"<h3>{escape(subheading)}</h3>")
        css = "reading-paragraph unpolished" if fallback else "reading-paragraph"
        for (first, last), card in marked:
            if start <= last and end >= first:
                bold = bold + card.bold
                red = red + card.red
        chunks = [chunk for chunk in text.split("\n\n") if chunk.strip()] or [text]
        for number, chunk in enumerate(chunks, 1):
            paragraph_id = f"para-{index}" if number == 1 else f"para-{index}-{number}"
            sections.append(
                f'<p class="{css}" id="{paragraph_id}" data-cue-start="{start}" data-cue-end="{end}">'
                f"{render_emphasis(chunk, bold, red, match_lexical=True)}</p>"
            )
    sections.append("</section>")
    return "\n".join(sections), "".join(toc)


def _tab(name: str, label: str) -> str:
    active = name == "transcript"
    css = "tab active" if active else "tab"
    return (f'<button type="button" class="{css}" id="tab-{name}" data-tab="{name}" '
            f'role="tab" aria-controls="panel-{name}" aria-selected="{str(active).lower()}" '
            f'tabindex="{0 if active else -1}">{icon(name)}<span>{label}</span></button>')


def render_html(
    document: TranscriptDocument | NoteDocument, *, home_href: str | None = None,
    category: str | None = None, description: str | None = None, extras: ArticleExtras | None = None,
    title: str | None = None, reading_state: dict | None = None,
) -> str:
    """Fill the bundled html-library article template for single-file offline use."""
    note = document if isinstance(document, NoteDocument) else None
    transcript = note.transcript if note else document
    title = title if title is not None else display_title(transcript)
    overview = extras.reading if extras else None
    if overview is None and note:
        overview = note_overview(note)
    chapters = overview.sections if overview else []
    if chapters and chapters[-1].start_segment_index >= len(transcript.segments):
        raise LinkReadError("文章目录的字幕索引超出当前文稿范围")
    if extras and extras.quotes:
        try:
            extras.quotes.verify_source(transcript)
        except ValueError as exc:
            raise LinkReadError(f"金句文件无效：{exc}") from exc
        cards = extras.quotes.cards
    else:
        cards = note_quotes(note) if note else []
    article, toc = _article(transcript, note, chapters, cards)
    panels = note_fragments(note) if note else {}
    if overview:
        panels["summary"] = overview_panel(overview)
    resources = files(__package__)
    mindmap_style = ""
    mindmap_scripts = ""
    if extras and extras.mindmap:
        links = extras.mindmap_links.verified(extras.mindmap, transcript) if extras.mindmap_links else {}
        panels["mindmap"] = fill_template("mindmap.html", {
            "MINDMAP_DATA": json_data(extras.mindmap), "MINDMAP_LINKS": json_data(links),
        })
        css = resources.joinpath("mindmap.css").read_text(encoding="utf-8")
        mindmap_style = f'<style id="yano-car-cost-mindmap-style">\n{css}</style>'
        vendor = resources.joinpath("vendor")
        notices = "\n\n".join(vendor.joinpath(f"{name}.LICENSE.txt").read_text(encoding="utf-8")
                              for name in ("d3", "markmap", "jsx-dom", "js-yaml"))
        library_sources = [vendor.joinpath(filename) for filename in (
            "d3.min.js", "markmap-lib.min.js", "markmap-view.min.js",
        )]
        libraries = "\n".join(f'<script>\n{resource.read_text(encoding="utf-8")}\n</script>'
                              for resource in library_sources)
        script = resources.joinpath("mindmap.js").read_text(encoding="utf-8")
        mindmap_scripts = (f'<!--\n{notices}\n-->\n{libraries}\n'
                           f'<script id="yano-car-cost-mindmap-init">\n{script}</script>')
    if cards:
        panels["highlights"] = highlights_panel(transcript, cards, title=title)
    shownotes = shownotes_panel(transcript.source, transcript=transcript)
    if shownotes:
        panels["shownotes"] = shownotes
    tabs = {
        "summary": "Summary", "mindmap": "Mindmap", "transcript": "Transcript", "keywords": "Keywords",
        "highlights": "Highlights", "shownotes": "Shownotes",
    }
    tabs_html = "".join(_tab(name, label) for name, label in tabs.items()
                        if name in panels or name == "transcript")
    home = escape(home_href, quote=True) if home_href is not None else None
    state = reading_state or {}
    read, starred = state.get("isRead") is True, state.get("isStarred") is True
    reading_actions = (
        f'<div class="reading-actions" data-reading-actions data-reading-key="{escape(reading_state_key(transcript), quote=True)}" '
        f'data-is-read="{str(read).lower()}" data-is-starred="{str(starred).lower()}">'
        f'<button type="button" data-reading-complete aria-pressed="{str(read).lower()}" '
        f'aria-label="{"Mark Unread" if read else "Mark Complete"}" '
        f'title="{"Mark Unread" if read else "Mark Complete"}">{icon("complete")}</button>'
        f'<button type="button" data-reading-star aria-pressed="{str(starred).lower()}">{icon("star")}'
        f'<span>{"Starred" if starred else "Star"}</span></button></div>'
    )
    return fill_template("reader.html", {
        "TITLE": escape(title),
        "DESCRIPTION": escape(description or (overview.lead if overview else title)),
        "HOME": f'<a href="{home}">Library</a>' if home else "<span>Library</span>",
        "SIDEBAR_HOME": f'<a href="{home}">Yano · Library</a>' if home else "Yano · Library",
        "CATEGORY": f'<span>/</span><span>{escape(category)}</span>' if category else "",
        "METADATA": _metadata(transcript),
        "READING_ACTIONS": reading_actions,
        "TABS": tabs_html,
        "SIDEBAR_TOC": (f'<div class="toc-label">Contents</div><nav class="toc" aria-label="Contents">{toc}</nav>'
                        if toc else ""),
        "MOBILE_TOC": (f'<details class="mobile-toc"><summary>Contents</summary><nav>{toc}</nav></details>'
                       if toc else ""),
        "READING_NOTICE": _reading_notice(note),
        "ARTICLE": article,
        "SUMMARY_PANEL": panels.get("summary", ""),
        "MINDMAP_PANEL": panels.get("mindmap", ""),
        "MINDMAP_STYLE": mindmap_style,
        "MINDMAP_SCRIPTS": mindmap_scripts,
        "KEYWORDS_PANEL": panels.get("keywords", ""),
        "HIGHLIGHTS_PANEL": panels.get("highlights", ""),
        "SHOWNOTES_PANEL": panels.get("shownotes", ""),
        "READER_SCRIPT": (resources.joinpath("reading-position.js").read_text(encoding="utf-8")
                          + "\n" + resources.joinpath("reader.js").read_text(encoding="utf-8")),
    })
