"""JSON Lines interface for the local macOS app and its bundled executable."""

import argparse
from contextlib import ExitStack
from dataclasses import asdict
from hashlib import sha256
import json
import os
import shutil
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlsplit

from .ai.provider import make_provider
from .ai.checkpoints import provider_identity
from .asr.config import AsrConfig
from .asr.bailian import BailianAsr
from .metadata import canonical_video_url, video_metadata, thumbnail_url, shownotes_metadata
from .article_state import archive_mutation, file_lock, patch_reading_state, reading_state, save_reading_state
from .article_details import DETAILS_FILENAME, detail_value, linked_source_value, read_article_details, save_article_details
from .ai.models import NoteDocument
from .ai.openai_compatible import OpenAICompatibleProvider
from .covers import copy_local_cover, fetch_cover_bytes, find_local_cover, local_cover_url, remove_local_cover, save_cover_bytes
from .generation import _run_note, generate_article
from .errors import LinkReadError, error_context, error_details
from .links import canonical_link
from .messages import english_mode, localize_event
from .extractors.bilibili import BILIBILI_SHORT_HOSTS
from .extractors.local import extract_local
from .models import SourceInfo, TranscriptDocument
from .pipeline import extract as extract_source, video_source_key
from .progress import ProgressCallback, ProgressEvent, report
from .renderers.extras import load_extras
from .renderers.formatting import display_title, source_link, source_info_link
from .renderers.catalog import load_catalog, load_user_metadata
from .renderers.markdown import render_markdown
from .renderers.subtitles import SubtitleFormat, render_subtitles
from .renderers.html import render_html
from .renderers.reading_state import reading_state_key
from .renderers.shownotes import description_html
from .renderers.site import article_metadata, load_transcript, load_document
from .storage import write_text
from .source_notes import MAX_SOURCE_NOTES_BYTES, with_source_notes
from .workspace import find_workspace, _source_key
from .transcript_previews import load_preview, save_preview, prepare_preview, resume_preview, preview_folder, snapshot_id
from .translation import PageLanguage, load_edition
from .reader_cache import (CACHE_VERSION, LIBRARY_INDEX, archive_signature, implementation_signature,
                           cached_reader, remember_reader, read_cache, save_cache)


def _emit(kind: str, **fields: object) -> None:
    print(json.dumps({"type": kind, **localize_event(kind, fields)}, ensure_ascii=False, separators=(",", ":")), flush=True)


def _video_url(source: str) -> str:
    return canonical_link(source)


def _article_path(library: Path, article_id: str) -> Path:
    folder = library / article_id
    article = folder / "article.html"
    if (library.is_symlink() or not library.is_dir()
            or not article_id or article_id.startswith(".") or Path(article_id).name != article_id
            or folder.is_symlink() or article.is_symlink() or not article.is_file()):
        raise LinkReadError("找不到这篇已生成的文章")
    return article


def _article_source(article: Path) -> tuple[Path, TranscriptDocument | NoteDocument, TranscriptDocument]:
    transcript = article.with_name("transcript.json")
    if transcript.is_symlink():
        raise LinkReadError("原始字幕路径不可用")
    raw = load_transcript(transcript)
    note_path, note = _run_note(article.parent, raw)
    return (note_path, note, raw) if note is not None else (transcript, raw, raw)


def _source_metadata(raw: TranscriptDocument) -> dict:
    return _source_info_metadata(raw.source)


def _source_info_metadata(source: SourceInfo) -> dict:
    url = source_info_link(source)
    thumbnail = thumbnail_url(source.thumbnail_url)
    if source.platform == "youtube" and url:
        video_id = parse_qs(urlsplit(url).query)["v"][0]
        thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    return {
        "title": source.title,
        "platform": source.platform,
        "sourceUrl": url,
        "author": source.author,
        "publishedAt": source.published_at,
        "durationMs": source.duration_ms,
        "thumbnailUrl": thumbnail,
    }


def _article_record(article: Path, *, archived: tuple[Path, TranscriptDocument | NoteDocument, TranscriptDocument] | None = None) -> dict:
    source, document, raw = archived if archived is not None else _article_source(article)
    metadata = article_metadata(document, source, overview=load_extras(source).reading)
    appearance = _source_metadata(raw)
    details = read_article_details(article.parent) if raw.source.platform == "local" else None
    linked = details.get("linkedSource") if details else None
    if linked:
        appearance.update(sourceUrl=linked["url"], thumbnailUrl=linked["thumbnailUrl"])
    return {
        **appearance,
        "id": article.parent.name,
        "title": metadata["title"],
        "category": metadata["category"],
        "hasAnalysis": isinstance(document, NoteDocument),
        "updatedAt": int(article.stat().st_mtime * 1000),
        "readingKey": reading_state_key(raw),
        "localCover": local_cover_url(article.parent),
        **reading_state(article.parent),
    }


