"""Render an offline bookshelf using the html-library homepage template."""

from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re

from pydantic import ValidationError

from ..errors import ExtractionError
from ..models import TranscriptDocument
from ..ai.models import NoteDocument
from ..storage import write_text
from ..article_state import reading_state
from ..article_details import read_article_details
from ..source_notes import with_source_notes
from .catalog import load_catalog, load_user_metadata, source_date
from .extras import ReadingOverview, is_extra_json, load_extras
from .formatting import display_title
from .html import render_html
from ..translation import EDITION_FILE
from .reading_state import reading_state_key
from .templates import fill_template, json_data


PLATFORMS = {"bilibili": "Bilibili", "youtube": "YouTube", "apple_podcasts": "Apple Podcasts", "local": "本地文件"}
_DOC_ICON = ('<svg class="doc-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="1.45" aria-hidden="true"><path d="M7 3h7l5 5v12a1 1 0 0 1-1 1H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"/>'
             '<path d="M14 3v6h5M9 13h6M9 17h6"/></svg>')
_READ_ICON = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
              'aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 6-7"/></svg>')
_STAR_ICON = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
              'aria-hidden="true"><path d="m12 3 2.8 5.7 6.3.9-4.5 4.4 1 6.2-5.6-3-5.6 3 1-6.2L2 9.6l6.3-.9Z"/></svg>')


def load_document(path: Path) -> TranscriptDocument | NoteDocument:
    """Load a raw transcript or enriched note, never evaluate embedded content."""
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("JSON root must be an object")
        if value.get("document_type") == "linkread_note":
            return NoteDocument.model_validate(value)
        return TranscriptDocument.model_validate(value)
    except (OSError, UnicodeError, ValidationError, ValueError) as exc:
        raise ExtractionError(f"不是有效的 LinkRead TranscriptDocument / NoteDocument：{path.name}") from exc


def load_transcript(path: Path) -> TranscriptDocument:
    """Backwards-compatible raw-transcript-only reader."""
    try:
        return TranscriptDocument.model_validate_json(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValidationError, ValueError) as exc:
        raise ExtractionError(f"不是有效的 LinkRead TranscriptDocument v1.0：{path.name}") from exc


def article_metadata(
    document: TranscriptDocument | NoteDocument, source_file: Path, *, overview: ReadingOverview | None = None,
) -> dict:
    transcript = document.transcript if isinstance(document, NoteDocument) else document
    info = transcript.source
    details = read_article_details(source_file.parent)
    linked = details.get("linkedSource") if details and info.platform == "local" else None
    default_title = linked["title"] if linked else display_title(transcript)
    extra = load_user_metadata(source_file)
    catalog = load_catalog(source_file)
    tags = extra.get("tags")
    if tags is None:
        tags = catalog.tags if catalog else []
    if not isinstance(tags, list) or not all(isinstance(x, str) for x in tags):
        raise ExtractionError(f"tags 必须是字符串数组：{source_file}")
    normalized_date, date_source = source_date(document, source_file)
    if catalog and catalog.date:
        normalized_date, date_source = catalog.date, catalog.date_source or "custom"
    if extra.get("date"):
        # load_catalog validates explicit dates even with incomplete category/tags.
        normalized_date, date_source = extra["date"], "custom"
    description = document.analysis.summary[:155] if isinstance(document, NoteDocument) else default_title
    if overview:
        description = overview.lead
    category_override = extra.get("category")
    if isinstance(category_override, str):
        category_override = category_override.strip()
    category = (details["category"] if details is not None and details["category"] is not None
                else category_override or (catalog.category if catalog else ""))
    values = {
        "title": extra.get("title") or default_title,
        "description": extra.get("description") or description,
        "category": category,
        "tags": tags,
        "date": normalized_date,
        "date_source": date_source,
        "source": extra.get("source") or PLATFORMS[info.platform],
    }
    for key in ("title", "description", "category", "source"):
        if not isinstance(values[key], str):
            raise ExtractionError(f"{key} 必须为字符串：{source_file}")
    return values


def _slug(relative: Path) -> str:
    """Keep paths portable and collision-resistant for Chinese filenames and nesting."""
    name = relative.with_suffix("").as_posix().lower().replace("/", "-")
    ascii_slug = re.sub(r"[^a-z0-9_-]+", "-", name).strip("-_ ")[:55]
    suffix = sha256(relative.as_posix().encode("utf-8")).hexdigest()[:8]
    return f"{ascii_slug or 'article'}-{suffix}"


def _document_files(source: Path) -> list[Path]:
    if source.is_file():
        if source.suffix.lower() != ".json":
            raise ExtractionError("site 输入必须为 transcript.json 或包含它的目录")
        return [source]
    if not source.is_dir():
        raise ExtractionError(f"文件或目录不存在：{source}")
    result = sorted(
        path for path in source.rglob("*.json")
        if not any(part.startswith(".") for part in path.relative_to(source).parts)
        and path.is_file() and not is_extra_json(path)
        and path.name not in {"article.json", EDITION_FILE} and not path.name.endswith(".article.json")
    )
    if not result:
        raise ExtractionError(f"目录下没有 LinkRead 字幕 JSON：{source}")
    return result


