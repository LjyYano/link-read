"""File writes shared by the library and static site."""

from pathlib import Path
from tempfile import NamedTemporaryFile


def write_text(target: Path, content: str, *, replace: bool = True) -> None:
    """Publish a complete UTF-8 file and leave the previous file on failure."""
    target.parent.mkdir(parents=True, exist_ok=True)
    stream = NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                prefix=".linkread-", delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            stream.write(content)
        if replace:
            temporary.replace(target)
        else:
            # A concurrent first write can only publish one complete file.
            target.hardlink_to(temporary)
    finally:
        temporary.unlink(missing_ok=True)