def _article_details_record(article: Path) -> dict:
    source, document, raw = _article_source(article)
    metadata = article_metadata(document, source, overview=load_extras(source).reading)
    generated = load_catalog(source, include_user=False)
    user = load_user_metadata(source)
    details = read_article_details(article.parent)
    linked = details.get("linkedSource") if details and raw.source.platform == "local" else None
    source_title = display_title(raw)
    return {
        "title": metadata["title"], "category": metadata["category"],
        "originalTitle": linked["title"] if linked else source_title,
        "originalCategory": generated.category if generated else "",
        "titleOverridden": details["title"] is not None if details is not None else bool(user.get("title")),
        "categoryOverridden": details["category"] is not None if details is not None else bool(user.get("category")),
        **({
            "linkedSourceUrl": linked["url"],
            "linkedThumbnailUrl": linked["thumbnailUrl"],
            "subtitleTitle": source_title,
        } if linked else {}),
    }


def _get_article_details(library: Path, article_id: str) -> dict:
    _article_path(library, article_id)
    with archive_mutation(library):
        article = _article_path(library, article_id)
        archive_signature(article.parent)
        return _article_details_record(article)


def _linked_appearance(url: str) -> dict[str, str]:
    try:
        preview = video_metadata(url)
        resolved = source_info_link(preview.source)
        key = video_source_key(url)
        if not resolved or (key and key != video_source_key(resolved)):
            raise LinkReadError("来源链接与返回的视频信息不匹配")
        if urlsplit(url).hostname in BILIBILI_SHORT_HOSTS and preview.source.platform != "bilibili":
            raise LinkReadError("B 站短链接返回了其他平台")
        if preview.source.title == preview.source.video_id:
            raise LinkReadError("来源未返回可用的标题")
        return linked_source_value({
            "url": canonical_link(resolved),
            "title": " ".join(preview.source.title.split())[:300].strip(),
            "thumbnailUrl": preview.thumbnail_url,
        })
    except LinkReadError as exc:
        raise LinkReadError("无法读取该链接的标题或封面，文章详情未保存。", code="linked_source_unavailable") from exc


def _update_article_details(library: Path, article_id: str, values: object) -> dict:
    if (not isinstance(values, dict) or not {"title", "category"} <= values.keys()
            or values.keys() - {"title", "category", "sourceUrl"}):
        raise LinkReadError("请同时提供标题和分类，恢复原始值时使用 null")
    title = detail_value(values["title"], "title")
    category = detail_value(values["category"], "category")
    _article_path(library, article_id)
    change_link = "sourceUrl" in values
    source_url = values.get("sourceUrl")
    signature = None
    linked = None
    fetched_cover = None
    if change_link and source_url is not None:
        if not isinstance(source_url, str) or len(source_url) > 4096:
            raise LinkReadError("请输入有效的视频或播客单集链接")
        source_url = source_url.strip()
        if source_url:
            source_url = canonical_link(source_url)
            with archive_mutation(library):
                article = _article_path(library, article_id)
                signature = archive_signature(article.parent)
                if _article_source(article)[2].source.platform != "local":
                    raise LinkReadError("只有本地字幕文章可以关联来源链接")
            linked = _linked_appearance(source_url)
            fetched_cover = fetch_cover_bytes(linked.get("thumbnailUrl")) if linked else None

    with archive_mutation(library):
        article = _article_path(library, article_id)
        current_signature = archive_signature(article.parent)
        if signature is not None and signature != current_signature:
            raise LinkReadError("文章已更新，请重新打开详情后保存。", code="linked_source_changed")
        archived = _article_source(article)
        source, _, raw = archived
        if change_link and raw.source.platform != "local":
            raise LinkReadError("只有本地字幕文章可以关联来源链接")
        if not change_link:
            previous = read_article_details(article.parent)
            linked = previous.get("linkedSource") if previous else None
        generated = load_catalog(source, include_user=False)
        record = _article_record(article, archived=archived)
        # Prepare the response before the atomic write. A later cache rebuild is
        # optional and must not turn a successful save into an apparent failure.
        default_title = linked["title"] if linked else display_title(raw)
        default_category = generated.category if generated else ""
        record.update(title=default_title if title is None else title,
                      category=default_category if category is None else category)
        if change_link:
            original = _source_metadata(raw)
            record.update(sourceUrl=linked["url"] if linked else original["sourceUrl"],
                          thumbnailUrl=linked["thumbnailUrl"] if linked else original["thumbnailUrl"])
        save_article_details(article.parent, title=title, category=category, linked_source=linked)
        if change_link:
            if fetched_cover is not None:
                save_cover_bytes(article.parent, *fetched_cover)
            else:
                remove_local_cover(article.parent)
        record["localCover"] = local_cover_url(article.parent)
        return record


def _article_markdown(library: Path, article_id: str, *, content: str = "full",
                      include_timestamped: bool = True) -> str:
    _article_path(library, article_id)
    with archive_mutation(library):
        article = _article_path(library, article_id)
        archive_signature(article.parent)
        source, document, raw = _reader_source(article)
        metadata = article_metadata(document, source, overview=load_extras(source).reading)
        return render_markdown(document, content=content, include_timestamped=include_timestamped,
                               title=metadata["title"], category=metadata["category"],
                               edition=load_edition(article.parent, raw))


