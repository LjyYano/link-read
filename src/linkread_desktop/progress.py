"""Structured progress events shared by the CLI and desktop application."""

from collections.abc import Callable
from dataclasses import dataclass

from .models import SourceInfo


@dataclass(frozen=True)
class RequestStatus:
    sequence: int
    id: str
    stage: str | None
    phase: str
    active: int
    queued: int
    validating: int
    completed: int
    cacheHits: int


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    message: str
    completed: int | None = None
    total: int | None = None
    work_dir: str | None = None
    source: SourceInfo | None = None
    request: RequestStatus | None = None


ProgressCallback = Callable[[ProgressEvent], None]


def report(
    progress: ProgressCallback | None, stage: str, message: str, *,
    completed: int | None = None, total: int | None = None,
    work_dir: str | None = None,
    source: SourceInfo | None = None,
    request: RequestStatus | None = None,
) -> None:
    if progress is not None:
        progress(ProgressEvent(stage=stage, message=message, completed=completed, total=total,
                               work_dir=work_dir, source=source, request=request))
