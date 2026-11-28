"""Manual reading flags, kept independently from generated article content."""

from contextlib import contextmanager
import json
import os
from pathlib import Path

from .errors import LinkReadError
from .storage import write_text


STATE_FILENAME = ".reading-state.json"


def reading_state(folder: Path) -> dict[str, bool]:
    path = folder / STATE_FILENAME
    if path.is_symlink():
        raise LinkReadError("阅读状态文件不可用")
    if not path.exists():
        return {"isRead": False, "isStarred": False}
    try:
        if path.stat().st_size > 4096:
            raise ValueError("state is too large")
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or value.get("version") != 1
                or any(type(value.get(key)) is not bool for key in ("isRead", "isStarred"))):
            raise ValueError("invalid reading state")
        return {key: value[key] for key in ("isRead", "isStarred")}
    except (OSError, ValueError) as exc:
        raise LinkReadError("无法读取已保存的阅读状态，请检查资料库中的 .reading-state.json") from exc


@contextmanager
def file_lock(lock: Path):
    """Acquire an exclusive cross-process flock on a private lock file."""
    import fcntl  # Desktop targets macOS; flock also works in Linux verification.

    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


@contextmanager
def archive_mutation(library: Path):
    """Serialize state patches and the final archive replacement across sidecars."""
    with file_lock(library / ".article-state.lock"):
        yield


def save_reading_state(folder: Path, state: dict[str, bool]) -> None:
    path = folder / STATE_FILENAME
    if (folder.is_symlink() or not folder.is_dir() or path.is_symlink()
            or (folder / "article.html").is_symlink() or not (folder / "article.html").is_file()):
        raise LinkReadError("找不到这篇已生成的文章")
    if any(type(state.get(key)) is not bool for key in ("isRead", "isStarred")):
        raise LinkReadError("阅读状态必须为布尔值")
    write_text(path, json.dumps({"version": 1, **state}, separators=(",", ":")) + "\n")


def patch_reading_state(folder: Path, *, is_read: bool | None = None,
                        is_starred: bool | None = None) -> dict[str, bool]:
    if is_read is None and is_starred is None:
        raise LinkReadError("请选择要更新的阅读状态")
    with archive_mutation(folder.parent):
        state = reading_state(folder)
        for key, value in (("isRead", is_read), ("isStarred", is_starred)):
            if value is not None:
                if type(value) is not bool:
                    raise LinkReadError("阅读状态必须为布尔值")
                state[key] = value
        save_reading_state(folder, state)
    return state
