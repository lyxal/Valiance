"""Task-boundary transfer classification and recursive runtime validation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Iterable


class TransferClass(Enum):
    """Ownership class used when a runtime value crosses a task boundary."""

    VALUE = auto()
    SHARED_HANDLE = auto()
    ISOLATED = auto()


class IsolatedResource:
    """Marker base for uniquely owned resources that must not be aliased."""

    task_transfer_class = TransferClass.ISOLATED


def declare_transfer_class(cls: type, classification: TransferClass) -> type:
    """Declare how an external runtime value crosses task/channel boundaries.

    External integrations must call this during registration instead of relying
    on the value classifier's ordinary-value fallback.
    """
    if not isinstance(classification, TransferClass):
        raise TypeError("transfer classification must be a TransferClass")
    existing = getattr(cls, "task_transfer_class", None)
    if existing is not None and existing is not classification:
        raise ValueError(f"{cls.__name__} already has a different transfer class")
    cls.task_transfer_class = classification
    return cls


@dataclass(frozen=True, slots=True)
class TransferViolation:
    """One unsafe value and its stable path through the transferred graph."""

    path: str
    reason: str

    def render(self) -> str:
        """Render the transfer violation with its complete value path."""
        return f"cannot transfer {self.path}: {self.reason}"


def classify_task_value(value: Any) -> TransferClass:
    """Classify one runtime value without traversing its children."""
    explicit = getattr(value, "task_transfer_class", None)
    if isinstance(explicit, TransferClass):
        return explicit
    # Avoid importing VM/concurrency classes and creating module cycles. Shared
    # runtime entities opt in by their immutable-handle shape.
    if type(value).__name__ in {"TaskHandle", "Channel"}:
        return TransferClass.SHARED_HANDLE
    return TransferClass.VALUE


def _children(value: Any) -> Iterable[tuple[str, Any]]:
    """Yield owned graph edges with diagnostic path components."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield f"[{key!r}]", item
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield f"[{index}]", item
        return
    inner = getattr(value, "value", None)
    if type(value).__name__ == "TaggedValue":
        yield ".value", inner
        return
    fields = getattr(value, "fields", None)
    if isinstance(fields, dict):
        for name, item in fields.items():
            yield f".{name}", item
        return
    owned_names = getattr(value, "owned_names", None)
    globals_ = getattr(value, "globals", None)
    if isinstance(owned_names, frozenset) and isinstance(globals_, dict):
        for name in sorted(owned_names):
            if name in globals_:
                yield f".capture.{name}", globals_[name]
        return
    overloads = getattr(value, "overloads", None)
    if isinstance(overloads, tuple) and type(value).__name__ == "OverloadedFunctionValue":
        for index, overload in enumerate(overloads):
            yield f".overload[{index}]", overload


def validate_task_transfer(
    value: Any,
    *,
    path: str = "value",
    unique: bool = False,
) -> tuple[TransferViolation, ...]:
    """Validate a complete possibly cyclic graph for transfer into a task.

    Isolated resources are accepted only when the caller proves unique ownership.
    Shared task/channel handles terminate traversal and preserve entity identity.
    """
    violations: list[TransferViolation] = []
    seen: set[int] = set()

    def visit(item: Any, item_path: str, root_unique: bool) -> None:
        """Visit one value or syntax node while avoiding recursive cycles."""
        marker = id(item)
        if marker in seen:
            return
        seen.add(marker)
        classification = classify_task_value(item)
        if classification is TransferClass.SHARED_HANDLE:
            return
        if classification is TransferClass.ISOLATED:
            if not root_unique:
                violations.append(
                    TransferViolation(
                        item_path,
                        "isolated resource is not proven uniquely owned",
                    )
                )
            return
        for suffix, child in _children(item):
            # Uniqueness of an outer aggregate does not prove uniqueness of an
            # arbitrary nested resource unless move analysis supplied that fact.
            visit(child, item_path + suffix, False)

    visit(value, path, unique)
    return tuple(violations)


def require_task_transfer(value: Any, *, path: str = "value", unique: bool = False) -> None:
    """Raise a stable ValueError for the first unsafe transfer path."""
    violations = validate_task_transfer(value, path=path, unique=unique)
    if violations:
        raise ValueError(violations[0].render())
