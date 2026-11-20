"""Bounded execution of independent model batches in source order."""

from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import TypeVar

from ..errors import LinkReadError


T = TypeVar("T")
R = TypeVar("R")


def ordered_batches(
    items: Sequence[T], process: Callable[[int, T], R], *, workers: int,
    on_complete: Callable[[int, int], None] | None = None,
) -> list[R]:
    """Keep at most workers calls in flight and stop scheduling on failure."""
    if not 1 <= workers <= 4:
        raise LinkReadError("--jobs 需要在 1 到 4 之间")
    if workers == 1:
        results_in_order = []
        for index, item in enumerate(items, 1):
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
                index, item = next(queue)
                pending[executor.submit(process, index, item)] = index
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                # Check every completed call before scheduling any more work.
                for future in done:
                    index = pending.pop(future)
                    results[index] = future.result()
                    if on_complete is not None:
                        on_complete(index, len(results))
                for _ in done:
                    entry = next(queue, None)
                    if entry is not None:
                        index, item = entry
                        pending[executor.submit(process, index, item)] = index
        finally:
            for future in pending:
                future.cancel()
    return [results[index] for index in range(1, len(items) + 1)]
