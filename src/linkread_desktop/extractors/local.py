"""Local file extraction. These paths work fully offline."""

import json
from pathlib import Path

from pydantic import ValidationError

from ..errors import ExtractionError, UnsupportedSourceError
from ..models import SourceInfo, TranscriptDocument
from ..parsers import parse_bilibili_json, parse_srt_vtt
from ..progress import ProgressCallback, report


def extract_local(path: Path, *, progress: ProgressCallback | None = None) -> TranscriptDocument:
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
    segments = parse_bilibili_json(content) if suffix == ".json" else parse_srt_vtt(content)
    report(progress, "parsed", f"字幕解析完成：{len(segments)} 个片段")
    return TranscriptDocument(
        source=SourceInfo(platform="local", title=path.stem),
        transcript_source="local",
        segments=segments,
    )
