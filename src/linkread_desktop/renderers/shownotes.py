"""Render publisher descriptions without active HTML or external assets."""

from html import escape
from html.parser import HTMLParser
from bisect import bisect_right
import re
from urllib.parse import quote, urljoin, urlsplit

from ..models import SourceInfo, TranscriptDocument
from .formatting import source_info_link
from .labels import page_label


_TAGS = {"p", "div", "br", "hr", "ul", "ol", "li", "h2", "h3", "h4", "h5", "h6",
         "blockquote", "strong", "em", "u", "s", "code", "pre", "a"}
_ALIASES = {"b": "strong", "i": "em", "h1": "h2"}
_VOID = {"br", "hr"}
_DROP = {"script", "style", "iframe", "object", "embed", "svg", "math", "template", "noscript"}
_URL = re.compile(r"https?://[^\s<>\"'，。；！？、]+", re.IGNORECASE)
_TIMESTAMP = re.compile(r"(?<![0-9A-Za-z_:/+.=-])([0-9]{1,3}:[0-9]{2}(?::[0-9]{2})?)(?![0-9A-Za-z_:])")


class _Timeline:
    def __init__(self, document: TranscriptDocument):
        self.document = document
        self.starts = [segment.start_ms for segment in document.segments]
        self.ends, self.latest_cover = [], []
        longest_end, longest_index = -1, 0
        for index, segment in enumerate(document.segments):
            end = segment.end_ms
            if end is None:
                end = self.starts[index + 1] if index + 1 < len(self.starts) else segment.start_ms
            self.ends.append(end)
            if end >= longest_end:
                longest_end, longest_index = end, index
            self.latest_cover.append((longest_end, longest_index))

    def target(self, value: str) -> int | None:
        parts = [int(part) for part in value.split(":")]
        if len(parts) not in {2, 3} or parts[-1] >= 60 or len(parts) == 3 and parts[1] >= 60:
            return None
        seconds = (parts[0] * 60 + parts[1]) if len(parts) == 2 else (parts[0] * 3600 + parts[1] * 60 + parts[2])
        ms = seconds * 1000
        duration = self.document.source.duration_ms
        if duration is not None and ms >= duration:
            return None
        segments = self.document.segments
        index = bisect_right(self.starts, ms) - 1
        if index < 0:
            return 0  # Intro music: the first saved words are the useful target.
        if segments[index].start_ms == ms or ms < self.ends[index]:
            return index
        # An earlier overlapping speaker can still be talking. The prefix
        # coverage table avoids rescanning long transcripts for every timecode.
        longest_end, longest_index = self.latest_cover[index]
        if ms < longest_end:
            return longest_index
        # In a known silent gap, continue at the next real cue. Never invent
        # text after the final cue, even if the publisher gives a longer duration.
        return index + 1 if index + 1 < len(segments) else None


def _timestamp_markup(value: str, timeline: _Timeline | None) -> str:
    if timeline is None:
        return escape(value)
    pieces, start = [], 0
    for match in _TIMESTAMP.finditer(value):
        pieces.append(escape(value[start:match.start()]))
        target = timeline.target(match[0])
        label = escape(match[0])
        pieces.append(label if target is None else (
            f'<a class="shownotes-time" href="#cue-{target}" data-source-cue="{target}" '
            f'title="Read transcript at {label}">{label}</a>'
        ))
        start = match.end()
    pieces.append(escape(value[start:]))
    return "".join(pieces)


def _markdown_text(value: str) -> str:
    # Source prose is not executable Markdown. In particular a literal
    # [label](javascript:...) must not bypass the allowlisted HTML-link parser.
    return re.sub(r"([\\`*_{}\[\]#|!])", r"\\\1", escape(value, quote=False))


def _href(value: str, base: str | None) -> str | None:
    value = value.strip()
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    try:
        url = urljoin(base or "", value)
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        _ = parsed.port
        return url
    except ValueError:
        return None


def _link(url: str, label: str) -> str:
    return (f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer" '
            f'referrerpolicy="no-referrer">{label}</a>')


def _linkify(value: str, timeline: _Timeline | None = None) -> str:
    pieces, start = [], 0
    for match in _URL.finditer(value):
        url = match[0].rstrip(".,;:!?")
        # Keep balanced parentheses within URLs but exclude prose punctuation.
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1]
        end = match.start() + len(url)
        pieces.append(_timestamp_markup(value[start:match.start()], timeline))
        safe = _href(url, None)
        pieces.append(_link(safe, escape(url)) if safe else escape(url))
        start = end
    pieces.append(_timestamp_markup(value[start:], timeline))
    return "".join(pieces)


