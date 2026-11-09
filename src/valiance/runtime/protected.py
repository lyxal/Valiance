"""Private guarded-value storage and scoped exclusive-operation requests.

The VM owns invocation and cleanup.  This layer does not run user functions and
is deliberately unaware of bytecode, scheduling quanta, or standard modules.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable
from .concurrency import ExclusiveGate
from .transfer import TransferClass, require_shared_value


class GuardedValue:
    """Identity-bearing shared handle to one committed immutable logical value."""

    task_transfer_class = TransferClass.SHARED_HANDLE

    def __init__(self, initial: Any) -> None:
        """Own the initial committed value and an independent access gate."""
        self.gate = ExclusiveGate()
        self.value = initial  # one independently owned occurrence
        self.owners = 1
        self.destroyed = False
        self._lock = RLock()

    def retain_handle(self) -> None:
        """Increment shared-handle ownership unless already destroyed."""
        with self._lock:
            if self.destroyed:
                raise RuntimeError("cannot retain a released State")
            self.owners += 1

    def release_handle(self, release: Callable[[Any], None]) -> None:
        """Release one handle, finally destroying the idle cell exactly once."""
        with self._lock:
            if self.owners <= 0:
                raise RuntimeError("State ownership underflow")
            if self.owners == 1 and (
                self.gate.owner is not None or self.gate.waiter_count
            ):
                # Reject an invalid final drop without corrupting the handle
                # count: the same cell may still have an active protected lease.
                raise RuntimeError("State released with an active operation")
            self.owners -= 1
            if self.owners:
                return
            self.destroyed = True
            previous = self.value
            self.value = None
        release(previous)

    def commit(self, owner: object, replacement: Any) -> Any:
        """Publish a completely validated replacement and return the old owner."""
        require_shared_value(replacement, path='State replacement')
        with self._lock:
            if self.destroyed or self.gate.owner is not owner:
                raise RuntimeError("State commit without exclusive ownership")
            previous = self.value
            self.value = replacement  # linearisation point
            return previous


@dataclass(slots=True)
class ProtectedOperation:
    """Request for VM-mediated protected access, not a language value."""

    kind: str
    cell: GuardedValue
    payload: Any = None
    closed: bool = False

    def close(self, release: Callable[[Any], None]) -> None:
        """Drop all uncommitted occurrences once, even during task unwinding."""
        if self.closed:
            return
        self.closed = True
        try:
            if self.payload is not None:
                release(self.payload)
        finally:
            self.cell.release_handle(release)
