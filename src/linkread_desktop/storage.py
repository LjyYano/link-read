"""File writes shared by the library and static site."""

import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def _publish(target: Path, content: str | bytes, *, mode: str, replace: bool,
             encoding: str | None = None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    kwargs = {"encoding": encoding} if encoding is not None else {}
    stream = NamedTemporaryFile(mode=mode, dir=target.parent,
                                prefix=".linkread-", delete=False, **kwargs)
    temporary = Path(stream.name)
    try:
        with stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            temporary.replace(target)
        else:
            # A concurrent first write can only publish one complete file.
            target.hardlink_to(temporary)
    finally:
        temporary.unlink(missing_ok=True)


def write_text(target: Path, content: str, *, replace: bool = True) -> None:
    """Publish a complete UTF-8 file and leave the previous file on failure."""
    _publish(target, content, mode="w", encoding="utf-8", replace=replace)


def write_bytes(target: Path, content: bytes, *, replace: bool = True) -> None:
    """Publish a complete binary file and leave the previous file on failure."""
    _publish(target, content, mode="wb", replace=replace)
