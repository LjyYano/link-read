"""Subtitle parsers with no network or model dependencies."""

import html
import json
import math
import re

from .errors import ExtractionError, SubtitleUnavailableError
from .models import TranscriptSegment

_TIMESTAMP = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?$")
_VTT_TIMESTAMPS = re.compile(r"<\d{2}:\d{2}(?::\d{2})?\.\d{3}>")
_VTT_CLASS_TAG = re.compile(r"<c(?:\.[^>.\s]+)*>")
_VTT_META_BLOCK = re.compile(r"^(?:NOTE|STYLE|REGION)(?:\s|$)")
_TAGS = re.compile(r"</?[a-zA-Z][a-zA-Z0-9._-]*(?:\s[^>]*)?>")


def _milliseconds(value: str) -> int:
    match = _TIMESTAMP.fullmatch(value.strip())
    if not match:
        raise ExtractionError(f"无效的字幕时间戳：{value!r}")
    hours, minutes, seconds, fraction = match.groups()
    if int(seconds) >= 60 or (hours is not None and int(minutes) >= 60):
        raise ExtractionError(f"无效的字幕时间戳：{value!r}")
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int((fraction or "0").ljust(3, "0"))


def _plain_text(value: str) -> str:
    return " ".join(value.replace("\u00a0", " ").split())


def _text(value: str) -> str:
    value = _VTT_TIMESTAMPS.sub("", value)
    value = _TAGS.sub("", value)
    value = html.unescape(value).replace("\u00a0", " ")
    return " ".join(value.split())


def _iter_cues(content: str):
    normalized = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    for block in re.split(r"\n\s*\n", normalized):
        lines = [line.strip() for line in block.splitlines()]
        if not lines or _VTT_META_BLOCK.match(lines[0]):
            continue
        idx = next((i for i, line in enumerate(lines) if "-->" in line), -1)
        if idx < 0:
            continue
        left, right = lines[idx].split("-->", 1)
        end_token = right.strip().split()[0] if right.strip() else ""
        yield left, end_token, lines[idx + 1 :]


def has_rolling_caption_markers(content: str) -> bool:
    """Return True only when cue payloads carry deterministic YouTube auto-caption tags.

    Detection rules (deterministic only, no heuristic text comparison):
    - Inspects only cue payload lines after ``-->`` (ignoring ``WEBVTT`` headers
      and ``NOTE`` / ``STYLE`` / ``REGION`` blocks).
    - Matches an inline WebVTT cue timestamp tag (``<HH:MM:SS.mmm>`` or
      ``<MM:SS.mmm>``) or a WebVTT ``<c>`` / ``<c.class>`` class tag.
    """
    return any(
        _VTT_TIMESTAMPS.search(line) or _VTT_CLASS_TAG.search(line)
        for _, _, raw_lines in _iter_cues(content)
        for line in raw_lines
    )


def _line_overlap(previous_lines: list[str], cue_lines: list[str]) -> int:
    limit = min(len(previous_lines), len(cue_lines))
    for count in range(limit, 0, -1):
        if previous_lines[-count:] == cue_lines[:count]:
            return count
    return 0


