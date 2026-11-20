"""Subtitle parsers with no network or model dependencies."""

import html
import json
import re

from .errors import ExtractionError, SubtitleUnavailableError
from .models import TranscriptSegment

_TIMESTAMP = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?$")
_VTT_TIMESTAMPS = re.compile(r"<\d{2}:\d{2}(?::\d{2})?\.\d{3}>")
_TAGS = re.compile(r"<[^>]+>")


def _milliseconds(value: str) -> int:
    match = _TIMESTAMP.fullmatch(value.strip())
    if not match:
        raise ExtractionError(f"无效的字幕时间戳：{value!r}")
    hours, minutes, seconds, fraction = match.groups()
    if int(seconds) >= 60 or (hours is not None and int(minutes) >= 60):
        raise ExtractionError(f"无效的字幕时间戳：{value!r}")
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int((fraction or "0").ljust(3, "0"))


def _text(value: str) -> str:
    value = _VTT_TIMESTAMPS.sub("", value)
    value = _TAGS.sub("", value)
    value = html.unescape(value).replace("\u00a0", " ")
    return " ".join(value.split())


def parse_srt_vtt(content: str) -> list[TranscriptSegment]:
    """Read SRT and WebVTT cues, preserving their original timing.

    Overlapping auto-caption snapshots are suppressed or reduced to newly
    introduced text. Repetitions in non-overlapping cues remain intact.
    """
    content = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[TranscriptSegment] = []
    previous = ""
    previous_start = -1
    previous_end = -1
    for block in re.split(r"\n\s*\n", content):
        lines = [line.strip() for line in block.splitlines()]
        if not lines or lines[0].startswith(("NOTE", "STYLE", "REGION")):
            continue
        idx = next((i for i, line in enumerate(lines) if "-->" in line), -1)
        if idx < 0:
            continue
        left, right = lines[idx].split("-->", 1)
        end_token = right.strip().split()[0] if right.strip() else ""
        start_ms = _milliseconds(left)
        end_ms = _milliseconds(end_token)
        text = _text(" ".join(lines[idx + 1 :]))
        if not text:
            continue
        original_text = text
        overlaps_previous = previous_start <= start_ms < previous_end
        if overlaps_previous and (text == previous or previous.endswith(text)):
            # A repeated snapshot can extend the display interval even when
            # it contributes no new words. Keep it for the next overlap check.
            previous_end = max(previous_end, end_ms)
            continue
        # YouTube auto-caption cues often repeat all the previous words.
        if overlaps_previous and previous and text.startswith(previous) and len(text) > len(previous):
            text = text[len(previous) :].strip()
            if not text:
                continue
        cues.append(TranscriptSegment(start_ms=start_ms, end_ms=end_ms, text=text))
        previous = original_text
        previous_start, previous_end = start_ms, end_ms
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
            text = _text(str(cue["content"]))
            if not text:
                continue
            segments.append(
                TranscriptSegment(
                    start_ms=round(float(cue["from"]) * 1000),
                    end_ms=round(float(cue["to"]) * 1000),
                    text=text,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ExtractionError("B 站字幕包含无效的时间戳或内容") from exc
    if not segments:
        raise SubtitleUnavailableError("B 站字幕为空")
    return sorted(segments, key=lambda item: item.start_ms)
