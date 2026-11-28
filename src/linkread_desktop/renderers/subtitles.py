"""Deterministic SRT and WebVTT export from verified TranscriptDocument cues."""

from typing import Literal

from ..models import TranscriptDocument

SubtitleFormat = Literal["srt", "vtt"]


def _format_timestamp(ms: int, *, decimal: str) -> str:
    value = max(0, int(ms))
    hours, rem = divmod(value, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{decimal}{millis:03d}"


def _clean_cue_text(text: str) -> str:
    return " ".join(text.replace("\r", " ").replace("\n", " ").split())


def _vtt_cue_text(text: str) -> str:
    cleaned = _clean_cue_text(text)
    return cleaned.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("-->", "--&gt;")


def render_subtitles(document: TranscriptDocument, fmt: SubtitleFormat = "srt") -> str:
    """Serialize every transcript segment using its original timestamps."""
    if fmt not in {"srt", "vtt"}:
        raise ValueError(f"Unsupported subtitle format: {fmt}")
    decimal = "," if fmt == "srt" else "."
    blocks: list[str] = []
    if fmt == "vtt":
        blocks.append("WEBVTT")
    for index, cue in enumerate(document.segments, 1):
        start = _format_timestamp(cue.start_ms, decimal=decimal)
        end_ms = cue.end_ms if cue.end_ms is not None and cue.end_ms >= cue.start_ms else cue.start_ms
        end = _format_timestamp(end_ms, decimal=decimal)
        text = _clean_cue_text(cue.text) if fmt == "srt" else _vtt_cue_text(cue.text)
        if fmt == "srt":
            blocks.append(f"{index}\n{start} --> {end}\n{text}")
        else:
            blocks.append(f"{start} --> {end}\n{text}")
    return "\n\n".join(blocks) + "\n"
