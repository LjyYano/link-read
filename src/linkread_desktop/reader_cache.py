"""Disposable offline caches; archived documents remain the source of truth."""

from functools import lru_cache
from hashlib import sha256
from importlib.resources import files
import json
import os
from pathlib import Path
import stat

from . import __version__
from .errors import LinkReadError
from .storage import write_text


# Bump when a packaged metadata/renderer algorithm changes without asset changes.
CACHE_VERSION = 10
READER_MANIFEST = ".reader-cache.json"
LIBRARY_INDEX = ".library-index.json"
_DERIVED = {".reader.html", READER_MANIFEST}


def archive_signature(folder: Path) -> str:
    """Detect changed content, atomic replacement and new/deleted sidecars cheaply.

    ctime/inode accompany mtime and size: an editor preserving mtime or replacing
    a same-sized file must still invalidate cached metadata. No symlink is read.
    """
    if folder.is_symlink() or not folder.is_dir():
        raise LinkReadError("文章归档目录不可用")
    rows = []
    info = folder.stat()
    rows.append(["/", info.st_dev, info.st_ino])
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.name in _DERIVED or entry.name.startswith(".linkread-"):
                continue
            if Path(entry.name).suffix not in {".json", ".md", ".html"}:
                continue
            info = entry.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise LinkReadError("文章数据文件不可用")
            rows.append([entry.name, info.st_dev, info.st_ino, info.st_size,
                         info.st_mtime_ns, info.st_ctime_ns, info.st_mode])
    return sha256(json.dumps(sorted(rows), separators=(",", ":")).encode()).hexdigest()


@lru_cache(maxsize=1)
def implementation_signature() -> str:
    """Invalidate on shipped reader assets, Python sources or format changes.

    Resources work in editable installs and frozen sidecars. Frozen builds whose
    Python sources are not resources use CACHE_VERSION for algorithm changes.
    """
    digest = sha256(f"linkread-cache:{CACHE_VERSION}:{__version__}".encode())

    def visit(root, prefix=""):
        for entry in sorted(root.iterdir(), key=lambda item: item.name):
            name = prefix + entry.name
            if entry.is_dir():
                if entry.name != "__pycache__":
                    visit(entry, name + "/")
            elif entry.name.endswith((".py", ".html", ".js", ".css", ".LICENSE.txt")):
                digest.update(name.encode())
                digest.update(entry.read_bytes())

    visit(files(__package__))
    return digest.hexdigest()


def read_cache(path: Path, *, limit: int) -> dict | None:
    """Bad or interrupted caches are ignored, never treated as missing articles."""
    if path.is_symlink():
        return None
    try:
        if not path.is_file() or path.stat().st_size > limit:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, RecursionError):
        return None


def save_cache(path: Path, value: dict) -> None:
    """Failure to save optional acceleration metadata must not block reading."""
    if path.is_symlink():
        return
    try:
        write_text(path, json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


def cached_reader(article: Path, signature: str) -> dict | None:
    cached = article.with_name(".reader.html")
    if cached.is_symlink():
        raise LinkReadError("阅读缓存路径不可用")
    manifest = read_cache(article.with_name(READER_MANIFEST), limit=16384)
    if (not manifest or manifest.get("version") != CACHE_VERSION
            or manifest.get("inputs") != signature
            or manifest.get("implementation") != implementation_signature()
            or not isinstance(manifest.get("readingKey"), str)
            or not manifest["readingKey"] or len(manifest["readingKey"]) > 512):
        return None
    try:
        # Validate the materialized file too: a truncated/edited cache is rebuilt.
        digest = sha256()
        descriptor = os.open(cached, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != manifest.get("sha256"):
            return None
    except OSError:
        return None
    return {"revision": digest.hexdigest()[:20], "readingKey": manifest["readingKey"]}


def remember_reader(article: Path, signature: str, html: str, reading_key: str) -> dict:
    digest = sha256(html.encode("utf-8")).hexdigest()
    save_cache(article.with_name(READER_MANIFEST), {
        "version": CACHE_VERSION, "inputs": signature,
        "implementation": implementation_signature(), "sha256": digest,
        "readingKey": reading_key,
    })
    return {"revision": digest[:20], "readingKey": reading_key}
