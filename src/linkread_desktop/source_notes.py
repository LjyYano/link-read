"""Apply explicitly refreshed source descriptions without modifying transcripts."""

import json
from pathlib import Path

from .ai.models import NoteDocument
from .errors import LinkReadError
from .models import TranscriptDocument
from .pipeline import video_source_key
from .renderers.formatting import source_info_link

MAX_SOURCE_NOTES_BYTES = 2 * 1024**2


def with_source_notes(document: TranscriptDocument | NoteDocument, folder: Path):
    overlay = folder / ".source-metadata.json"
    if overlay.is_symlink():
        raise LinkReadError("来源简介文件不可用")
    if not overlay.is_file():
        return document
    if overlay.stat().st_size > MAX_SOURCE_NOTES_BYTES:
        raise LinkReadError("来源简介文件过大")
    raw = document.transcript if isinstance(document, NoteDocument) else document
    url = source_info_link(raw.source)
    key = video_source_key(url) if url else None
    value = json.loads(overlay.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("version") != 1 or not key
            or value.get("sourceKey") != key
            or (value.get("description") is not None and not isinstance(value["description"], str))
            or value.get("descriptionFormat") not in (None, "text", "html")):
        raise LinkReadError("来源简介与当前文章不匹配")
    source = raw.source.model_copy(update={
        "description": value.get("description"),
        "description_format": value.get("descriptionFormat"),
    })
    raw = raw.model_copy(update={"source": source})
    return document.model_copy(update={"transcript": raw}) if isinstance(document, NoteDocument) else raw
