"""Immutable, offline transcript snapshots, separate from published articles."""

from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import time

from .article_state import archive_mutation
from .errors import LinkReadError
from .extractors.local import extract_local
from .links import canonical_link
from .models import TranscriptDocument
from .pipeline import video_source_key
from .renderers.html import render_html
from .translation import page_locale
from .renderers.site import load_transcript
from .storage import write_text
from .source_notes import with_source_notes
from .workspace import _source_key, find_workspace


PREVIEW_DIRECTORY = ".transcript-previews"
PREVIEW_ID = re.compile(r"[0-9a-f]{64}\Z")
MAX_PREVIEW_AGE_SECONDS = 14 * 24 * 3600


def cleanup_stale_previews(library: Path, *, max_age_seconds: int = MAX_PREVIEW_AGE_SECONDS) -> None:
    """Best-effort cleanup of old temporary preview snapshots (>14 days)."""
    root = library / PREVIEW_DIRECTORY
    if library.is_symlink() or root.is_symlink() or not root.is_dir():
        return
    cutoff = time.time() - max_age_seconds
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.is_symlink() or not entry.is_dir():
            continue
        if not PREVIEW_ID.fullmatch(entry.name):
            continue
        try:
            newest = entry.stat(follow_symlinks=False).st_mtime
            for child_name in ("request.json", "preview.html", "transcript.json"):
                child = entry / child_name
                if child.exists() and not child.is_symlink():
                    newest = max(newest, child.stat(follow_symlinks=False).st_mtime)
            if newest < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def snapshot_id(document: TranscriptDocument) -> str:
    return sha256(document.model_dump_json(exclude_none=True).encode("utf-8")).hexdigest()


def _unavailable() -> LinkReadError:
    return LinkReadError("找不到可读取的原文快照，请重新导入来源", code="preview_unavailable", action="source")


def _render_preview(library: Path, document: TranscriptDocument) -> str:
    # An explicitly refreshed publisher description can be shown without
    # changing the immutable transcript or any model checkpoint identity.
    archive = find_workspace(library, _source_key(document))
    visible = with_source_notes(document, archive) if archive is not None else document
    return render_html(visible, language=page_locale(document, "original"))


def preview_folder(library: Path, preview_id: str, *, create: bool = False) -> Path:
    if not isinstance(preview_id, str) or not PREVIEW_ID.fullmatch(preview_id):
        raise _unavailable()
    root = library / PREVIEW_DIRECTORY
    folder = root / preview_id
    if library.is_symlink() or root.is_symlink() or folder.is_symlink():
        raise _unavailable()
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder.mkdir(mode=0o700, exist_ok=True)
    if not folder.is_dir():
        raise _unavailable()
    for name in ("transcript.json", "request.json", "preview.html"):
        if (folder / name).is_symlink():
            raise _unavailable()
    return folder


def _binding(source: str, local_file: bool) -> dict:
    if local_file:
        path = Path(source)
        if not path.is_absolute():
            raise _unavailable()
        source = str(path)
    else:
        source = canonical_link(source)
    return {"source": source, "localFile": local_file}


def _read_manifest(folder: Path, document: TranscriptDocument) -> dict:
    path = folder / "request.json"
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ValueError()
        value = json.loads(path.read_text(encoding="utf-8"))
        if (value.get("version") != 1 or value.get("sourceKey") != _source_key(document)
                or not isinstance(value.get("requests"), list)
                or not all(isinstance(item, dict) and isinstance(item.get("source"), str)
                           and type(item.get("localFile")) is bool for item in value["requests"])):
            raise ValueError()
        return value
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise _unavailable() from exc


def load_preview(library: Path, preview_id: str) -> tuple[Path, TranscriptDocument]:
    folder = preview_folder(library, preview_id)
    path = folder / "transcript.json"
    if not path.is_file():
        raise _unavailable()
    document = load_transcript(path)
    if snapshot_id(document) != preview_id:
        raise LinkReadError("原文快照已被改动，已停止读取", code="preview_mismatch", action="source")
    _read_manifest(folder, document)
    return folder, document


def save_preview(library: Path, document: TranscriptDocument, *, source: str,
                 local_file: bool) -> str:
    """Publish a complete snapshot before reporting that a transcript is readable."""
    preview_id = snapshot_id(document)
    binding = _binding(source, local_file)
    if library.is_symlink():
        raise _unavailable()
    library.mkdir(parents=True, exist_ok=True)
    with archive_mutation(library):
        folder = preview_folder(library, preview_id, create=True)
        raw = folder / "transcript.json"
        if raw.exists():
            existing = load_transcript(raw)
            if snapshot_id(existing) != preview_id:
                raise _unavailable()
        else:
            write_text(raw, document.model_dump_json(indent=2, exclude_none=True) + "\n", replace=False)
        manifest = (_read_manifest(folder, document) if (folder / "request.json").exists()
                    else {"version": 1, "sourceKey": _source_key(document), "requests": []})
        if binding not in manifest["requests"]:
            # A snapshot can be imported from another local path, or a canonical
            # URL and its short link. Keep only server-observed bindings.
            manifest["requests"].append(binding)
            manifest["requests"] = manifest["requests"][-64:]
            write_text(folder / "request.json", json.dumps(manifest, ensure_ascii=False))
        if not (folder / "preview.html").is_file():
            write_text(folder / "preview.html", _render_preview(library, document))
    return preview_id


def prepare_preview(library: Path, preview_id: str) -> tuple[TranscriptDocument, str]:
    preview_folder(library, preview_id)  # Validate before even opening the lock.
    with archive_mutation(library):
        folder, document = load_preview(library, preview_id)
        # Re-render through the current deterministic renderer. Never trust
        # arbitrary HTML placed next to an otherwise valid snapshot.
        html = _render_preview(library, document)
        write_text(folder / "preview.html", html)
    return document, sha256(html.encode("utf-8")).hexdigest()[:20]


def resume_preview(library: Path, preview_id: str, *, source: str,
                   local_file: bool) -> Path:
    folder, document = load_preview(library, preview_id)
    manifest = _read_manifest(folder, document)
    binding = _binding(source, local_file)
    if local_file:
        # A moved/deleted input is recoverable only through a recorded binding.
        # A changed input must never silently resume an older transcript.
        path = Path(source)
        if binding not in manifest["requests"]:
            raise LinkReadError("原文快照与所选文件不匹配", code="preview_mismatch", action="source")
        if path.exists() and (not path.is_file() or snapshot_id(extract_local(path)) != preview_id):
            raise LinkReadError("字幕文件已变化，请重新导入，不能混用原文快照", code="preview_mismatch", action="source")
    else:
        key = video_source_key(binding["source"])
        if (key is not None and key != manifest["sourceKey"]) or (key is None and binding not in manifest["requests"]):
            raise LinkReadError("原文快照与当前单集不匹配", code="preview_mismatch", action="source")
    return folder / "transcript.json"