def _valid_cached_record(record: object, article_id: str) -> bool:
    if not isinstance(record, dict) or record.get("id") != article_id:
        return False
    if set(record) != {"id", "title", "category", "hasAnalysis", "updatedAt", "isRead", "isStarred",
                       "platform", "sourceUrl", "author", "publishedAt", "durationMs", "thumbnailUrl",
                       "localCover", "readingKey"}:
        return False
    if not isinstance(record["platform"], str) or record["platform"] not in {"local", "youtube", "bilibili", "apple_podcasts"}:
        return False
    if any(type(record[key]) is not bool for key in ("hasAnalysis", "isRead", "isStarred")):
        return False
    if any(not isinstance(record[key], str) for key in ("title", "category")):
        return False
    if not isinstance(record["readingKey"], str) or not 0 < len(record["readingKey"]) <= 2048:
        return False
    if any(record[key] is not None and not isinstance(record[key], str)
           for key in ("sourceUrl", "author", "publishedAt", "thumbnailUrl")):
        return False
    local_cover = record["localCover"]
    if local_cover is not None and (
        not isinstance(local_cover, str)
        or not local_cover.startswith("linkread-reader://localhost/cover/")
        or len(local_cover) > 2048
    ):
        return False
    return (type(record["updatedAt"]) is int and record["updatedAt"] >= 0
            and (record["durationMs"] is None
                 or type(record["durationMs"]) is int and record["durationMs"] >= 0))


