"""Local storage for one video's transcript and article files."""

from hashlib import sha256
from pathlib import Path
import re
import unicodedata

from .errors import LinkReadError
from .models import TranscriptDocument
from .pipeline import video_source_key
from .renderers.formatting import source_link
from .renderers.site import load_transcript
from .storage import write_text


DEFAULT_LIBRARY = Path("library")
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')
_WINDOWS_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)


def _source_key(document: TranscriptDocument) -> str:
    try:
        url = source_link(document)
    except ValueError:
        url = None
    if url:
        key = video_source_key(url)
        if key:
            return key
    fingerprint = sha256(document.model_dump_json(exclude_none=True).encode("utf-8")).hexdigest()[:12]
    return f"{document.source.platform}-{fingerprint}"


def _folder_title(title: str) -> str:
    title = _UNSAFE_CHARS.sub(" ", unicodedata.normalize("NFC", title))
    title = "".join(" " if unicodedata.category(char).startswith("C") else char for char in title)
    title = " ".join(title.split()).strip(" .")
    title = title.encode("utf-8")[:120].decode("utf-8", errors="ignore").rstrip(" .") or "未命名"
    return f"_{title}" if _WINDOWS_DEVICE.match(title) else title


def find_workspace(root: Path, key: str) -> Path | None:
    suffix = f" [{key}]"
    root = root.expanduser()
    matches = [path for path in root.iterdir() if path.name.endswith(suffix)] if root.is_dir() else []
    if len(matches) > 1:
        raise LinkReadError(f"资料库内有多个目录对应 {key}，请先合并到一个目录")
    if not matches:
        return None
    folder = matches[0]
    if folder.is_symlink() or not folder.is_dir():
        raise LinkReadError(f"视频目录不可用：{folder}")
    return folder


def workspace_dir(root: Path, document: TranscriptDocument) -> Path:
    key = _source_key(document)
    return find_workspace(root, key) or root.expanduser() / f"{_folder_title(document.source.title)} [{key}]"


def in_workspace(path: Path, document: TranscriptDocument) -> bool:
    return path.absolute().parent.name.endswith(f" [{_source_key(document)}]")


def load_saved_transcript(target: Path, key: str) -> TranscriptDocument:
    if target.is_symlink() or not target.is_file():
        raise LinkReadError(f"原始字幕路径不可用：{target}")
    saved = load_transcript(target)
    if _source_key(saved) != key:
        raise LinkReadError(f"已保存的字幕属于其他视频：{target}")
    return saved


def keep_transcript(target: Path, document: TranscriptDocument) -> tuple[TranscriptDocument, bool]:
    """Keep the saved transcript as the source for later stages of this video."""
    if not target.exists() and not target.is_symlink():
        try:
            write_text(target, document.model_dump_json(indent=2, exclude_none=True) + "\n", replace=False)
        except FileExistsError:
            pass
        else:
            return document, False
    return load_saved_transcript(target, _source_key(document)), True