def parse_srt_vtt(content: str, *, rolling: bool = False) -> list[TranscriptSegment]:
    """Read SRT and WebVTT cues, preserving all cues and their original timing.

    By default (``rolling=False``), every non-empty cue and its original
    timestamps are preserved intact, including overlapping dialogue. Set
    ``rolling=True`` only for rolling auto-caption streams (such as YouTube
    automatic captions), where overlapping snapshots, contiguous 10ms scroll
    frames, and two-line rolling windows are reduced to newly introduced text
    while non-overlapping spoken repetitions remain intact.
    """
    cues: list[TranscriptSegment] = []
    previous = ""
    previous_lines: list[str] = []
    previous_start = -1
    previous_end = -1
    previous_short = False
    for left, end_token, raw_lines in _iter_cues(content):
        start_ms = _milliseconds(left)
        end_ms = _milliseconds(end_token)
        if end_ms < start_ms:
            raise ExtractionError(f"字幕结束时间早于开始时间：{left.strip()!r} --> {end_token!r}")
        text = _text(" ".join(raw_lines))
        if not text:
            continue
        if rolling:
            original_text = text
            cue_lines = [cleaned for line in raw_lines if (cleaned := _text(line))]
            is_short = 0 < end_ms - start_ms <= 50
            overlaps_previous = previous_start <= start_ms < previous_end
            contiguous = previous_end >= 0 and previous_end <= start_ms <= previous_end + 10
            # YouTube rolling WebVTT emits ~10ms transition frames repeating the
            # bottom line right as it scrolls to the top line of the next cue.
            if contiguous and is_short and previous and (text == previous or previous.endswith(text)):
                previous = original_text
                previous_lines = cue_lines
                previous_end = max(previous_end, end_ms)
                previous_short = True
                continue
            if (overlaps_previous or (contiguous and previous_short)) and (text == previous or previous.endswith(text)):
                # A repeated snapshot can extend the display interval even when
                # it contributes no new words. Keep it for the next overlap check.
                previous_end = max(previous_end, end_ms)
                if is_short:
                    previous = original_text
                    previous_lines = cue_lines
                    previous_short = True
                continue
            # YouTube auto-caption cues often repeat all the previous words.
            # Never slice across the middle of an ASCII word ("art" vs "artificial").
            mid_word = (
                bool(previous) and len(text) > len(previous)
                and previous[-1].isascii() and previous[-1].isalnum()
                and text[len(previous)].isascii() and text[len(previous)].isalnum()
            )
            if ((overlaps_previous or (contiguous and previous_short))
                    and previous and text.startswith(previous) and len(text) > len(previous) and not mid_word):
                text = text[len(previous) :].strip()
                if not text:
                    previous = original_text
                    previous_lines = cue_lines
                    previous_start, previous_end, previous_short = start_ms, end_ms, is_short
                    continue
            elif (overlaps_previous or contiguous) and len(cue_lines) >= 2 and len(previous_lines) >= 2:
                # Two-line rolling window without an intermediate 10ms frame.
                overlap = _line_overlap(previous_lines, cue_lines)
                if overlap:
                    remaining = cue_lines[overlap:]
                    previous = original_text
                    previous_lines = cue_lines
                    previous_start, previous_end, previous_short = start_ms, end_ms, is_short
                    if not remaining:
                        continue
                    text = " ".join(remaining)
                    cues.append(TranscriptSegment(start_ms=start_ms, end_ms=end_ms, text=text))
                    continue
            previous = original_text
            previous_lines = cue_lines
            previous_start, previous_end, previous_short = start_ms, end_ms, is_short
        cues.append(TranscriptSegment(start_ms=start_ms, end_ms=end_ms, text=text))
    if not cues:
        raise SubtitleUnavailableError("字幕文件没有可识别的时间戳和内容")
    return sorted(cues, key=lambda item: item.start_ms)


def parse_bilibili_json(content: str) -> list[TranscriptSegment]:
    """Accept the Bilibili subtitle JSON format: {body:[{from,to,content}]}"""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ExtractionError("B 站字幕 JSON 格式错误") from exc
    if not isinstance(data, dict) or not isinstance(data.get("body"), list):
        raise ExtractionError("不是有效的 B 站字幕 JSON（缺少 body 数组）")
    segments = []
    for cue in data["body"]:
        try:
            if not isinstance(cue, dict):
                raise ValueError("cue is not an object")
            raw_from, raw_to = cue["from"], cue["to"]
            if isinstance(raw_from, bool) or isinstance(raw_to, bool):
                raise ValueError("timestamp cannot be boolean")
            start_sec = float(raw_from)
            end_sec = float(raw_to)
            if not (math.isfinite(start_sec) and math.isfinite(end_sec)):
                raise ValueError("timestamp must be finite")
            start_ms = round(start_sec * 1000)
            end_ms = round(end_sec * 1000)
            if start_ms < 0 or end_ms < start_ms:
                raise ValueError("invalid timestamp range")
            text = _plain_text(str(cue["content"]))
            if not text:
                continue
            segments.append(
                TranscriptSegment(
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=text,
                )
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ExtractionError("B 站字幕包含无效的时间戳或内容") from exc
    if not segments:
        raise SubtitleUnavailableError("B 站字幕为空")
    return sorted(segments, key=lambda item: item.start_ms)
