"""Publication manifest for pure-JSON desktop archives, with legacy article.html support."""

import json
from pathlib import Path
import time

from .errors import LinkReadError
from .storage import write_text

MANIFEST_FILENAME = "manifest.json"
LEGACY_ARTICLE_FILENAME = "article.html"
PUBLISHED_MARKER_NAMES = frozenset({MANIFEST_FILENAME, LEGACY_ARTICLE_FILENAME})


def published_article_marker(folder: Path) -> Path | None:
    """Return the published marker file for an archive folder, or None if unpublished.

    Prefers pure-JSON ``manifest.json`` and falls back to legacy ``article.html``
    so existing user libraries remain readable without migration. Symlinks on
    either marker are rejected.
    """
    if folder.is_symlink() or not folder.is_dir():
        return None
    manifest = folder / MANIFEST_FILENAME
    legacy = folder / LEGACY_ARTICLE_FILENAME
    if manifest.is_symlink() or legacy.is_symlink():
        raise LinkReadError("找不到这篇已生成的文章")
    if manifest.is_file():
        return manifest
    if legacy.is_file():
        return legacy
    return None


def write_manifest(folder: Path, *, has_analysis: bool = True, published_at: int | None = None) -> Path:
    """Atomically publish a pure-JSON archive manifest."""
    if folder.is_symlink() or not folder.is_dir():
        raise LinkReadError("文章归档目录不可用")
    target = folder / MANIFEST_FILENAME
    if target.is_symlink():
        raise LinkReadError("文章清单路径不可用")
    payload = {
        "version": 1,
        "document_type": "linkread_manifest",
        "publishedAt": int(time.time() * 1000) if published_at is None else int(published_at),
        "hasAnalysis": bool(has_analysis),
    }
    write_text(target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return target
