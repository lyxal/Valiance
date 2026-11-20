"""Reusable native-future boundary for cooperative VM external operations.

Producers return an operation rather than running user code in a background
thread. The VM attaches a scheduler registration, receives the result on its
own thread, and calls abort when its owning task is cancelled. Root execution
waits for the *same* producer and decodes results through the same contract.
"""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ExternalOperation:
    """Future and abort hook with scheduler-owned materialization of results."""

    future: Future[Any]
    complete: Callable[[Any], tuple[Any, ...]]
    fail: Callable[[BaseException], tuple[Any, ...]]
    abort: Callable[[], None]

    def resolve(self) -> tuple[Any, ...]:
        try:
            result = self.future.result()
        except BaseException as exc:
            return self.fail(exc)
        return self.complete(result)
