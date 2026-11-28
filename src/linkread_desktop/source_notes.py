"""Apply explicitly refreshed source descriptions without modifying transcripts."""

import json
import os
from pathlib import Path
import stat

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
    if not overlay.exists():
        return document
    raw = document.transcript if isinstance(document, NoteDocument) else document
    url = source_info_link(raw.source)
    key = video_source_key(url) if url else None
    try:
        descriptor = os.open(overlay, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SOURCE_NOTES_BYTES:
                raise ValueError("invalid source notes size/type")
            contents = stream.read(MAX_SOURCE_NOTES_BYTES + 1)
        if len(contents) > MAX_SOURCE_NOTES_BYTES:
            raise ValueError("source notes too large")
        value = json.loads(contents.decode("utf-8-sig"))
    except (OSError, ValueError, RecursionError) as exc:
        raise LinkReadError("来源简介文件损坏或不可读") from exc
    if (not isinstance(value, dict) or type(value.get("version")) is not int
            or value.get("version") != 1 or not key
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
