"""Read optional presentation data for the html-library article template."""

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..errors import LinkReadError
from ..models import TranscriptDocument
from ..quotes import QuoteCards
from ..storage import write_text
from .catalog import CatalogMetadata, load_catalog
from .reading_state import reading_state_key


_HEADING = re.compile(r"(#{1,6})\s+(.+)")
MAX_READING_SECTIONS = 6


class ReadingSection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    start_segment_index: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=160)
    label: str | None = Field(default=None, min_length=1, max_length=60)
    summary: str = Field(min_length=1, max_length=400)


class ReadingOverview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    lead: str = Field(min_length=1, max_length=500)
    sections: list[ReadingSection] = Field(default_factory=list, max_length=MAX_READING_SECTIONS)
    source_note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_section_order(self) -> "ReadingOverview":
        if any(a.start_segment_index >= b.start_segment_index for a, b in zip(self.sections, self.sections[1:])):
            raise ValueError("章节起点需要按字幕顺序递增")
        return self


def mindmap_digest(markdown: str) -> str:
    return sha256(markdown.replace("\r\n", "\n").strip().encode("utf-8")).hexdigest()


def mindmap_source_digest(document: TranscriptDocument) -> str:
    """Bind optional navigation to source identity, original text and real times."""
    payload = {"key": reading_state_key(document), "segments": [
        segment.model_dump(mode="json", exclude_none=True) for segment in document.segments
    ]}
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def mindmap_paths(markdown: str) -> set[str]:
    paths, stack, counts = set(), [], {}
    for line in markdown.splitlines():
        heading = _HEADING.fullmatch(line.strip())
        if not heading:
            continue
        depth = len(heading[1])
        while len(stack) >= depth:
            stack.pop()
        if stack:
            parent = stack[-1]
            index = counts.get(parent, 0)
            path = f"{parent}.{index}"
            counts[parent] = index + 1
        else:
            path = "0"
        paths.add(path)
        stack.append(path)
    return paths


class MindmapLink(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(pattern=r"^0(?:\.[0-9]+){1,5}$")
    start_segment_index: int = Field(ge=0)


class MindmapLinks(BaseModel):
    """Optional, verified navigation; heading-only legacy maps have no mapping."""
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1] = 1
    map_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    links: list[MindmapLink] = Field(max_length=80)

    @model_validator(mode="after")
    def unique_paths(self) -> "MindmapLinks":
        if len({link.path for link in self.links}) != len(self.links):
            raise ValueError("思维导图来源索引不能重复")
        return self

    def verified(self, markdown: str, document: TranscriptDocument) -> dict[str, int]:
        # A manually edited map or a replaced transcript remains readable, but
        # must not inherit another version's source links.
        if self.map_hash != mindmap_digest(markdown) or self.source_hash != mindmap_source_digest(document):
            return {}
        paths = mindmap_paths(markdown)
        if any(link.path not in paths or link.start_segment_index >= len(document.segments) for link in self.links):
            return {}
        return {link.path: link.start_segment_index for link in self.links}


@dataclass(frozen=True)
class ArticleExtras:
    mindmap: str | None = None
    quotes: QuoteCards | None = None
    reading: ReadingOverview | None = None
    catalog: CatalogMetadata | None = None
    mindmap_links: MindmapLinks | None = None


def validate_mindmap(markdown: str) -> None:
    """A concise heading tree, with depth chosen independently per branch."""
    depths = []
    for line in markdown.splitlines():
        if not line.strip():
            continue
        heading = _HEADING.fullmatch(line.strip())
        if not heading:
            raise ValueError("思维导图只支持 Markdown 标题")
        depth, label = len(heading[1]), heading[2]
        if depth > (depths[-1] if depths else 0) + 1 or len(label) > 100:
            raise ValueError("标题层级不能跳级，每个节点最多 100 字")
        depths.append(depth)
    if len(depths) < 2 or depths[0] != 1 or depths.count(1) != 1:
        raise ValueError("思维导图需要一个根节点和至少一个分支")


def is_extra_json(path: Path) -> bool:
    sidecars = ("anki.json", "reading.json", "quotes.json", "catalog.json", "mindmap-links.json")
    return path.name in sidecars or path.name.endswith(tuple(f".{name}" for name in sidecars))


def _sidecar(source: Path, name: str) -> Path | None:
    for candidate in (source.with_suffix(f".{name}"), source.with_name(name)):
        if candidate.is_file():
            return candidate
    return None


def load_reading_overview(source: Path) -> ReadingOverview | None:
    reading_path = _sidecar(source, "reading.json")
    if not reading_path:
        return None
    try:
        return ReadingOverview.model_validate_json(reading_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise LinkReadError(f"文章摘要与目录文件无效：{reading_path}（{exc}）") from exc


def load_extras(source: Path) -> ArticleExtras:
    mindmap_path = _sidecar(source, "mindmap.md")
    quotes_path = _sidecar(source, "quotes.json")
    reading_path = _sidecar(source, "reading.json")
    links_path = _sidecar(source, "mindmap-links.json")
    mindmap = None
    quotes = None
    reading = None
    links = None
    if mindmap_path:
        try:
            mindmap = mindmap_path.read_text(encoding="utf-8-sig")
            validate_mindmap(mindmap)
        except (OSError, ValueError) as exc:
            raise LinkReadError(f"思维导图文件无效：{mindmap_path.name}") from exc
    if quotes_path:
        try:
            quotes = QuoteCards.model_validate_json(quotes_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise LinkReadError(f"金句文件无效：{quotes_path.name}") from exc
    if reading_path:
        try:
            reading = ReadingOverview.model_validate_json(reading_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise LinkReadError(f"文章摘要与目录文件无效：{reading_path.name}") from exc
    if links_path:
        try:
            links = MindmapLinks.model_validate_json(links_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            # Optional navigation must not make the original article unreadable.
            # Invalid data provides no links; it is never repaired by guessing.
            links = None
    return ArticleExtras(mindmap=mindmap, quotes=quotes, reading=reading, catalog=load_catalog(source),
                         mindmap_links=links)


def save_missing_extras(source: Path, previous: ArticleExtras, completed: ArticleExtras) -> None:
    """Keep existing sidecars and publish each new file only after validation."""
    for field, filename in (("reading", "reading.json"), ("mindmap", "mindmap.md"), ("quotes", "quotes.json"),
                            ("mindmap_links", "mindmap-links.json")):
        value = getattr(completed, field)
        if value is None or getattr(previous, field) is not None:
            continue
        content = value if isinstance(value, str) else value.model_dump_json(indent=2, exclude_none=True) + "\n"
        target = source.with_name(filename)
        try:
            write_text(target, content, replace=False)
        except FileExistsError:
            if not target.is_file():
                raise LinkReadError(f"文章数据路径不可用：{target}") from None
