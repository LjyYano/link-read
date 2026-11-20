"""Small, atomic display overrides independent of archived source documents."""

import json
import os
from pathlib import Path
import stat

from .errors import LinkReadError
from .storage import write_text


DETAILS_FILENAME = ".article-details.json"
MAX_DETAILS_BYTES = 64 * 1024


def detail_value(value: object, field: str) -> str | None:
    """Null explicitly restores the source title / generated category."""
    if value is None:
        return None
    label, limit = ("标题", 300) if field == "title" else ("分类", 80)
    if not isinstance(value, str):
        raise LinkReadError(f"{label}必须为文字")
    value = value.strip()
    if (field == "title" and not value) or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise LinkReadError(f"{label}须为单行文字，最多 {limit} 个字符" + ("且不能为空" if field == "title" else ""))
    return value


def read_article_details(folder: Path) -> dict | None:
    """A corrupt or unsafe overlay is an error, never permission to replace it."""
    path = folder / DETAILS_FILENAME
    if not path.exists() and not path.is_symlink():
        return None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_DETAILS_BYTES:
                raise ValueError("invalid metadata file")
            contents = stream.read(MAX_DETAILS_BYTES + 1)
        if len(contents) > MAX_DETAILS_BYTES:
            raise ValueError("metadata is too large")
        value = json.loads(contents.decode("utf-8-sig"))
        if (not isinstance(value, dict) or type(value.get("version")) is not int
                or value["version"] != 1 or not {"title", "category"} <= value.keys()):
            raise ValueError("invalid metadata")
        for field in ("title", "category"):
            value[field] = detail_value(value[field], field)
        return value
    except (OSError, ValueError, RecursionError, LinkReadError) as exc:
        raise LinkReadError("无法读取已保存的文章信息，请检查资料库中的 .article-details.json") from exc


def save_article_details(folder: Path, *, title: str | None, category: str | None) -> None:
    """Caller holds the archive mutation lock; one rename publishes both fields."""
    previous = read_article_details(folder) or {}
    value = {**previous, "version": 1, "title": detail_value(title, "title"),
             "category": detail_value(category, "category")}
    try:
        contents = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    except (ValueError, RecursionError) as exc:
        raise LinkReadError("文章信息无法保存，原有数据未改动") from exc
    if len(contents.encode("utf-8")) > MAX_DETAILS_BYTES:
        raise LinkReadError("文章信息过大，未保存")
    write_text(folder / DETAILS_FILENAME, contents)