def render_home(records: list[dict], *, language: str = "legacy") -> str:
    """Fill the html-library table template with local article links."""
    from .labels import SHELF_EN, ZH, page_label

    rows = []
    articles = []
    for item in records:
        metadata = item["metadata"]
        url = f"pages/{item['slug']}/index.html"
        href = escape(url, quote=True)
        title = escape(metadata["title"], quote=True)
        desc = escape(metadata["description"], quote=True)
        category = escape(metadata["category"], quote=True)
        category_badge = f'<span class="category">{category}</span>' if category.strip() else ""
        date_text = escape(metadata["date"] or "—")
        date_title = {"published": "发布日期", "archived": "归档日期", "custom": "文章日期"}.get(
            metadata.get("date_source", ""), "文章日期" if metadata["date"] else "日期未知")
        if language == "en":
            date_title = SHELF_EN[date_title]
        tags = "".join(f'<span class="tag">{escape(tag)}</span>' for tag in metadata["tags"][:4])
        key = item.get("readingKey", f"page:{item['slug']}")
        state = item.get("readingState", {"isRead": False, "isStarred": False})
        read, starred = state.get("isRead") is True, state.get("isStarred") is True
        read_label = page_label(language, "Mark Unread" if read else "Mark Complete")
        star_label = page_label(language, "Unstar" if starred else "Star")
        rows.append(f'''<tr class="record" tabindex="0" data-href="{href}">
          <td class="name-cell"><a class="document-link" href="{href}" title="{desc}">{_DOC_ICON}<span>{title}</span></a></td>
          <td class="category-cell">{category_badge}</td>
          <td class="tags-cell"><div class="tag-set">{tags}</div></td>
          <td class="reading-cell"><div class="reading-flags">
            <button type="button" class="reading-toggle" data-read aria-pressed="{str(read).lower()}" aria-label="{read_label}" title="{read_label}">{_READ_ICON}</button>
            <button type="button" class="reading-toggle star-toggle" data-star aria-pressed="{str(starred).lower()}" aria-label="{star_label}">{_STAR_ICON}</button>
          </div></td>
          <td><span class="date" title="{date_title}">{date_text}</span></td>
        </tr>''')
        articles.append({**metadata, "url": url, "readingKey": key, "readingState": state})
    return fill_template("home.html", {
        "PAGE_LANGUAGE": "en" if language == "en" else "zh-CN", "LABELS": json_data(ZH if language == "zh-CN" else {}),
        "COUNT": str(len(records)), "ROWS": "\n".join(rows), "ARTICLE_DATA": json_data(articles),
    }, language=language)


def build_site(source: Path, output_dir: Path, *, language: str = "legacy") -> int:
    """Validate every document before writing, then create home + article pages."""
    from ..translation import load_edition

    paths = _document_files(source)
    root = source.parent if source.is_file() else source
    records = []
    for path in paths:
        doc = with_source_notes(load_document(path), path.parent)
        relative = path.relative_to(root)
        transcript = doc.transcript if isinstance(doc, NoteDocument) else doc
        fingerprint = sha256(transcript.model_dump_json(exclude_none=True).encode("utf-8")).hexdigest()
        records.append({
            "document": doc,
            "path": path,
            "slug": _slug(relative),
            "parent": str(relative.parent),
            "fingerprint": fingerprint,
        })
    # When note.json and transcript.json describe the same source in one folder,
    # prefer the enriched note rather than duplicating the homepage record.
    # Keep distinct sources (including two different videos in the same folder).
    dedup = {}
    for record in records:
        doc = record["document"]
        key = (record["parent"], record["fingerprint"])
        previous = dedup.get(key)
        is_note = isinstance(doc, NoteDocument)
        previous_note = isinstance(previous["document"], NoteDocument) if previous else False
        if (previous is None or (is_note and not previous_note) or
                (is_note and previous_note and
                 len(doc.analysis.reading_paragraphs) >
                 len(previous["document"].analysis.reading_paragraphs))):
            dedup[key] = record
    records = list(dedup.values())
    for record in records:
        # Keep the canonical document for stable source identity and local notes.
        # Display titles are passed separately, never written into its transcript.
        extras = load_extras(record["path"])
        metadata = article_metadata(record["document"], record["path"], overview=extras.reading)
        record.update(metadata=metadata, extras=extras,
                      readingKey=reading_state_key(record["document"]),
                      readingState=reading_state(record["path"].parent))
    records.sort(key=lambda record: (record["metadata"]["date"], record["metadata"]["title"]), reverse=True)
    # The original validated transcript remains canonical and is not modified.
    # article.json matches the layout used by LjyYano/html-library.
    pages = []
    for record in records:
        folder = output_dir / "pages" / record["slug"]
        extras = record["extras"]
        metadata = record["metadata"]
        document = record["document"]
        transcript = document.transcript if isinstance(document, NoteDocument) else document
        html = render_html(document, home_href="../../index.html",
                           title=metadata["title"],
                           category=metadata["category"], description=metadata["description"],
                           extras=extras, reading_state=record["readingState"],
                           edition=load_edition(record["path"].parent, transcript))
        pages.append((folder, html, metadata))
    home = render_home(records, language=language)
    for folder, html, metadata in pages:
        write_text(folder / "index.html", html)
        write_text(folder / "article.json", json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    write_text(output_dir / "index.html", home)
    return len(records)
