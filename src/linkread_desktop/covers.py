"""Offline local cover image cache inside each article archive folder."""

import base64
import os
from pathlib import Path
import re
import stat
from urllib.parse import parse_qs, quote, urljoin, urlsplit

import requests

from .metadata import thumbnail_url
from .models import SourceInfo
from .renderers.formatting import source_info_link
from .storage import write_bytes


COVER_EXTENSIONS = (".jpg", ".png", ".webp", ".gif")
COVER_FILENAMES = frozenset(f"cover{ext}" for ext in COVER_EXTENSIONS)
MAX_COVER_BYTES = 5 * 1024 * 1024
# A cover above this size is linked online instead of being copied into every reading page.
MAX_EMBEDDED_COVER_BYTES = 1024 * 1024
# Online covers are bounded like any other saved link, independently of the inline budget.
MAX_COVER_URL_CHARS = 8192
COVER_MIME = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}
_COVER_PREFIX_CHARS = max(len(f"data:{mime};base64,") for mime in COVER_MIME.values())
_INLINE_COVER = re.compile(r"data:image/(?:jpeg|png|gif|webp);base64,[A-Za-z0-9+/]+={0,2}")
COVER_TIMEOUT_SECONDS = 10
MAX_COVER_REDIRECTS = 3
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


def detect_image_extension(data: bytes) -> str | None:
    """Sniff only safe raster image formats from magic bytes; never save HTML or SVG."""
    if len(data) < 12:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return None


def find_local_cover(folder: Path) -> Path | None:
    """Return the validated local cover file in an article directory, if any."""
    if folder.is_symlink() or not folder.is_dir():
        return None
    for ext in COVER_EXTENSIONS:
        candidate = folder / f"cover{ext}"
        try:
            info = candidate.lstat()
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode) and 0 < info.st_size <= MAX_COVER_BYTES:
            return candidate
    return None


def local_cover_url(folder: Path) -> str | None:
    """Build the Tauri custom-protocol URL with a stat-based cache buster."""
    cover = find_local_cover(folder)
    if cover is None:
        return None
    try:
        info = cover.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
        return None
    version = f"{info.st_mtime_ns}-{info.st_size}"
    return f"linkread-reader://localhost/cover/{quote(folder.name, safe='')}?v={version}"


def remove_local_cover(folder: Path) -> None:
    """Delete any regular cached cover files inside an article directory."""
    if folder.is_symlink() or not folder.is_dir():
        return
    for ext in COVER_EXTENSIONS:
        candidate = folder / f"cover{ext}"
        try:
            if stat.S_ISREG(candidate.lstat().st_mode):
                candidate.unlink(missing_ok=True)
        except OSError:
            continue


def save_cover_bytes(folder: Path, ext: str, data: bytes) -> Path | None:
    """Atomically write validated cover bytes into an article directory."""
    if (ext not in COVER_EXTENSIONS or not data or len(data) > MAX_COVER_BYTES
            or detect_image_extension(data) != ext):
        return None
    if folder.is_symlink():
        return None
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"cover{ext}"
    if target.is_symlink():
        return None
    write_bytes(target, data, replace=True)
    for other_ext in COVER_EXTENSIONS:
        if other_ext == ext:
            continue
        stale = folder / f"cover{other_ext}"
        try:
            if stat.S_ISREG(stale.lstat().st_mode):
                stale.unlink(missing_ok=True)
        except OSError:
            continue
    return target


def copy_local_cover(source_folder: Path, target_folder: Path) -> Path | None:
    """Carry over an existing verified local cover during offline replacement."""
    source = find_local_cover(source_folder)
    if source is None:
        return None
    try:
        data = source.read_bytes()
    except OSError:
        return None
    ext = detect_image_extension(data)
    if ext is None:
        return None
    return save_cover_bytes(target_folder, ext, data)


