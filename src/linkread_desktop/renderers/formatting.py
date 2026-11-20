"""Shared presentation helpers. The source transcript stays unmodified."""

from dataclasses import dataclass
from html import escape
import re
from urllib.parse import parse_qs, urlsplit

from ..models import SourceInfo, TranscriptDocument, TranscriptSegment

_YOUTUBE_ID = re.compile(r"[a-zA-Z0-9_-]{11}\Z")
_BILIBILI_ID = re.compile(r"BV[a-zA-Z0-9]{10}\Z")
_CJK_END = re.compile(r"[\u3400-\u9fff\u3000-\u303f]$")
_CJK_START = re.compile(r"^[\u3400-\u9fff\u3000-\u303f]")
_PUNCTUATION_START = re.compile(r"^[,.;!?，。！？；：、\)）】\]\"'”’]")
_LOCAL_EXPORT_SUFFIX = re.compile(r"_(?:哔哩哔哩_)?bilibili_BV[a-zA-Z0-9]{10}_字幕$", re.IGNORECASE)


def display_title(document: TranscriptDocument) -> str:
    """Hide recognized local-export bookkeeping without changing source metadata."""
    title = document.source.title
    if document.source.platform == "local":
        return _LOCAL_EXPORT_SUFFIX.sub("", title).strip() or title
    return title


@dataclass(frozen=True)
class Paragraph:
    start_ms: int
    text: str
    segment_count: int


def time_label(ms: int, *, always_hours: bool = False) -> str:
    """Format milliseconds as MM:SS or H:MM:SS. No timestamp rounding."""
    seconds = max(0, ms) // 1000
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours or always_hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def duration_label(ms: int | None) -> str | None:
    if ms is None:
        return None
    return time_label(ms)


def _glue(previous: str, next_text: str) -> str:
    if not previous or _PUNCTUATION_START.search(next_text):
        return ""
    if _CJK_END.search(previous) and _CJK_START.search(next_text):
        return ""
    return " "


def group_paragraphs(
    segments: list[TranscriptSegment], *, max_chars: int = 260, max_segments: int = 7,
    max_gap_ms: int = 12000,
) -> list[Paragraph]:
    """Deterministic reading paragraphs; the original cues are never changed.

    Split by length, cue count, sentence ending, or a long silence. All text
    still appears in chronological order in the complete subtitle view.
    """
    paragraphs: list[Paragraph] = []
    parts: list[str] = []
    chars = 0
    first_ms = 0
    previous_end = 0

    count = 0
    for cue in segments:
        if count and (
            count >= max_segments or chars + len(cue.text) >= max_chars or
            cue.start_ms - previous_end > max_gap_ms
        ):
            # Finalize the previous paragraph before starting a new one.
            if parts:
                paragraphs.append(Paragraph(first_ms, "".join(parts), count))
            parts, chars, count = [], 0, 0
        if not count:
            first_ms = cue.start_ms
        previous_text = "".join(parts[-1:]) if parts else ""
        sep = _glue(previous_text, cue.text)
        if sep:
            parts.append(sep)
        parts.append(cue.text)
        chars += len(sep) + len(cue.text)
        count += 1
        previous_end = cue.end_ms if cue.end_ms is not None else cue.start_ms
        if count >= 2 and chars >= 80 and cue.text.endswith(("。", "！", "？", ".", "!", "?")):
            paragraphs.append(Paragraph(first_ms, "".join(parts), count))
            parts, chars, count = [], 0, 0
    if count:
        paragraphs.append(Paragraph(first_ms, "".join(parts), count))
    return paragraphs


def source_link(document: TranscriptDocument, time_ms: int | None = None) -> str | None:
    """Construct trustworthy links using validated video IDs, never raw untrusted URLs."""
    return source_info_link(document.source, time_ms)


def source_info_link(source: SourceInfo, time_ms: int | None = None) -> str | None:
    """Build the same safe source link before any transcript is available."""
    if source.platform == "apple_podcasts":
        from ..errors import UnsupportedSourceError
        from ..links import apple_podcast_ids, canonical_link
        try:
            _, episode_id, _ = apple_podcast_ids(source.url or "")
            if source.video_id is not None and source.video_id != episode_id:
                return None
            # Apple does not document a portable seek parameter for episode pages.
            return canonical_link(source.url)
        except UnsupportedSourceError:
            return None
    if source.platform == "youtube":
        video_id = source.video_id
        if not video_id and source.url:
            parsed = urlsplit(source.url)
            if parsed.scheme == "https" and parsed.hostname in {"www.youtube.com", "youtube.com"}:
                video_id = parse_qs(parsed.query).get("v", [None])[0]
        if not video_id or not _YOUTUBE_ID.fullmatch(video_id):
            return None
        url = f"https://www.youtube.com/watch?v={video_id}"
        return url + (f"&t={time_ms // 1000}s" if time_ms is not None else "")
    if source.platform == "bilibili":
        video_id = source.video_id
        if not video_id or not _BILIBILI_ID.fullmatch(video_id):
            return None
        page = "1"
        if source.url:
            parsed = urlsplit(source.url)
            if parsed.scheme == "https" and parsed.hostname in {"www.bilibili.com", "bilibili.com", "m.bilibili.com"}:
                p = parse_qs(parsed.query).get("p", ["1"])[0]
                if p.isdecimal() and 0 < int(p) <= 1000:
                    page = p
        url = f"https://www.bilibili.com/video/{video_id}/?p={page}"
        return url + (f"&t={time_ms // 1000}" if time_ms is not None else "")
    return None


def video_link(document: TranscriptDocument, label: str, start_ms: int | None = None) -> str:
    href = source_link(document, start_ms)
    if not href:
        return escape(label)
    return (f'<a href="{escape(href, quote=True)}" target="_blank" '
            f'rel="noopener noreferrer" referrerpolicy="no-referrer">{escape(label)}</a>')
