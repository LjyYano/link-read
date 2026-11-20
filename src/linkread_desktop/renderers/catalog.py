"""Persist bookshelf topics separately from raw transcripts and user overrides."""

from datetime import date, datetime
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..ai.models import NoteDocument
from ..article_details import read_article_details
from ..errors import ExtractionError, LinkReadError
from ..models import TranscriptDocument
from ..storage import write_text


class CatalogMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: str = Field(min_length=1)
    tags: list[str]
    date: str = ""
    date_source: Literal["published", "archived", "custom", ""] = ""

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        if value and _iso_date(value) != value:
            raise ValueError("date 必须为合法的 YYYY-MM-DD")
        return value


def _iso_date(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return ""
    try:
        date.fromisoformat(value)
    except ValueError:
        return ""
    return value


def _read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ExtractionError(f"文章元数据 JSON 无效：{path}") from exc
    if not isinstance(value, dict):
        raise ExtractionError(f"文章元数据必须是对象：{path}")
    return value


def load_user_metadata(source: Path) -> dict:
    """Merge article.json with filename-specific overrides without changing either."""
    result = {}
    for candidate in (source.with_name("article.json"), source.with_suffix(".article.json")):
        if candidate.is_file():
            result.update(_read_object(candidate))
    details = read_article_details(source.parent)
    if details is not None:
        for field in ("title", "category"):
            if details[field] is None:
                result.pop(field, None)
            else:
                result[field] = details[field]
    return result


def _catalog_file(source: Path) -> Path | None:
    for candidate in (source.with_suffix(".catalog.json"), source.with_name("catalog.json")):
        if candidate.is_file():
            return candidate
    return None


def load_catalog(source: Path, *, include_user: bool = True) -> CatalogMetadata | None:
    """Read generated topics, with explicit user fields taking precedence."""
    candidate = _catalog_file(source)
    values = {}
    if candidate:
        try:
            values = CatalogMetadata.model_validate(_read_object(candidate)).model_dump()
        except ValueError as exc:
            raise ExtractionError(f"书架分类与标签文件无效：{candidate}（{exc}）") from exc
    user = load_user_metadata(source) if include_user else {}
    category, tags = user.get("category"), user.get("tags")
    if category is not None:
        if not isinstance(category, str):
            raise ExtractionError(f"category 必须为字符串：{source}")
        if category.strip():
            values["category"] = category
    if tags is not None:
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ExtractionError(f"tags 必须是字符串数组：{source}")
        values["tags"] = tags  # An explicit [] deliberately clears tags.
    if user.get("date"):
        user_date = user["date"]
        if not isinstance(user_date, str) or _iso_date(user_date) != user_date:
            raise ExtractionError(f"date 必须为合法的 YYYY-MM-DD：{source}")
        values.update(date=user_date, date_source="custom")
    if not values.get("category") or "tags" not in values:
        return None
    try:
        return CatalogMetadata.model_validate(values)
    except ValueError as exc:
        raise ExtractionError(f"书架分类与标签文件无效：{source}（{exc}）") from exc


def _file_date(path: Path) -> str:
    try:
        stat = path.stat()
        # Birth time survives content rewrites on platforms that expose it.
        # Otherwise use the immutable transcript's mtime, never the current day.
        timestamp = getattr(stat, "st_birthtime", stat.st_mtime)
        return datetime.fromtimestamp(timestamp).date().isoformat()
    except (OSError, OverflowError, ValueError):
        return ""


def source_date(document: TranscriptDocument | NoteDocument, source: Path) -> tuple[str, str]:
    """Resolve a real publication date or the same transcript's archive date."""
    transcript = document.transcript if isinstance(document, NoteDocument) else document
    published = _iso_date((transcript.source.published_at or "")[:10])
    if published:
        return published, "published"
    archived = source
    candidate = source.with_name("transcript.json")
    if candidate != source and candidate.is_file():
        try:
            saved = TranscriptDocument.model_validate_json(candidate.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValueError):
            saved = None
        if saved == transcript:
            archived = candidate
    value = _file_date(archived)
    return value, "archived" if value else ""


def save_catalog(source: Path, catalog: CatalogMetadata, document: TranscriptDocument | NoteDocument) -> None:
    """Publish a complete generated sidecar once; user article.json stays untouched."""
    for candidate in (source.with_suffix(".catalog.json"), source.with_name("catalog.json")):
        if candidate.exists() or candidate.is_symlink():
            if candidate.is_symlink() or not candidate.is_file():
                raise LinkReadError(f"书架分类与标签路径不可用：{candidate}")
            return
    value, provenance = source_date(document, source)
    completed = CatalogMetadata(category=catalog.category, tags=catalog.tags, date=value, date_source=provenance)
    target = source.with_name("catalog.json")
    try:
        write_text(target, completed.model_dump_json(indent=2) + "\n", replace=False)
    except FileExistsError:
        if target.is_symlink() or not target.is_file():
            raise LinkReadError(f"书架分类与标签路径不可用：{target}") from None