def _read_cover_bytes(cover: Path) -> bytes | None:
    """Read a regular file without following symlinks, bounded by the inline limit."""
    try:
        descriptor = os.open(cover, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None
            data = stream.read(MAX_EMBEDDED_COVER_BYTES + 1)
    except OSError:
        return None
    return data if 0 < len(data) <= MAX_EMBEDDED_COVER_BYTES else None


def embedded_cover(folder: Path) -> str | None:
    """Inline the validated local cover when it is small enough for the reading page."""
    cover = find_local_cover(folder)
    if cover is None:
        return None
    data = _read_cover_bytes(cover)
    if data is None:
        return None
    # The type comes from the content, never from the file name.
    ext = detect_image_extension(data)
    if ext is None:
        return None
    return f"data:{COVER_MIME[ext]};base64,{base64.b64encode(data).decode('ascii')}"


def cover_source(folder: Path | None, thumbnail: str | None) -> str | None:
    """Prefer the offline local cover, then the allowlisted online thumbnail."""
    if folder is not None:
        embedded = embedded_cover(folder)
        if embedded is not None:
            return embedded
    return thumbnail_url(thumbnail)


def valid_cover_source(value: object) -> str | None:
    """Re-validate at render time: only an inlined raster image or an allowlisted thumbnail."""
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("data:"):
        # Base64 takes four characters per three bytes, in addition to the prefix.
        limit = 4 * ((MAX_EMBEDDED_COVER_BYTES + 2) // 3) + _COVER_PREFIX_CHARS
        return value if len(value) <= limit and _INLINE_COVER.fullmatch(value) else None
    return value if len(value) <= MAX_COVER_URL_CHARS and thumbnail_url(value) == value else None


def display_thumbnail(source: SourceInfo) -> str | None:
    """The cover the library shows: a stable YouTube frame, else the saved cover URL."""
    link = source_info_link(source)
    if source.platform == "youtube" and link:
        video_id = parse_qs(urlsplit(link).query).get("v")
        if video_id:
            return thumbnail_url(f"https://i.ytimg.com/vi/{video_id[0]}/hqdefault.jpg")
    return thumbnail_url(source.thumbnail_url)


def cover_candidates(source: SourceInfo) -> list[str]:
    """Return ordered, allowlisted HTTPS cover URLs for a media source."""
    urls: list[str] = []
    primary = thumbnail_url(source.thumbnail_url)
    if primary:
        urls.append(primary)
    if source.platform == "youtube":
        video_id = source.video_id
        if not video_id:
            link = source_info_link(source)
            if link:
                values = parse_qs(urlsplit(link).query).get("v")
                video_id = values[0] if values else None
        if video_id:
            fallback = thumbnail_url(f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg")
            if fallback and fallback not in urls:
                urls.append(fallback)
    return urls


def _read_bounded(response) -> bytes | None:
    if hasattr(response, "iter_content"):
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_COVER_BYTES:
                return None
            chunks.append(chunk)
        return b"".join(chunks)
    data = getattr(response, "content", b"")
    if not isinstance(data, bytes) or len(data) > MAX_COVER_BYTES:
        return None
    return data


def fetch_cover_bytes(url: str | None, *, session=None) -> tuple[str, bytes] | None:
    """Download a public cover image with strict CDN allowlist, redirect and size checks."""
    current = thumbnail_url(url)
    if not current:
        return None
    client = session or requests
    try:
        for _ in range(MAX_COVER_REDIRECTS + 1):
            response = client.get(
                current, headers=_HEADERS, timeout=COVER_TIMEOUT_SECONDS,
                allow_redirects=False, stream=True,
            )
            try:
                status = response.status_code
                if status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location", "")
                    if not isinstance(location, str) or not location.strip():
                        return None
                    next_url = thumbnail_url(urljoin(current, location.strip()))
                    if not next_url:
                        return None
                    current = next_url
                    continue
                if status != 200:
                    return None
                data = _read_bounded(response)
                if not data:
                    return None
                ext = detect_image_extension(data)
                if ext is None:
                    return None
                return ext, data
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
    except (requests.RequestException, OSError, ValueError):
        return None
    return None


def download_cover(
    urls: str | list[str] | None, folder: Path, *, session=None, overwrite: bool = False,
) -> Path | None:
    """Best-effort cover download into an article directory; never raises on failure."""
    if folder.is_symlink():
        return None
    if not overwrite:
        existing = find_local_cover(folder)
        if existing is not None:
            return existing
    if not urls:
        return None
    candidates = [urls] if isinstance(urls, str) else list(urls)
    for candidate in candidates:
        fetched = fetch_cover_bytes(candidate, session=session)
        if fetched is None:
            continue
        ext, data = fetched
        try:
            saved = save_cover_bytes(folder, ext, data)
        except OSError:
            return None
        if saved is not None:
            return saved
    return None
