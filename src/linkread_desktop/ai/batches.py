"""Bounded execution of independent model batches in source order."""

from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Event
from typing import TypeVar

from ..errors import LinkReadError


T = TypeVar("T")
R = TypeVar("R")


def _check_cancelled(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise LinkReadError("生成已取消", code="cancelled")


def ordered_batches(
    items: Sequence[T], process: Callable[[int, T], R], *, workers: int,
    on_complete: Callable[[int, int], None] | None = None,
    cancel: Event | None = None,
) -> list[R]:
    """Keep at most workers calls in flight and stop scheduling on failure."""
    if not 1 <= workers <= 4:
        raise LinkReadError("--jobs 需要在 1 到 4 之间")
    _check_cancelled(cancel)
    if workers == 1:
        results_in_order = []
        for index, item in enumerate(items, 1):
            _check_cancelled(cancel)
            results_in_order.append(process(index, item))
            if on_complete is not None:
                on_complete(index, index)
        return results_in_order
    queue = iter(enumerate(items, 1))
    results: dict[int, R] = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="linkread-ai") as executor:
        pending = {}
        try:
            for _ in range(min(workers, len(items))):
                _check_cancelled(cancel)
                index, item = next(queue)
                pending[executor.submit(process, index, item)] = index
            while pending:
                _check_cancelled(cancel)
                done, _ = (
                    wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                    if cancel is not None
                    else wait(pending, return_when=FIRST_COMPLETED)
                )
                if not done:
                    continue
                # Check every completed call before scheduling any more work.
                # Record any completed successes in this wave before raising a failure.
                first_error: BaseException | None = None
                for future in done:
                    index = pending.pop(future)
                    _check_cancelled(cancel)
                    try:
                        results[index] = future.result()
                    except BaseException as exc:
                        if first_error is None:
                            first_error = exc
                    else:
                        if on_complete is not None:
                            on_complete(index, len(results))
                if first_error is not None:
                    raise first_error
                for _ in done:
                    _check_cancelled(cancel)
                    entry = next(queue, None)
                    if entry is not None:
                        index, item = entry
                        pending[executor.submit(process, index, item)] = index
        finally:
            for future in pending:
                future.cancel()
    return [results[index] for index in range(1, len(items) + 1)]