def _record_digest(record: dict) -> str:
    return sha256(json.dumps(record, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode()).hexdigest()


def _library_records(library: Path) -> dict:
    """Reparse changed archives only; every list still checks actual membership."""
    if not library.exists():
        return {"articles": [], "unavailable": 0}
    if library.is_symlink() or not library.is_dir():
        raise LinkReadError("资料库目录不可用")
    with ExitStack() as stack:
        writable = True
        try:
            stack.enter_context(archive_mutation(library))
        except OSError:
            # Read-only archives remain browsable; skip writing acceleration
            # metadata when the shared mutation lock cannot be acquired.
            writable = False
        path = library / LIBRARY_INDEX
        implementation = implementation_signature()
        cached = read_cache(path, limit=32 * 1024 * 1024)
        entries = (cached.get("entries") if cached and cached.get("version") == CACHE_VERSION
                   and cached.get("implementation") == implementation else None)
        if not isinstance(entries, dict):
            entries = {}
        updated = {}
        articles = []
        unavailable = 0
        for article in library.glob("*/article.html"):
            article_id = article.parent.name
            if article_id.startswith("."):
                continue
            try:
                article = _article_path(library, article_id)
                signature = archive_signature(article.parent)
                previous = entries.get(article_id)
                record = previous.get("record") if isinstance(previous, dict) else None
                if (isinstance(previous, dict) and previous.get("inputs") == signature
                        and _valid_cached_record(record, article_id)
                        and previous.get("digest") == _record_digest(record)):
                    entry = previous
                else:
                    record = _article_record(article)
                    # Other tools may edit an archive outside our mutation lock.
                    # Never attach a cache entry to a signature captured earlier.
                    after = archive_signature(article.parent)
                    if after != signature:
                        record = _article_record(article)
                        signature = after
                        if archive_signature(article.parent) != signature:
                            raise LinkReadError("文章正在更新，请稍后刷新")
                    entry = {"inputs": signature, "record": record, "digest": _record_digest(record)}
                updated[article_id] = entry
                articles.append(record)
            except (LinkReadError, OSError, ValueError):
                unavailable += 1
        index = {"version": CACHE_VERSION, "implementation": implementation, "entries": updated}
        if writable and index != cached:
            save_cache(path, index)
        articles.sort(key=lambda item: (item["updatedAt"], item["title"]), reverse=True)
        return {"articles": articles, "unavailable": unavailable}


def _reader_source(article: Path):
    """Use separately refreshed source notes without changing archived transcripts."""
    source, document, raw = _article_source(article)
    document = with_source_notes(document, article.parent)
    raw = with_source_notes(raw, article.parent)
    return source, document, raw


def _preview_record(library: Path, preview_id: str, raw: TranscriptDocument) -> dict:
    folder = preview_folder(library, preview_id)
    return {**_source_metadata(raw), "id": "preview-" + preview_id, "previewId": preview_id,
            "category": "", "hasAnalysis": False,
            "updatedAt": int((folder / "transcript.json").stat().st_mtime * 1000),
            "isRead": False, "isStarred": False}


def _prepared_reader(article: Path) -> dict:
    """Reuse a verified current reader; original HTML/JSON remain unchanged."""
    signature = archive_signature(article.parent)
    previous = cached_reader(article, signature)
    if previous is not None:
        return previous
    source, document, raw = _reader_source(article)
    extras = load_extras(source)
    metadata = article_metadata(document, source, overview=extras.reading)
    html = render_html(document, title=metadata["title"], category=metadata["category"],
                       description=metadata["description"], extras=extras,
                       reading_state=reading_state(article.parent), edition=load_edition(article.parent, raw))
    cached = article.with_name(".reader.html")
    if cached.is_symlink():
        raise LinkReadError("阅读缓存路径不可用")
    if archive_signature(article.parent) != signature:
        raise LinkReadError("文章正在更新，请稍后重新打开")
    write_text(cached, html)
    return remember_reader(article, signature, html, reading_state_key(document))


def _refresh_shownotes(article: Path) -> dict:
    _, _, raw = _article_source(article)
    url = source_info_link(raw.source)
    key = video_source_key(url) if url else None
    if not url or not key:
        raise LinkReadError("这篇文章没有可获取简介的原始来源", code="shownotes_source_missing")
    with error_context("shownotes"):
        source = shownotes_metadata(url)
    refreshed_url = source_info_link(source)
    if (source.platform != raw.source.platform or not refreshed_url
            or key != video_source_key(refreshed_url)):
        raise LinkReadError("来源简介与当前文章不匹配，未保存", code="shownotes_source_mismatch")
    if not source.description or not source.description.strip():
        return {"status": "empty"}
    overlay = article.with_name(".source-metadata.json")
    content = json.dumps({
        "version": 1, "sourceKey": key,
        "description": source.description, "descriptionFormat": source.description_format,
    }, ensure_ascii=False) + "\n"
    if len(content.encode("utf-8")) > MAX_SOURCE_NOTES_BYTES:
        raise LinkReadError("来源 Shownotes 过大，已保留现有简介", code="shownotes_too_large")
    if source.description_format == "html" and not description_html(source):
        return {"status": "empty"}
    with archive_mutation(article.parent.parent):
        _article_path(article.parent.parent, article.parent.name)
        archive_signature(article.parent)
        if _source_key(load_transcript(article.with_name("transcript.json"))) != key:
            raise LinkReadError("来源简介与当前文章不匹配，未保存", code="shownotes_source_mismatch")
        write_text(overlay, content)
        return {"status": "updated", **_prepared_reader(article)}



def _duplicate(source: str, library: Path, *, local_file: bool, session=None) -> dict:
    """Look up an existing completed article using the canonical source key."""
    if local_file:
        path = Path(source)
        if not path.is_absolute() or not path.is_file():
            raise LinkReadError("找不到字幕文件，请重新选择", code="source_missing", stage="extract", action="source")
        document = extract_local(path)
        key = _source_key(document)
    else:
        url = _video_url(source)
        if urlsplit(url).hostname in BILIBILI_SHORT_HOSTS:
            url = canonical_video_url(url, session=session)
        key = video_source_key(url)
        if key is None:
            raise LinkReadError("无法识别视频 ID，不能安全检查重复内容")
    folder = find_workspace(library, key) if key else None
    article = None
    if folder is not None and (folder / "article.html").is_file():
        article = _article_record(_article_path(library, folder.name))
    return {"key": key, "article": article}


def _recover_backups(library: Path) -> None:
    """Repair an interrupted two-rename publication on the next sidecar start."""
    backups = library / ".linkread-backups"
    if not backups.is_dir() or backups.is_symlink():
        return
    with archive_mutation(library):
        for backup in backups.iterdir():
            if backup.is_symlink() or not backup.is_dir():
                continue
            target = library / backup.name
            if not target.exists() and not target.is_symlink():
                backup.rename(target)
            # Keep already published articles. A remaining backup is not removed
            # here: the user may inspect it if a power loss happened after swap.


def _publish_replacement(generated: Path, library: Path) -> Path:
    """Install only a fully generated article; restore the old one on error."""
    if library.is_symlink() or not library.is_dir():
        raise LinkReadError("资料库目录不可用")
    with archive_mutation(library):
        return _publish_replacement_locked(generated, library)


def _publish_replacement_locked(generated: Path, library: Path) -> Path:
    if generated.name != "article.html":
        raise LinkReadError("生成的文章文件不可用")
    _article_path(generated.parent.parent, generated.parent.name)
    archive_signature(generated.parent)
    raw = load_transcript(generated.parent / "transcript.json")
    key = _source_key(raw)
    previous = find_workspace(library, key)
    destination = previous or library / generated.parent.name
    if generated.parent.resolve() == destination.resolve():
        raise LinkReadError("重新生成必须使用独立的暂存目录，已保留旧文章")
    backups = library / ".linkread-backups"
    backups.mkdir(exist_ok=True)
    if backups.is_symlink():
        raise LinkReadError("资料库备份目录不可用")
    backup = backups / destination.name
    if backup.exists() or backup.is_symlink():
        raise LinkReadError("发现未处理的旧版文章备份，请先检查资料库")
    if destination.exists():
        archive_signature(destination)
        old_source, _, _ = _article_source(destination / "article.html")
        new_source, _, _ = _article_source(generated)
        # Preserve every user metadata object, including fields the app does not
        # edit. The GUI overlay remains last in precedence after regeneration.
        inherited = {}
        for path in destination.iterdir():
            if path.name == "article.json" or path.name.endswith(".article.json") or path.name == DETAILS_FILENAME:
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
                    raise LinkReadError("文章信息文件不可用，已保留旧文章")
                try:
                    text = path.read_text(encoding="utf-8-sig")
                    if not isinstance(json.loads(text), dict):
                        raise ValueError("metadata must be an object")
                except (OSError, ValueError, RecursionError) as exc:
                    raise LinkReadError("文章信息文件无效，已保留旧文章") from exc
                inherited[path.name] = text
        read_article_details(destination)  # Do not publish past a corrupt GUI overlay.
        old_named = old_source.with_suffix(".article.json").name
        new_named = new_source.with_suffix(".article.json").name
        if old_named != new_named and old_named in inherited:
            if new_named in inherited:
                # The new source may already have an inactive legacy sidecar.
                # Retain its extra fields while carrying the effective values.
                try:
                    merged = {**json.loads(inherited[new_named]), **json.loads(inherited[old_named])}
                    inherited[new_named] = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
                except (ValueError, RecursionError) as exc:
                    raise LinkReadError("文章信息无法继承，已保留旧文章") from exc
            else:
                inherited[new_named] = inherited[old_named]
        for name, text in inherited.items():
            target = generated.parent / name
            if target.is_symlink() or target.exists() and not target.is_file():
                raise LinkReadError("文章信息目标不可用，已保留旧文章")
            write_text(target, text)
        save_reading_state(generated.parent, reading_state(destination))
        # A regeneration may use older cached subtitles. Keep explicitly fetched
        # source notes, even when that older transcript has a stale description.
        previous_metadata = destination / ".source-metadata.json"
        if previous_metadata.is_file() and not previous_metadata.is_symlink():
            _reader_source(destination / "article.html")  # Validate identity before carrying it over.
            write_text(generated.parent / ".source-metadata.json", previous_metadata.read_text(encoding="utf-8"))
        if find_local_cover(generated.parent) is None:
            copy_local_cover(destination, generated.parent)
        destination.rename(backup)
    try:
        generated.parent.rename(destination)
    except OSError:
        if backup.exists() and not destination.exists():
            backup.rename(destination)
        raise
    # The new page is already visible; an old backup cleanup error should not
    # be reported as an unsuccessful generation.
    if backup.exists():
        try:
            shutil.rmtree(backup)
        except OSError:
            pass
    return destination / "article.html"


def _youtube_auth_options(
    source: str, *, local_file: bool, cookies_from_browser: str | None = None, cookies_file: Path | None = None,
) -> dict:
    if local_file or not (video_source_key(source) or "").startswith("youtube-"):
        return {}
    options: dict = {}
    if cookies_from_browser:
        options["cookies_from_browser"] = cookies_from_browser
    if cookies_file is not None:
        options["cookies_file"] = cookies_file
    return options


def _download_subtitles(
    source: str, library: Path, *, cookies_from_browser: str | None = None, cookies_file: Path | None = None,
) -> dict:
    url = _video_url(source.strip())
    auth = _youtube_auth_options(url, local_file=False, cookies_from_browser=cookies_from_browser, cookies_file=cookies_file)
    with error_context("extract"):
        # Explicitly disable ASR: downloading subtitles must never download audio or incur transcription charges.
        raw = extract_source(url, asr=None, **auth)
    preview_id = save_preview(library, raw, source=url, local_file=False)
    return {
        "previewId": preview_id,
        "source": url,
        "sourceKey": _source_key(raw),
        "readingKey": reading_state_key(raw),
        "language": raw.language,
        "segmentCount": len(raw.segments),
        "metadata": _source_metadata(raw),
    }


def _export_subtitles(library: Path, target_id: str, *, fmt: SubtitleFormat, preview: bool) -> dict:
    if preview:
        _, raw = load_preview(library, target_id)
    else:
        _article_path(library, target_id)
        with archive_mutation(library):
            article = _article_path(library, target_id)
            _, _, raw = _article_source(article)
    return {
        "text": render_subtitles(raw, fmt),
        "title": display_title(raw),
        "format": fmt,
    }


def _generate_with_replacement(
    source: str, library: Path, progress: ProgressCallback, *,
    local_file: bool, replace: bool, provider, asr: AsrConfig | None = None, page_language: PageLanguage = "original",
    cookies_from_browser: str | None = None, cookies_file: Path | None = None,
) -> Path:
    # Files and videos share the same enrichment, polish and publication pipeline.
    # ASR is irrelevant to subtitle files and must never be invoked for them.
    options: dict = {"asr": asr, "asr_cache_dir": library / ".asr"} if asr and not local_file else {}
    options.update(_youtube_auth_options(
        source, local_file=local_file, cookies_from_browser=cookies_from_browser, cookies_file=cookies_file,
    ))
    duplicate = _duplicate(source, library, local_file=local_file)
    if duplicate["article"] is not None and not replace:
        raise LinkReadError("这篇文章已存在，请确认重新生成并替换")
    # Incomplete runs can leave a valid note.json even without article.html.
    # Never silently reuse an API-generated note when the user now selects Codex.
    identity = provider_identity(provider)
    if page_language != "original":
        identity = {**identity, "pageLanguage": page_language}
        options["page_language"] = page_language
    if not replace and provider is not None and duplicate["key"]:
        old_workspace = find_workspace(library, duplicate["key"])
        if old_workspace is not None:
            saved_identity = old_workspace / ".generation-identity.json"
            if saved_identity.is_symlink():
                raise LinkReadError("模型缓存配置文件不可用", code="cache_invalid")
            if saved_identity.is_file():
                try:
                    recorded_identity = json.loads(saved_identity.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    recorded_identity = None
            else:
                recorded_identity = None
            for name in ("note-polished.json", "note.json"):
                cached_file = old_workspace / name
                if cached_file.is_symlink():
                    raise LinkReadError("模型笔记缓存不可用", code="cache_invalid")
                if cached_file.is_file():
                    cached = load_document(cached_file)
                    if isinstance(cached, NoteDocument) and (cached.provider != provider.name or recorded_identity != identity):
                        report(progress, "provider_switch", "模型配置已变化或旧缓存身份无法确认，将使用当前 AI 工具独立生成")
                        replace = True
                        break
            if local_file and (old_workspace / "transcript.json").is_file():
                if (old_workspace / "transcript.json").is_symlink():
                    raise LinkReadError("原文缓存不可用", code="cache_invalid")
                if load_transcript(old_workspace / "transcript.json") != extract_local(Path(source)):
                    replace = True

    def persisted_progress(event):
        if event.stage == "saved_transcript" and event.work_dir is not None:
            target = Path(event.work_dir) / ".generation-identity.json"
            if target.is_symlink():
                raise LinkReadError("模型缓存配置文件不可用", code="cache_invalid")
            write_text(target, json.dumps(identity, ensure_ascii=False, sort_keys=True))
        progress(event)

    if not replace:
        return generate_article(source, provider, library=library, progress=persisted_progress, **options)

    library.mkdir(parents=True, exist_ok=True)
    stages = library / ".generation-staging"
    if library.is_symlink() or stages.is_symlink():
        raise LinkReadError("独立生成目录不可用", code="cache_invalid")
    stages.mkdir(mode=0o700, exist_ok=True)
    # Lock only the individual stage key outside stage_root so two processes do not
    # share one staging workspace, while never holding a global staging lock across
    # archive_mutation(library) calls (preventing cross-process AB-BA deadlocks).
    expected = extract_local(Path(source)) if local_file else None
    stage_key, value = _resolve_generation_stage(stages, duplicate["key"], identity, expected)
    with file_lock(stages / f".{stage_key}.lock"):
        stage_root = _prepare_generation_stage(stages, stage_key, value)
        report(progress, "replace", "正在独立生成新版本，复用同一来源及模型的已验证阶段；旧文章保留到完成")
        generated = generate_article(source, provider, library=stage_root, progress=persisted_progress, **options)
        report(progress, "replace_publish", "新版本已完成，正在替换旧文章")
        result = _publish_replacement(generated, library)
        # Interrupted/failed stages deliberately remain resumable. Only a
        # successful atomic publication retires this stage's metadata.
        try:
            (stage_root / "identity.json").unlink(missing_ok=True)
            stage_root.rmdir()
        except OSError:
            pass
        return result


def _stage_hash(value: dict) -> str:
    return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _resolve_generation_stage(
    stages: Path, source_key: str, identity: dict, expected: TranscriptDocument | None,
) -> tuple[str, dict]:
    value = {"version": 1, "sourceKey": source_key, "provider": identity}
    key = _stage_hash(value)
    if expected is not None:
        with file_lock(stages / f".{key}.lock"):
            stage = stages / key
            if stage.is_symlink():
                raise LinkReadError("独立生成目录不可用", code="cache_invalid")
            if stage.is_dir():
                existing = find_workspace(stage, source_key)
                if existing and (existing / "transcript.json").is_file():
                    if (existing / "transcript.json").is_symlink():
                        raise LinkReadError("独立生成原文缓存不可用", code="cache_invalid")
                    if load_transcript(existing / "transcript.json") != expected:
                        value = {**value, "transcriptId": snapshot_id(expected)}
                        key = _stage_hash(value)
    return key, value


def _prepare_generation_stage(stages: Path, key: str, value: dict) -> Path:
    stage = stages / key
    if stage.is_symlink():
        raise LinkReadError("独立生成目录不可用", code="cache_invalid")
    stage.mkdir(mode=0o700, exist_ok=True)
    manifest = stage / "identity.json"
    if manifest.is_symlink():
        raise LinkReadError("独立生成配置文件不可用", code="cache_invalid")
    if manifest.exists():
        try:
            existing = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LinkReadError("独立生成配置文件损坏", code="cache_invalid") from exc
        if existing != value:
            raise LinkReadError("独立生成缓存与当前来源或模型不匹配", code="cache_invalid")
    write_text(manifest, json.dumps(value, ensure_ascii=False, sort_keys=True))
    return stage


def _generation_stage(
    library: Path, source_key: str, identity: dict, expected: TranscriptDocument | None,
) -> Path:
    stages = library / ".generation-staging"
    if library.is_symlink() or stages.is_symlink():
        raise LinkReadError("独立生成目录不可用", code="cache_invalid")
    stages.mkdir(mode=0o700, exist_ok=True)
    key, value = _resolve_generation_stage(stages, source_key, identity, expected)
    return _prepare_generation_stage(stages, key, value)

def main() -> int:
    parser = argparse.ArgumentParser(description=("LinkRead desktop generation and offline reader" if english_mode()
                                                   else "LinkRead 桌面端生成和离线阅读工具"))
    parser.add_argument("--library", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("--provider", choices=("codex", "claude", "api"), default="codex")
    generate.add_argument("--base-url")
    generate.add_argument("--model")
    generate.add_argument("--replace", action="store_true")
    generate.add_argument("--executable")
    generate.add_argument("--local-file", action="store_true")
    generate.add_argument("--resume-preview")
    generate.add_argument("--page-language", choices=("original", "zh-CN", "en"), default="original",
                          help="Language of the generated reading page and transcript edition")
    generate.add_argument("--asr", action="store_true")
    generate.add_argument("--asr-base-url", default="https://dashscope.aliyuncs.com/api/v1")
    generate.add_argument("--asr-model", default="paraformer-v2")
    generate.add_argument("--asr-max-minutes", type=int, default=120)
    generate.add_argument("--asr-chunk-minutes", type=int, default=5)
    generate.add_argument("--ffmpeg-path", default="")
    generate.add_argument("--cookies-from-browser")
    generate.add_argument("--cookies", type=Path)
    generate.add_argument("source")
    check_api = commands.add_parser("test-api")
    check_api.add_argument("--base-url", required=True)
    check_api.add_argument("--model", required=True)
    duplicate = commands.add_parser("duplicate")
    duplicate.add_argument("--local-file", action="store_true")
    duplicate.add_argument("--resume-preview")
    duplicate.add_argument("source")
    commands.add_parser("validate-subtitle").add_argument("source")
    commands.add_parser("preview").add_argument("source")
    source_check = commands.add_parser("check-source")
    source_check.add_argument("--cookies-from-browser")
    source_check.add_argument("--cookies", type=Path)
    source_check.add_argument("source")
    download_sub = commands.add_parser("download-subtitles")
    download_sub.add_argument("--cookies-from-browser")
    download_sub.add_argument("--cookies", type=Path)
    download_sub.add_argument("source")
    export_sub = commands.add_parser("export-subtitles")
    export_sub.add_argument("--format", choices=("srt", "vtt"), default="srt")
    export_sub.add_argument("--preview", action="store_true")
    export_sub.add_argument("target_id")
    test_asr = commands.add_parser("test-asr")
    test_asr.add_argument("--base-url", required=True)
    test_asr.add_argument("--model", default="paraformer-v2")
    test_yt = commands.add_parser("test-youtube-cookies")
    test_yt.add_argument("--cookies-from-browser")
    test_yt.add_argument("--cookies", type=Path)
    commands.add_parser("library")
    markdown = commands.add_parser("markdown")
    markdown.add_argument("--content", choices=("readable", "full"), default="full")
    markdown.add_argument("--include-timestamped", choices=("true", "false"), default="true")
    markdown.add_argument("article_id")
    commands.add_parser("article-details").add_argument("article_id")
    details = commands.add_parser("update-article-details")
    details.add_argument("--values", required=True)
    details.add_argument("article_id")
    commands.add_parser("reader").add_argument("article_id")
    commands.add_parser("preview-reader").add_argument("preview_id")
    commands.add_parser("refresh-shownotes").add_argument("article_id")
    state = commands.add_parser("reading-state")
    state.add_argument("--read", choices=("true", "false"))
    state.add_argument("--starred", choices=("true", "false"))
    state.add_argument("article_id")
    args = parser.parse_args()
    library = args.library.expanduser()
    # Pop secret tokens from process env immediately so child processes (codex, claude, ffmpeg, yt-dlp) never inherit them.
    api_token = os.environ.pop("LINKREAD_API_TOKEN", None)
    asr_token = os.environ.pop("LINKREAD_ASR_TOKEN", None)

    try:
        if args.command in {"generate", "library"}:
            _recover_backups(library)
        if args.command == "generate":
            lock = Lock()

            def send_progress(event: ProgressEvent) -> None:
                with lock:
                    fields = {key: value for key, value in asdict(event).items()
                              if value is not None and key not in {"work_dir", "source"}}
                    if event.source is not None:
                        fields["metadata"] = _source_info_metadata(event.source)
                    if event.stage == "saved_transcript" and event.work_dir is not None:
                        raw = load_transcript(Path(event.work_dir) / "transcript.json")
                        fields["metadata"] = _source_metadata(raw)
                        fields["previewId"] = save_preview(library, raw, source=requested_source,
                                                           local_file=args.local_file)
                    _emit("progress", **fields)

            requested_source = args.source if args.local_file else _video_url(args.source.strip())
            source = requested_source
            if args.resume_preview:
                source = str(resume_preview(library, args.resume_preview, source=requested_source,
                                            local_file=args.local_file))
            local_input = args.local_file or bool(args.resume_preview)
            if local_input and (not Path(source).is_absolute() or not Path(source).is_file()):
                raise LinkReadError("找不到字幕文件，请重新选择", code="source_missing", stage="extract", action="source")
            with error_context("setup"):
                provider_kwargs = {"executable": args.executable, "model": args.model, "base_url": args.base_url}
                if args.provider == "api" and api_token is not None:
                    provider_kwargs["token"] = api_token
                provider = make_provider(args.provider, **provider_kwargs)
            article = _generate_with_replacement(
                source, library, send_progress, local_file=local_input,
                replace=args.replace, provider=provider, page_language=args.page_language,
                cookies_from_browser=args.cookies_from_browser, cookies_file=args.cookies,
                asr=(AsrConfig(base_url=args.asr_base_url, model=args.asr_model,
                               max_minutes=args.asr_max_minutes, chunk_minutes=args.asr_chunk_minutes,
                               token=asr_token, ffmpeg_path=args.ffmpeg_path) if args.asr else None))
            _emit("completed", output="article.html", article=_article_record(article))
        elif args.command == "preview":
            _emit("preview", **video_metadata(args.source).public())
        elif args.command == "check-source":
            from .source_readiness import check_source
            _emit("source_check", **check_source(
                args.source, cookies_from_browser=args.cookies_from_browser, cookies_file=args.cookies,
            ))
        elif args.command == "download-subtitles":
            _emit("subtitles_downloaded", **_download_subtitles(
                args.source, library, cookies_from_browser=args.cookies_from_browser, cookies_file=args.cookies,
            ))
        elif args.command == "export-subtitles":
            _emit("subtitles", **_export_subtitles(
                library, args.target_id, fmt=args.format, preview=args.preview,
            ))
        elif args.command == "test-asr":
            client = BailianAsr(AsrConfig(base_url=args.base_url, model=args.model, token=asr_token))
            segments = client.test_connection()
            _emit("connection", ok=True, message="已连接", sentences=len(segments))
        elif args.command == "test-youtube-cookies":
            from .extractors.youtube import test_youtube_cookies
            _emit("youtube_cookies_test", **test_youtube_cookies(
                cookies_from_browser=args.cookies_from_browser, cookies_file=args.cookies,
            ))
        elif args.command == "test-api":
            api = OpenAICompatibleProvider(base_url=args.base_url, model=args.model, token=api_token)
            api.test_connection()
            _emit("connection", ok=True, message="连接成功，模型和 Token 可用")
        elif args.command == "duplicate":
            source = args.source
            if args.resume_preview:
                source = str(resume_preview(library, args.resume_preview, source=source, local_file=args.local_file))
            _emit("duplicate", **_duplicate(source, library, local_file=args.local_file or bool(args.resume_preview)))
        elif args.command == "validate-subtitle":
            try:
                extract_local(Path(args.source))
            except (LinkReadError, ValueError, OverflowError) as exc:
                raise LinkReadError("无法识别粘贴的字幕，请检查格式和时间戳。", code="invalid_pasted_subtitle") from exc
            _emit("subtitle_validated")
        elif args.command == "library":
            _emit("library", **_library_records(library))
        elif args.command == "article-details":
            _emit("article_details", **_get_article_details(library, args.article_id))
        elif args.command == "update-article-details":
            _emit("article", **_update_article_details(library, args.article_id, json.loads(args.values)))
        elif args.command == "reading-state":
            article = _article_path(library, args.article_id)
            state = patch_reading_state(article.parent,
                is_read=None if args.read is None else args.read == "true",
                is_starred=None if args.starred is None else args.starred == "true")
            _emit("reading_state", **state)
        elif args.command == "reader":
            article = _article_path(library, args.article_id)
            with archive_mutation(library):
                prepared = _prepared_reader(article)
            _emit("reader", **prepared)
        elif args.command == "preview-reader":
            raw, revision = prepare_preview(library, args.preview_id)
            _emit("reader", previewId=args.preview_id, revision=revision, readingKey=reading_state_key(raw),
                  sourceKey=_source_key(raw), article=_preview_record(library, args.preview_id, raw))
        elif args.command == "refresh-shownotes":
            article = _article_path(library, args.article_id)
            _emit("shownotes", **_refresh_shownotes(article))
        else:
            _emit("markdown", text=_article_markdown(library, args.article_id, content=args.content,
                include_timestamped=args.include_timestamped == "true"))
    except (LinkReadError, OSError, ValueError) as exc:
        _emit("error", **error_details(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