class _DescriptionHTML(HTMLParser):
    def __init__(self, base: str | None, timeline: _Timeline | None = None):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.timeline = timeline
        self.parts: list[str] = []
        self.text: list[str] = []
        self.stack: list[str] = []
        self.dropped: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self.dropped:
            if tag in _DROP and tag != "embed":
                self.dropped.append(tag)
            return
        if tag in _DROP:
            # embed is void and must not hide the rest of the description.
            if tag != "embed":
                self.dropped.append(tag)
            return
        tag = _ALIASES.get(tag, tag)
        if tag not in _TAGS:
            return
        if tag == "a":
            url = _href(dict(attrs).get("href") or "", self.base)
            self.parts.append((f'<a href="{escape(url, quote=True)}" target="_blank" '
                               'rel="noopener noreferrer" referrerpolicy="no-referrer">')
                              if url else "<span>")
            self.stack.append("a" if url else "span")
        else:
            self.parts.append(f"<{tag}>")
            if tag not in _VOID:
                self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and tag != "embed":
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.dropped:
            if tag == self.dropped[-1]:
                self.dropped.pop()
            return
        tag = _ALIASES.get(tag, tag)
        if tag == "a" and self.stack and self.stack[-1] == "span":
            tag = "span"
        if tag not in self.stack:
            return
        while self.stack:
            opened = self.stack.pop()
            self.parts.append(f"</{opened}>")
            if opened == tag:
                break

    def handle_data(self, data):
        if not self.dropped:
            # Existing publisher links retain their original destinations;
            # never nest anchors or treat examples in code as a timeline.
            timeline = None if any(tag in {"a", "span", "pre", "code"} for tag in self.stack) else self.timeline
            self.parts.append(_timestamp_markup(data, timeline))
            self.text.append(data)

    def finish(self) -> tuple[str, str]:
        self.close()
        while self.stack:
            self.parts.append(f"</{self.stack.pop()}>")
        return "".join(self.parts), "".join(self.text).strip()


def description_html(source: SourceInfo, *, transcript: TranscriptDocument | None = None) -> str:
    description = source.description
    if not description or not description.strip():
        return ""
    timeline = _Timeline(transcript) if transcript is not None else None
    if source.description_format == "html":
        parser = _DescriptionHTML(source_info_link(source), timeline)
        parser.feed(description)
        markup, text = parser.finish()
        return f'<div class="shownotes-copy">{markup}</div>' if text else ""
    return f'<div class="shownotes-copy shownotes-plain">{_linkify(description.strip(), timeline)}</div>'


class _DescriptionMarkdown(HTMLParser):
    """Convert our allowlisted HTML to portable Markdown, without raw HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[tuple[str, int]] = []
        self.lists: list[tuple[str, int]] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"p", "div", "blockquote", "pre"}:
            self.parts.append("\n\n")
        elif tag in {"h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n\n" + "#" * min(6, int(tag[1]) + 1) + " ")
        elif tag in {"ul", "ol"}:
            self.lists.append((tag, 0))
            self.parts.append("\n")
        elif tag == "li":
            marker = "- "
            if self.lists:
                kind, count = self.lists[-1]
                self.lists[-1] = (kind, count + 1)
                if kind == "ol":
                    marker = f"{count + 1}. "
            self.parts.append("\n" + "  " * max(0, len(self.lists) - 1) + marker)
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "hr":
            self.parts.append("\n\n---\n\n")
        elif tag == "a":
            self.links.append((dict(attrs).get("href", ""), len(self.parts)))

    def handle_endtag(self, tag):
        if tag in {"p", "div", "blockquote", "pre", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n\n")
        elif tag in {"ul", "ol"}:
            if self.lists:
                self.lists.pop()
            self.parts.append("\n\n")
        elif tag == "a" and self.links:
            href, start = self.links.pop()
            label = "".join(self.parts[start:])
            if href and label != _markdown_text(href):
                # Angle autolinks avoid an untrusted ')' terminating a Markdown
                # link destination. The URL has already passed the HTML filter.
                target = quote(href, safe=":/?#[]@!$&'()*+,;=%~")
                self.parts.append(f" <{target}>")

    def handle_data(self, text):
        self.parts.append(_markdown_text(text))


def description_markdown(source: SourceInfo) -> str:
    markup = description_html(source)
    if not markup:
        return ""
    parser = _DescriptionMarkdown()
    parser.feed(markup)
    parser.close()
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", "".join(parser.parts)).strip()


def shownotes_panel(source: SourceInfo, *, transcript: TranscriptDocument | None = None, language: str = "en") -> str:
    content = description_html(source, transcript=transcript)
    url = source_info_link(source)
    if not content and not url:
        return ""
    original = _link(url, page_label(language, "View original")) if url else ""
    if content:
        body = f'<div class="shownotes-source">{original}</div>{content}' if original else content
    else:
        body = (f'<div class="shownotes-empty"><p>{page_label(language, "No shownotes saved yet.")}</p>'
                f'<div class="shownotes-empty-actions">{original}'
                f'<button type="button" data-refresh-shownotes hidden>{page_label(language, "Fetch shownotes")}</button></div></div>')
    return ('<section class="panel" id="panel-shownotes" data-panel="shownotes" role="tabpanel" '
            f'aria-labelledby="tab-shownotes" hidden><div class="panel-inner">{body}</div></section>')
