"""Local file extraction. These paths work fully offline."""

import json
from pathlib import Path

from pydantic import ValidationError

from ..errors import ExtractionError, UnsupportedSourceError
from ..models import SourceInfo, TranscriptDocument
from ..parsers import has_rolling_caption_markers, parse_bilibili_json, parse_srt_vtt
from ..progress import ProgressCallback, report


def extract_local(path: Path, *, progress: ProgressCallback | None = None) -> TranscriptDocument:
    """Extract a local subtitle file (.srt, .vtt, LinkRead JSON, or Bilibili JSON).

    Preserves all cues and original timestamps by default so overlapping
    dialogue is never dropped. Rolling-caption deduplication is enabled only
    for ``.vtt`` files whose cue payload text contains deterministic YouTube
    auto-caption markers: inline WebVTT cue timestamp tags (``<HH:MM:SS.mmm>``
    or ``<MM:SS.mmm>``) or WebVTT ``<c>`` / ``<c.class>`` tags. Local ``.srt``
    files and plain ``.vtt`` files without these markers are never deduplicated.
    """
    suffix = path.suffix.lower()
    if suffix not in {".srt", ".vtt", ".json"}:
        raise UnsupportedSourceError("本地文件目前只支持 .srt、.vtt、LinkRead 和 B 站字幕 .json")
    report(progress, "reading", "正在读取字幕文件")
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ExtractionError(f"无法读取文件：{path}") from exc
    if suffix == ".json":
        try:
            data = json.loads(content)
        except ValueError as exc:
            raise ExtractionError(f"字幕 JSON 格式错误：{path}") from exc
        if isinstance(data, dict) and "body" not in data and ("segments" in data or "schema_version" in data):
            try:
                document = TranscriptDocument.model_validate(data)
            except ValidationError as exc:
                raise ExtractionError(f"不是有效的 LinkRead TranscriptDocument v1.0：{path}（{exc}）") from exc
            report(progress, "parsed", f"已读取 LinkRead 字幕：{len(document.segments)} 个片段")
            return document
    report(progress, "parse", "正在解析字幕时间戳和文本")
    if suffix == ".json":
        segments = parse_bilibili_json(content)
    else:
        rolling = suffix == ".vtt" and has_rolling_caption_markers(content)
        segments = parse_srt_vtt(content, rolling=rolling)
    report(progress, "parsed", f"字幕解析完成：{len(segments)} 个片段")
    return TranscriptDocument(
        source=SourceInfo(platform="local", title=path.stem),
        transcript_source="local",
        segments=segments,
    )
