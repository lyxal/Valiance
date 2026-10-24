"""Helpers for runtime values shared by builtins, the VM, and the CLI."""

from __future__ import annotations

import cmath
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence, Sized
from dataclasses import dataclass, field
from fractions import Fraction
from functools import lru_cache
from decimal import Decimal, localcontext
from itertools import islice
import json
from typing import Any

from valiance.vtypes import DataTag


@dataclass(frozen=True, slots=True)
class BorrowedValue:
    """A temporary access to an owned occurrence, never a second owner."""

    value: Any
    allows_reconstruction: bool = False

    def read(self) -> Any:
        """Reject accesses whose owning occurrence has already ended."""
        value = self.value
        payload = value
        while isinstance(payload, TaggedValue):
            payload = payload.value
        if isinstance(payload, ObjectValue):
            if payload.destroyed or payload.ownership_transferred:
                raise RuntimeError('borrowed value outlived its owning occurrence')
        elif isinstance(payload, (ListValue, DictValue, LazyList)) and payload.refcount <= 0:
            raise RuntimeError('borrowed value outlived its owning occurrence')
        return value


@dataclass
class LazyList:
    """A replayable list-like value backed by a lazy or infinite iterable.

    Values are cached as the source advances. Every traversal starts at index
    zero and reads cached values before requesting more from the shared source,
    so duplication, formatting, and partial consumers do not destructively
    advance other references to the same logical list.
    """

    iterable: Iterable[Any]
    runtime_rank: int | None = field(default=None, compare=False, repr=False)
    owned_values: tuple[Any, ...] = field(default=(), compare=False, repr=False)
    refcount: int = field(default=1, compare=False, repr=False)
    _cache: list[Any] = field(default_factory=list, init=False, compare=False, repr=False)
    _source_iterator: Iterator[Any] | None = field(
        default=None, init=False, compare=False, repr=False
    )
    _exhausted: bool = field(default=False, init=False, compare=False, repr=False)

    def _iterate_uncached(self) -> Iterator[Any]:
        """Return the source iterator used to populate the replay cache."""
        return iter(self.iterable)

    def __iter__(self):
        """Iterate from the beginning while caching newly demanded values."""
        index = 0
        while True:
            if index < len(self._cache):
                yield self._cache[index]
                index += 1
                continue
            if self._exhausted:
                return
            if self._source_iterator is None:
                self._source_iterator = iter(self._iterate_uncached())
            try:
                item = next(self._source_iterator)
            except StopIteration:
                self._exhausted = True
                self._source_iterator = None
                return
            self._cache.append(item)
            index += 1
            yield item

    def __eq__(self, other: object) -> bool:
        """Return whether this lazy list equals another value."""
        if isinstance(other, LazyList):
            return list(self) == list(other)
        if isinstance(other, Sequence) and not isinstance(other, (str, bytes, tuple)):
            return list(self) == list(other)
        return False


@dataclass(frozen=True, slots=True)
class LazyPipelineStage:
    """One reusable lazy transformation that builds fresh per-iterator state."""

    name: str
    build: Callable[[], Callable[[Any], tuple[bool, Any, bool]]] = field(
        repr=False,
        compare=False,
    )
    empty_without_source: bool = False

    @staticmethod
    def mapping(operation: Callable[[Any], Any]) -> LazyPipelineStage:
        """Build a stateless stage that transforms every incoming value."""
        return LazyPipelineStage(
            "map",
            lambda: lambda value: (True, operation(value), False),
        )

    @staticmethod
    def filtering(predicate: Callable[[Any], bool]) -> LazyPipelineStage:
        """Build a stateless stage that conditionally keeps its incoming value."""
        return LazyPipelineStage(
            "filter",
            lambda: lambda value: (bool(predicate(value)), value, False),
        )

    @staticmethod
    def dropping(count: int) -> LazyPipelineStage:
        """Build a stateful stage that discards a fixed input prefix."""
        if count < 0:
            raise ValueError("pipeline drop count cannot be negative")

        def build() -> Callable[[Any], tuple[bool, Any, bool]]:
            """Create independent drop state for one pipeline iterator."""
            remaining = count

            def apply(value: Any) -> tuple[bool, Any, bool]:
                """Drop values until the stage's prefix has been consumed."""
                nonlocal remaining
                if remaining > 0:
                    remaining -= 1
                    return False, value, False
                return True, value, False

            return apply

        return LazyPipelineStage("drop", build)

    @staticmethod
    def limiting(count: int) -> LazyPipelineStage:
        """Build a stateful stage that stops after the requested output prefix."""
        if count < 0:
            raise ValueError("pipeline limit cannot be negative")

        def build() -> Callable[[Any], tuple[bool, Any, bool]]:
            """Create independent limit state for one pipeline iterator."""
            remaining = count

            def apply(value: Any) -> tuple[bool, Any, bool]:
                """Keep one value and mark the item that completes the prefix."""
                nonlocal remaining
                if remaining <= 0:
                    return False, value, True
                remaining -= 1
                return True, value, remaining == 0

            return apply

        return LazyPipelineStage(
            "take",
            build,
            empty_without_source=count == 0,
        )


@dataclass(frozen=True, slots=True)
class PipelineTerminal:
    """Generic state machine driven by a planned lazy pipeline."""

    initial: Callable[[], Any] = field(repr=False)
    consume: Callable[[Any, Any], tuple[Any, bool]] = field(repr=False)
    finish: Callable[[Any], Any] = field(repr=False)

    @staticmethod
    def reducing(initial: Any, reducer: Callable[[Any, Any], Any]) -> PipelineTerminal:
        """Build a terminal that consumes every produced value."""
        return PipelineTerminal(
            lambda: initial,
            lambda state, item: (reducer(state, item), False),
            lambda state: state,
        )

    @staticmethod
    def first(error: str) -> PipelineTerminal:
        """Build an early-terminating terminal for the first produced value."""
        missing = object()

        def finish(value: Any) -> Any:
            """Return the first value or raise the caller-provided empty error."""
            if value is missing:
                raise RuntimeError(error)
            return value

        return PipelineTerminal(
            lambda: missing,
            lambda _state, item: (item, True),
            finish,
        )


@dataclass(eq=False)
class PlannedLazyList(LazyList):
    """A lazy list whose reusable stages can be fused into terminal consumers."""

    source: Iterable[Any] = field(default=())
    stages: tuple[LazyPipelineStage, ...] = field(default=())

    def __init__(
        self,
        source: Iterable[Any],
        stages: tuple[LazyPipelineStage, ...],
        *,
        runtime_rank: int | None = None,
        owned_values: tuple[Any, ...] = (),
    ) -> None:
        """Initialize a reusable lazy pipeline without nesting generators."""
        super().__init__((), runtime_rank, owned_values)
        self.source = source
        self.stages = stages

    def _iterate_uncached(self) -> Iterator[Any]:
        """Execute one set of pipeline stages to populate the replay cache."""
        operations = tuple(stage.build() for stage in self.stages)
        if any(stage.empty_without_source for stage in self.stages):
            # A zero-length prefix must not pull even one source item.
            return
        for source_item in self.source:
            item = source_item
            stop_after_item = False
            for operation in operations:
                keep, item, stop = operation(item)
                stop_after_item = stop_after_item or stop
                if not keep:
                    break
            else:
                yield item
            if stop_after_item:
                return

    def append_stage(self, stage: LazyPipelineStage) -> PlannedLazyList:
        """Return a pipeline sharing this source with one additional stage."""
        return PlannedLazyList(
            self.source,
            (*self.stages, stage),
            runtime_rank=self.runtime_rank,
            owned_values=self.owned_values,
        )

    def run_terminal(self, terminal: PipelineTerminal) -> Any:
        """Drive this pipeline with an arbitrary stateful terminal."""
        state = terminal.initial()
        for item in self:
            state, stop = terminal.consume(state, item)
            if stop:
                break
        return terminal.finish(state)

    def reduce_terminal(
        self,
        initial: Any,
        reducer: Callable[[Any, Any], Any],
    ) -> Any:
        """Drive every pipeline stage and one terminal reducer in one loop."""
        return self.run_terminal(PipelineTerminal.reducing(initial, reducer))

    def count_terminal(self) -> int:
        """Count values surviving every pipeline stage without materializing them."""
        return self.reduce_terminal(0, lambda count, _item: count + 1)

    def sum_terminal(self, zero: Any) -> Any:
        """Fuse this pipeline with numeric summation in one loop."""
        return self.reduce_terminal(zero, lambda total, item: total + item)




def _mutate_and_invalidate(
    container: Any,
    mutation: Callable[..., Any],
    *args: Any,
) -> Any:
    """Apply a built-in container mutation and invalidate ownership metadata."""
    result = mutation(container, *args)
    container._invalidate_ownership_cache()
    return result


class ListValue(list[Any]):
    """An eager Valiance list carrying rank and ownership-scan metadata."""

    def __init__(
        self,
        iterable: Iterable[Any] = (),
        *,
        runtime_rank: int | None = None,
    ) -> None:
        """Initialize this list value."""
        super().__init__(iterable)
        self.runtime_rank = runtime_rank
        self.refcount = 1
        self._ownership_trivial: bool | None = None
        self._tag_free: bool | None = None

    def _invalidate_ownership_cache(self) -> None:
        """Forget whether every direct item is ownership-trivial."""
        self._ownership_trivial = None
        self._tag_free = None

    def __setitem__(self, key: Any, value: Any) -> None:
        """Set one item and invalidate cached ownership metadata."""
        _mutate_and_invalidate(self, list.__setitem__, key, value)

    def __delitem__(self, key: Any) -> None:
        """Delete one item and invalidate cached ownership metadata."""
        _mutate_and_invalidate(self, list.__delitem__, key)

    def append(self, value: Any) -> None:
        """Append one item and invalidate cached ownership metadata."""
        super().append(value)
        self._invalidate_ownership_cache()

    def extend(self, values: Iterable[Any]) -> None:
        """Append several items and invalidate cached ownership metadata."""
        super().extend(values)
        self._invalidate_ownership_cache()

    def insert(self, index: int, value: Any) -> None:
        """Insert one item and invalidate cached ownership metadata."""
        super().insert(index, value)
        self._invalidate_ownership_cache()

    def pop(self, index: int = -1) -> Any:
        """Remove one item and invalidate cached ownership metadata."""
        value = super().pop(index)
        self._invalidate_ownership_cache()
        return value

    def remove(self, value: Any) -> None:
        """Remove one matching item and invalidate cached ownership metadata."""
        super().remove(value)
        self._invalidate_ownership_cache()

    def clear(self) -> None:
        """Remove all items and record that the empty list is ownership-trivial."""
        super().clear()
        self._ownership_trivial = True

    def reverse(self) -> None:
        """Reverse this list without changing its ownership classification."""
        super().reverse()

    def sort(self, *args: Any, **kwargs: Any) -> None:
        """Sort this list without changing its ownership classification."""
        super().sort(*args, **kwargs)

    def __iadd__(self, values: Iterable[Any]):
        """Append several items and invalidate cached ownership metadata."""
        result = super().__iadd__(values)
        self._invalidate_ownership_cache()
        return result

    def __imul__(self, count: int):
        """Repeat this list without changing direct item ownership kinds."""
        return super().__imul__(count)


class DictValue(dict[Any, Any]):
    """A Valiance mapping carrying cached ownership-scan metadata."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize this mapping value."""
        super().__init__(*args, **kwargs)
        self.refcount = 1
        self._ownership_trivial: bool | None = None

    def _invalidate_ownership_cache(self) -> None:
        """Forget whether every direct value is ownership-trivial."""
        self._ownership_trivial = None

    def __setitem__(self, key: Any, value: Any) -> None:
        """Set one item and invalidate cached ownership metadata."""
        _mutate_and_invalidate(self, dict.__setitem__, key, value)

    def __delitem__(self, key: Any) -> None:
        """Delete one item and invalidate cached ownership metadata."""
        _mutate_and_invalidate(self, dict.__delitem__, key)

    def clear(self) -> None:
        """Remove all items and record that the mapping is ownership-trivial."""
        super().clear()
        self._ownership_trivial = True

    def pop(self, key: Any, *default: Any) -> Any:
        """Remove one item and invalidate cached ownership metadata."""
        value = super().pop(key, *default)
        self._invalidate_ownership_cache()
        return value

    def popitem(self) -> tuple[Any, Any]:
        """Remove one item and invalidate cached ownership metadata."""
        value = super().popitem()
        self._invalidate_ownership_cache()
        return value

    def setdefault(self, key: Any, default: Any = None) -> Any:
        """Set a default and invalidate metadata only when insertion occurs."""
        if key in self:
            return self[key]
        value = super().setdefault(key, default)
        self._invalidate_ownership_cache()
        return value

    def update(self, *args: Any, **kwargs: Any) -> None:
        """Update items and invalidate cached ownership metadata."""
        super().update(*args, **kwargs)
        self._invalidate_ownership_cache()

    def __ior__(self, other: Any):
        """Merge values and invalidate cached ownership metadata."""
        result = super().__ior__(other)
        self._invalidate_ownership_cache()
        return result


class RecordValue(DictValue):
    """A record mapping whose bareword keys are retained for display."""


@dataclass(frozen=True, eq=False)
class TaggedValue:
    """A runtime value carrying reified data-tag evidence."""

    value: Any
    tags: frozenset[DataTag] = field(default_factory=frozenset)
    # An immutable, occurrence-local trait dictionary.  Different views of
    # the same nominal object never overwrite one another's checked evidence.
    structural_witnesses: tuple[
        tuple[str, tuple[tuple[str, ...], tuple[str, ...]], str], ...
    ] = ()

    def __iter__(self):
        """Iterate over values stored by this tagged value."""
        return iter(self.value)

    def __len__(self) -> int:
        """Return the number of values stored by this tagged value."""
        return len(self.value)

    def __getitem__(self, index: Any) -> Any:
        """Return an item selected from this tagged value."""
        return self.value[index]

    def __bool__(self) -> bool:
        """Return the truthiness of the wrapped runtime value."""
        return bool(self.value)

    def __eq__(self, other: object) -> bool:
        """Return whether this tagged value equals another value."""
        return self.value == unwrap_runtime_value(other)


def unwrap_runtime_value(value: Any) -> Any:
    """Return the payload beneath runtime evidence and temporary access wrappers."""
    while isinstance(value, (TaggedValue, BorrowedValue)):
        value = value.read() if isinstance(value, BorrowedValue) else value.value
    return value


def runtime_trait_witness(
    value: Any,
    name: str,
    contract: tuple[tuple[str, ...], tuple[str, ...]],
) -> str | None:
    """Read the analysis-selected method from a single trait value view."""
    if not isinstance(value, TaggedValue):
        return None
    for method, signature, identity in value.structural_witnesses:
        if method == name and signature == contract:
            return identity
    return None


def with_runtime_trait_witnesses(
    value: Any,
    proofs: tuple[tuple[str, tuple[tuple[str, ...], tuple[str, ...]], str], ...],
) -> Any:
    """Attach checked evidence to this occurrence, never the shared object."""
    if not proofs:
        return value
    payload = value.value if isinstance(value, TaggedValue) else value
    previous = value.structural_witnesses if isinstance(value, TaggedValue) else ()
    tags = runtime_value_tags(value)
    merged = {(name, contract): identity for name, contract, identity in previous}
    for name, contract, identity in proofs:
        merged[(name, contract)] = identity
    return TaggedValue(
        payload, tags,
        tuple((name, contract, identity) for (name, contract), identity in merged.items()),
    )


def runtime_value_tags(value: Any) -> frozenset[DataTag]:
    """Return the reified data tags attached to a runtime value."""
    return value.tags if isinstance(value, TaggedValue) else frozenset()


@lru_cache(maxsize=256)
def _cached_runtime_tag_additions(
    tags: tuple[DataTag, ...],
) -> frozenset[DataTag]:
    """Cache normalized positive tag additions for hot return paths."""
    return frozenset(tag for tag in tags if not tag.absent)


@lru_cache(maxsize=256)
def _cached_tagged_scalar(
    payload: Decimal | str | int | bool | None,
    tags: frozenset[DataTag],
) -> TaggedValue:
    """Intern frequently repeated immutable tagged scalar values."""
    return TaggedValue(payload, tags)


def update_runtime_tags(
    value: Any,
    *,
    add: tuple[DataTag, ...] = (),
    remove: tuple[DataTag, ...] = (),
) -> Any:
    """Apply a tag-evidence delta without nesting wrappers."""
    if isinstance(value, TaggedValue):
        payload = value.value
        current = value.tags
        witnesses = value.structural_witnesses
    else:
        payload = value
        current = frozenset()
        witnesses = ()

    if not remove:
        additions = _cached_runtime_tag_additions(add)
        tags = additions if not current else current | additions
        if tags == current:
            return value
    else:
        removed = {(tag.name, tag.depth) for tag in remove}
        tags = frozenset(tag for tag in current if (tag.name, tag.depth) not in removed)
        tags = tags.union(tag for tag in add if not tag.absent)

    if not tags and not witnesses:
        return payload
    if not witnesses and isinstance(payload, (Decimal, str, int, bool, type(None))):
        return _cached_tagged_scalar(payload, tags)
    return TaggedValue(payload, tags, witnesses)


@dataclass(frozen=True, slots=True)
class ObjectRuntimeType:
    """Runtime lifecycle metadata attached to nominal object values."""

    destructor_name: str | None = None
    dup_name: str | None = None
    dup_error: str | None = None
    mustcall_mode: str | None = None
    mustcall_methods: tuple[str, ...] = ()
    accepted_names: tuple[str, ...] = ()
    generic_variances: tuple[str, ...] = ()
    # Each fact includes a constructor's generic-supertype projections.
    type_facts: tuple[tuple[object, ...], ...] = ()
    generic_supertypes: tuple[
        tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]], ...
    ] = ()
    field_order: tuple[str, ...] = ()


@dataclass(slots=True)
class FFIHandleValue:
    """Shared identity for one opaque native pointer."""
    ffi_type: str
    address: int
    alive: bool = True
    active_leases: int = 0
    destroy_pending: bool = False

    def __post_init__(self) -> None:
        """Validate and normalize this FFI runtime value after construction."""
        if not self.ffi_type.startswith("&") or self.address == 0:
            raise ValueError("opaque FFI handles require a non-null native address")

    @property
    def type_name(self) -> str:
        """Return the exact runtime type name for this FFI value."""
        return self.ffi_type

    def acquire_lease(self) -> None:
        """Pin the native allocation while one host call may access it."""
        if not self.alive or self.destroy_pending:
            raise ValueError(f"{self.ffi_type} handle is not live")
        self.active_leases += 1

    def release_lease(self) -> None:
        """Release one completed host-call lease."""
        if self.active_leases < 1:
            raise ValueError("native handle lease underflow")
        self.active_leases -= 1
        if self.active_leases == 0 and self.destroy_pending:
            self.alive = False

    def request_destroy(self) -> None:
        """Prevent future calls and invalidate after the final active lease."""
        if not self.alive or self.destroy_pending:
            raise ValueError(f"{self.ffi_type} handle has already been released")
        self.destroy_pending = True
        if self.active_leases == 0:
            self.alive = False

    def invalidate(self) -> None:
        """Mark this shared native handle identity as no longer usable."""
        self.request_destroy()

    def __str__(self) -> str:
        """Return the user-facing representation of this FFI value."""
        state = "live" if self.alive else "released"
        return f"{self.ffi_type}<opaque {state}>"


@dataclass(frozen=True, slots=True)
class FFIBufferValue:
    """Copied rank-one native array of primitive or linked C struct values."""
    element_type: str
    values: tuple["FFIScalarValue | FFIStructValue | ObjectValue", ...]

    def __post_init__(self) -> None:
        """Require each element to be a matching scalar or copied C struct."""
        if not self.element_type.startswith("&") or self.element_type.endswith("+"):
            raise ValueError("FFI buffer element type must be a scalar or struct")
        for value in self.values:
            if not isinstance(value, (FFIScalarValue, FFIStructValue, ObjectValue)):
                raise TypeError("FFI buffer items must be scalar or struct values")
            actual_type = (
                value.type_name if isinstance(value, ObjectValue) else value.ffi_type
            )
            if actual_type != self.element_type:
                raise TypeError("FFI buffer items must match the declared element type")

    @property
    def ffi_type(self) -> str:
        """Return the rank-one FFI buffer type spelling."""
        return self.element_type + "+"

    @property
    def type_name(self) -> str:
        """Return the exact runtime type name for this FFI value."""
        return self.ffi_type

    def __iter__(self):
        """Iterate over the immutable FFI buffer values."""
        return iter(self.values)

    def __len__(self) -> int:
        """Return the number of values in this FFI buffer."""
        return len(self.values)

    def __str__(self) -> str:
        """Return the user-facing representation of this FFI value."""
        return f"{self.ffi_type}[" + ", ".join(
            str(item.value) if isinstance(item, FFIScalarValue)
            else format_runtime_value(item)
            for item in self.values
        ) + "]"


@dataclass(frozen=True, slots=True)
class FFIStructValue:
    """Immutable first-class value for one plain declaration-order C struct."""
    ffi_type: str
    fields: tuple[tuple[str, Any], ...]

    def __post_init__(self) -> None:
        """Validate and normalize this FFI runtime value after construction."""
        if not self.ffi_type.startswith("&"):
            raise ValueError("FFI struct type must begin with '&'")
        names = tuple(name for name, _ in self.fields)
        if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
            raise ValueError("FFI struct fields require unique non-empty names")

    @property
    def type_name(self) -> str:
        """Return the exact runtime type name for this FFI value."""
        return self.ffi_type

    def field(self, name: str) -> Any:
        """Return a named field from this copied native structure."""
        for field_name, value in self.fields:
            if field_name == name:
                return value
        raise KeyError(name)

    def with_field(self, name: str, value: Any) -> "FFIStructValue":
        """Return a reconstructed native structure with one field replaced."""
        if name not in {field_name for field_name, _ in self.fields}:
            raise KeyError(name)
        return FFIStructValue(self.ffi_type, tuple((field_name, value if field_name == name else current) for field_name, current in self.fields))

    def __str__(self) -> str:
        """Return the user-facing representation of this FFI value."""
        return self.ffi_type + "{" + ", ".join(f"{name}: {format_runtime_value(value)}" for name, value in self.fields) + "}"


@dataclass(frozen=True, slots=True)
class FFIScalarValue:
    """An immutable first-class scalar carrying exact FFI type identity.

    The payload is deliberately opaque to ordinary Valiance operations.  Phase C
    conversions and later native-call lowering are the only facilities expected
    to inspect or construct these values in language code.  Keeping the value
    immutable gives it ordinary copy, storage, collection, closure, and task
    transfer semantics without introducing native-resource ownership.
    """

    ffi_type: str
    value: None | bool | int | float | str

    def __post_init__(self) -> None:
        """Validate the canonical type spelling and portable scalar payload."""
        if (
            not isinstance(self.ffi_type, str)
            or not self.ffi_type.startswith("&")
            or len(self.ffi_type) == 1
            or any(character.isspace() for character in self.ffi_type)
        ):
            raise ValueError("FFI scalar type must be a canonical '&'-prefixed name")
        if self.ffi_type.endswith(("+", "*", "~", "?")):
            raise ValueError("FFI scalar type cannot be a Valiance collection or optional")
        if self.value is not None and not isinstance(
            self.value, (bool, int, float, str)
        ):
            raise TypeError(
                "FFI scalar payload must be None, bool, int, float, or str"
            )

    @property
    def type_name(self) -> str:
        """Return the exact runtime type spelling used by casts and dispatch."""
        return self.ffi_type

    def __str__(self) -> str:
        """Render a typed scalar without pretending it is a Valiance primitive."""
        return f"{self.ffi_type}({self.value!r})"


@dataclass
class ObjectLifecycleState:
    """Mutable protocol state propagated across versions of a logical object."""

    mustcall_called: frozenset[str] = field(default_factory=frozenset)


@dataclass
class ObjectValue:
    """A nominal structured runtime value."""

    type_name: str
    fields: dict[str, Any]
    type_args: tuple[str, ...] = ()
    runtime_type: ObjectRuntimeType | None = field(
        default=None,
        compare=False,
        repr=False,
    )
    refcount: int = field(default=1, compare=False, repr=False)
    lifecycle_state: ObjectLifecycleState = field(
        default_factory=ObjectLifecycleState,
        compare=False,
        repr=False,
    )
    # Trait evidence belongs to a value occurrence (TaggedValue), not to
    # this shared nominal object: a single object can have multiple views.
    cleaning_up: bool = field(default=False, compare=False, repr=False)
    destructor_borrowed: bool = field(default=False, compare=False, repr=False)
    destroyed: bool = field(default=False, compare=False, repr=False)
    ownership_transferred: bool = field(default=False, compare=False, repr=False)

    @property
    def mustcall_called(self) -> frozenset[str]:
        """Return contractual calls propagated across object versions."""
        return self.lifecycle_state.mustcall_called

    @mustcall_called.setter
    def mustcall_called(self, called: frozenset[str]) -> None:
        """Update contractual calls for versions sharing this protocol state."""
        self.lifecycle_state.mustcall_called = called


class PanicSignal(Exception):
    """Internal runtime signal carrying a Valiance panic value."""

    def __init__(self, value: Any):
        """Initialize this panic signal."""
        super().__init__(value)
        self.value = value

    @property
    def recoverable(self) -> bool:
        """Keep ownership-integrity failures outside language panic handlers."""
        value = unwrap_runtime_value(self.value)
        return not (isinstance(value, ObjectValue) and value.type_name == "DuplicationFault")


def is_recoverable_fault(error: BaseException) -> bool:
    """Reject recovery when a primary or secondary fault is an integrity failure."""
    pending = [error]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        if not getattr(current, "recoverable", True):
            return False
        pending.extend(getattr(current, "secondary_faults", ()))
    return True


from .bigdecimal import BigDecimal


class NumericContext:
    # Minimum digits generated for inherently inexact operations.
    min_precision = 100

    # Extra digits to reduce rounding error.
    guard_digits = 10


ZERO = BigDecimal(0)


def _number_component_from_string(value: str) -> BigDecimal:
    """Parse one real component, including real-valued decimal exponents."""
    lower = value.lower()
    if "e" not in lower:
        return BigDecimal.from_string(value)
    mantissa_text, exponent_text = lower.split("e", 1)
    if "." not in exponent_text:
        return BigDecimal.from_string(value)

    mantissa = Decimal(mantissa_text)
    exponent = Decimal(exponent_text)
    with localcontext() as ctx:
        ctx.prec = NumericContext.min_precision
        result = mantissa * (Decimal(10) ** exponent)
    return BigDecimal.from_decimal(result)


def inferred_precision(*values: BigDecimal) -> int:
    """
    Estimate a useful working precision for an inexact operation.
    """
    if not values:
        return NumericContext.min_precision

    digits = max(v.digits() for v in values)
    scale = sum(abs(v.exponent) for v in values)

    return max(
        NumericContext.min_precision,
        digits + scale + NumericContext.guard_digits,
    )


class RuntimeNumber:
    __slots__ = ("real", "imag")

    def __init__(self, value: object = 0):
        """Initialize this runtime number from a value of any supported type."""
        if isinstance(value, complex):
            self.real = BigDecimal.from_value(value.real)
            self.imag = BigDecimal.from_value(value.imag)
        elif isinstance(value, RuntimeNumber):
            self.real = value.real
            self.imag = value.imag
        elif isinstance(value, BigDecimal):
            self.real = value
            self.imag = ZERO
        elif isinstance(value, Decimal):
            self.real = BigDecimal.from_decimal(value)
            self.imag = ZERO
        elif isinstance(value, int):
            self.real = BigDecimal.from_int(value)
            self.imag = ZERO
        elif isinstance(value, float):
            self.real = BigDecimal.from_float(value)
            self.imag = ZERO
        elif isinstance(value, str):
            if "i" in value:
                parts = value.split("i", 1)
                self.real = _number_component_from_string(parts[0])
                self.imag = (
                    ZERO if parts[1] == "" else _number_component_from_string(parts[1])
                )
            else:
                self.real = _number_component_from_string(value)
                self.imag = ZERO
        elif isinstance(value, tuple) and len(value) == 2:
            if isinstance(value[0], BigDecimal) and isinstance(value[1], BigDecimal):
                self.real = value[0]
                self.imag = value[1]
            elif isinstance(value[0], int) and isinstance(value[1], int):
                self.real = BigDecimal.from_int(value[0])
                self.imag = BigDecimal.from_int(value[1])
            elif isinstance(value[0], float) and isinstance(value[1], float):
                self.real = BigDecimal.from_float(value[0])
                self.imag = BigDecimal.from_float(value[1])
            elif isinstance(value[0], Decimal) and isinstance(value[1], Decimal):
                self.real = BigDecimal.from_decimal(value[0])
                self.imag = BigDecimal.from_decimal(value[1])
            elif isinstance(value[0], str) and isinstance(value[1], str):
                self.real = BigDecimal.from_string(value[0])
                self.imag = BigDecimal.from_string(value[1])
            elif isinstance(value[0], RuntimeNumber) and isinstance(
                value[1], RuntimeNumber
            ):
                self.real = value[0].real
                self.imag = value[1].imag
            else:
                raise TypeError(f"Unsupported numeric type: {type(value).__name__}")
        else:
            raise TypeError(f"Unsupported numeric type: {type(value).__name__}")

    # ---------- construction ----------

    @classmethod
    def from_value(cls, value: object):
        """Construct a runtime number from a value of any supported type."""
        if isinstance(value, cls):
            return value

        if isinstance(value, complex):
            return cls(value.real, value.imag)

        return cls(value)

    @staticmethod
    def _convert(value: object):
        """Convert a value of any supported type to a runtime number."""
        if isinstance(value, BigDecimal):
            return value

        if isinstance(value, Decimal):
            return BigDecimal.from_decimal(value)

        if isinstance(value, int):
            return BigDecimal.from_int(value)

        if isinstance(value, float):
            # Compatibility with existing callers.
            return BigDecimal.from_float(value)

        if isinstance(value, str):
            return BigDecimal.from_string(value)

        raise TypeError(f"Unsupported numeric type: {type(value).__name__}")

    # ---------- representation ----------

    def __repr__(self):
        """Return a string representation of this runtime number."""
        if self.imag.is_zero():
            return str(self.real)

        return f"{self.real}i{self.imag}"

    __str__ = __repr__

    # ---------- comparisons ----------

    def __eq__(self, other: object):
        """Return whether this runtime number equals another value."""
        if not isinstance(
            other, (RuntimeNumber, complex, BigDecimal, Decimal, int, float)
        ):
            return NotImplemented
        try:
            other = RuntimeNumber.from_value(other)
        except TypeError:
            return NotImplemented

        return self.real == other.real and self.imag == other.imag

    def __hash__(self) -> int:
        """Return a value-consistent hash for dictionary-key use."""
        if self.imag.is_zero() and self.real.is_integer():
            return hash(self.real.to_int())
        return hash((self.real, self.imag))

    def _require_real(self, other):
        """Require that another value is a real number for ordering comparisons."""
        other = RuntimeNumber.from_value(other)

        if not self.imag.is_zero() or not other.imag.is_zero():
            raise TypeError("Ordering is undefined for complex numbers")

        return other

    def __lt__(self, other):
        """Return whether this runtime number is less than another value."""
        return self.real < self._require_real(other).real

    def __le__(self, other):
        """Return whether this runtime number is less than or equal to another value."""
        return self.real <= self._require_real(other).real

    def __gt__(self, other):
        """Return whether this runtime number is greater than another value."""
        return self.real > self._require_real(other).real

    def __ge__(self, other):
        """Return whether this runtime number is greater than or equal to another value."""
        return self.real >= self._require_real(other).real

    # ---------- exact arithmetic ----------

    def __add__(self, other):
        """Add this runtime number to another value."""
        other = RuntimeNumber.from_value(other)
        if self.imag.is_zero() and other.imag.is_zero():
            return RuntimeNumber(self.real + other.real)
        return RuntimeNumber(
            (
                self.real + other.real,
                self.imag + other.imag,
            )
        )

    def __radd__(self, other):
        """Add another value to this runtime number."""
        return self + other

    def __sub__(self, other):
        """Subtract another value from this runtime number."""
        other = RuntimeNumber.from_value(other)
        if self.imag.is_zero() and other.imag.is_zero():
            return RuntimeNumber(self.real - other.real)
        return RuntimeNumber((self.real - other.real, self.imag - other.imag))

    def __rsub__(self, other):
        """Subtract this runtime number from another value."""
        return RuntimeNumber.from_value(other) - self

    def __mul__(self, other):
        """Multiply this runtime number by another value."""
        other = RuntimeNumber.from_value(other)
        if self.imag.is_zero() and other.imag.is_zero():
            return RuntimeNumber(self.real * other.real)
        return RuntimeNumber(
            (
                self.real * other.real - self.imag * other.imag,
                self.real * other.imag + self.imag * other.real,
            )
        )

    def __rmul__(self, other):
        """Multiply another value by this runtime number."""
        return self * other

    # ---------- division ----------

    def __truediv__(self, other):
        """Divide this runtime number by another value."""
        other = RuntimeNumber.from_value(other)

        if not self.imag.is_zero() or not other.imag.is_zero():
            return self._complex_divide(other)

        return RuntimeNumber(
            self._divide_decimal(
                self.real,
                other.real,
            )
        )

    def _divide_decimal(self, a, b):
        """Divide two BigDecimal values with inferred precision."""
        precision = inferred_precision(a, b)

        with localcontext() as ctx:
            ctx.prec = precision

            result = a.to_decimal(precision) / b.to_decimal(precision)

        return BigDecimal.from_decimal(result)

    def _complex_divide(self, other):
        """Divide this runtime number by another value using complex arithmetic."""
        denominator = other.real * other.real + other.imag * other.imag

        return RuntimeNumber(
            (
                (self.real * other.real + self.imag * other.imag)
                / RuntimeNumber(denominator),
                (self.imag * other.real - self.real * other.imag)
                / RuntimeNumber(denominator),
            ),
        )

    def __rtruediv__(self, other):
        """Divide another value by this runtime number."""
        return RuntimeNumber.from_value(other) / self

    # ---------- power ----------

    def __pow__(self, other):
        """Raise this number to another number on the principal branch."""
        other = RuntimeNumber.from_value(other)

        if other.imag.is_zero() and other.real.is_integer():
            exponent = other.real.to_int()
            if exponent < 0:
                return RuntimeNumber(1) / (self ** -exponent)

            result = RuntimeNumber(1)
            for _ in range(exponent):
                result *= self
            return result

        precision = inferred_precision(
            self.real,
            self.imag,
            other.real,
            other.imag,
        )

        if self.imag.is_zero() and other.imag.is_zero():
            base_decimal = self.real.to_decimal(precision)
            exponent_decimal = other.real.to_decimal(precision)

            if self.real >= ZERO:
                with localcontext() as ctx:
                    ctx.prec = precision
                    result = base_decimal**exponent_decimal
                return RuntimeNumber(BigDecimal.from_decimal(result))

            # Prefer the real value for recognizable odd-denominator rational
            # powers, such as (-8) ** (1 / 3). Other negative-base powers use
            # the principal complex branch below.
            rational = Fraction(exponent_decimal).limit_denominator(1_000_000)
            with localcontext() as ctx:
                ctx.prec = precision
                approximation = Decimal(rational.numerator) / Decimal(
                    rational.denominator
                )
                tolerance = Decimal(10) ** min(-16, other.real.exponent)
                is_rational = abs(exponent_decimal - approximation) <= tolerance
            if rational.denominator % 2 == 1 and is_rational:
                with localcontext() as ctx:
                    ctx.prec = precision
                    magnitude = (-base_decimal) ** (
                        Decimal(abs(rational.numerator))
                        / Decimal(rational.denominator)
                    )
                    if rational.numerator < 0:
                        magnitude = Decimal(1) / magnitude
                if rational.numerator % 2:
                    magnitude = -magnitude
                return RuntimeNumber(BigDecimal.from_decimal(magnitude))

        base = complex(
            float(self.real.to_decimal(precision)),
            float(self.imag.to_decimal(precision)),
        )
        exponent = complex(
            float(other.real.to_decimal(precision)),
            float(other.imag.to_decimal(precision)),
        )
        result = base**exponent

        # Complex arithmetic can leave a rounding-sized imaginary component
        # on a mathematically real result, such as i ** 2.
        tolerance = 1e-15 * max(1.0, abs(result.real), abs(result.imag))
        real = 0.0 if abs(result.real) <= tolerance else result.real
        imag = 0.0 if abs(result.imag) <= tolerance else result.imag
        return RuntimeNumber((float(real), float(imag)))

    def __rpow__(self, other):
        """Raise another number to this number on the principal branch."""
        return RuntimeNumber.from_value(other) ** self

    def __mod__(self, other):
        """Return the modulus of this runtime number and another value."""
        other = RuntimeNumber.from_value(other)

        if not self.imag.is_zero() or not other.imag.is_zero():
            raise TypeError("Modulo is only defined for real numbers")

        return RuntimeNumber(self.real % other.real)

    def __rmod__(self, other):
        """Return the modulus of another value and this runtime number."""
        return RuntimeNumber.from_value(other) % self

    # ---------- unary ----------

    def __neg__(self):
        """Negate this runtime number."""
        return RuntimeNumber(
            -self.real,
            -self.imag,
        )

    def __bool__(self):
        """Return whether this runtime number is non-zero."""
        return not self.real.is_zero()

    # ---------- helpers ----------

    def is_complex(self):
        """Return whether this runtime number has a non-zero imaginary part."""
        return not self.imag.is_zero()

    def is_integer(self):
        """Return whether this runtime number is an integer."""
        return self.imag.is_zero() and self.real.is_integer()

    def is_finite(self):
        """Return whether this runtime number is finite."""
        return True

    def to_integral_value(self):
        """Return the integral value of this runtime number."""
        return RuntimeNumber(
            (
                self.real.to_int(),
                self.imag.to_int(),
            )
        )

    def scaleb(self, n: int) -> RuntimeNumber:
        """Return this runtime number scaled by 10**n."""
        return RuntimeNumber(
            (
                self.real.scaleb(n),
                self.imag.scaleb(n),
            )
        )

    # ---------- python conversion ----------

    def __int__(self):
        """Return the integer value of this runtime number."""
        if not self.imag.is_zero():
            raise TypeError("Cannot convert complex RuntimeNumber to int")

        return self.real.to_int()

    def __float__(self):
        """Return the float value of this runtime number."""
        if not self.imag.is_zero():
            raise TypeError("Cannot convert complex RuntimeNumber to float")

        return float(self.real.to_decimal())

    def __format__(self, format_spec: str) -> str:
        """Return a formatted string representation of this number."""
        if self.imag == ZERO:
            return format(self.real, format_spec)
        return format(self.real, format_spec) + "i" + format(self.imag, format_spec)


DIAGNOSTIC_LIST_PREVIEW_LIMIT = 100


def is_list_like(value: Any) -> bool:
    """Return whether a runtime value behaves like a Valiance list."""
    value = unwrap_runtime_value(value)
    return isinstance(value, Iterable) and not isinstance(
        value, (str, bytes, tuple, Mapping)
    )


def is_finite_list_like(value: Any) -> bool:
    """Return whether a list-like value has a known finite length."""
    value = unwrap_runtime_value(value)
    return is_list_like(value) and isinstance(value, Sized)


def is_eager_sequence(value: Any) -> bool:
    """Return whether a list-like value can be indexed without consumption."""
    value = unwrap_runtime_value(value)
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, tuple))


def runtime_collection_rank(value: Any) -> int | None:
    """Return the exact uniform rank carried by or observable from a list value."""
    value = unwrap_runtime_value(value)
    recorded = getattr(value, "runtime_rank", None)
    if isinstance(recorded, int) and recorded >= 1:
        return recorded
    if not is_eager_sequence(value):
        return None
    if not value:
        return 1

    child_ranks = tuple(runtime_collection_rank(item) for item in value)
    list_children = tuple(rank is not None for rank in child_ranks)
    if not any(list_children):
        return 1
    if not all(list_children):
        return None
    first = child_ranks[0]
    if first is None or any(rank != first for rank in child_ranks[1:]):
        return None
    return first + 1


def with_runtime_collection_rank(value: Any, rank: int | None) -> Any:
    """Attach exact collection-rank evidence without changing value semantics."""
    if rank is None:
        return value
    wrapped = unwrap_runtime_value(value)
    tags = runtime_value_tags(value)
    if isinstance(wrapped, LazyList):
        wrapped.runtime_rank = rank
        return TaggedValue(wrapped, tags) if tags else wrapped
    if isinstance(wrapped, ListValue):
        wrapped.runtime_rank = rank
        return TaggedValue(wrapped, tags) if tags else wrapped
    if isinstance(wrapped, list):
        ranked = ListValue(wrapped, runtime_rank=rank)
        return TaggedValue(ranked, tags) if tags else ranked
    return value


def format_runtime_value(
    value: Any,
    *,
    quote_strings: bool = False,
    tuple_single_comma: bool = False,
    lazy_preview_limit: int | None = None,
) -> str:
    """Format output, quoting strings inside aggregates and optionally at top level."""
    value = unwrap_runtime_value(value)
    options = {
        "quote_strings": True,
        "tuple_single_comma": tuple_single_comma,
        "lazy_preview_limit": lazy_preview_limit,
    }
    if isinstance(value, (FFIScalarValue, FFIBufferValue, FFIStructValue, FFIHandleValue)):
        return str(value)
    if isinstance(value, (RuntimeNumber, int, float, Decimal)):
        rendered = format(value, "f")
        return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered
    if isinstance(value, str):
        return repr(value) if quote_strings else value
    if isinstance(value, list):
        items: Iterable[Any] = value
        has_more = False
        if lazy_preview_limit is not None and len(value) > lazy_preview_limit:
            items, has_more = value[:lazy_preview_limit], True
        return _format_list_items(items, has_more=has_more, **options)
    if is_list_like(value):
        if lazy_preview_limit is None:
            return _format_list_items(value, **options)
        preview = list(islice(iter(value), lazy_preview_limit + 1))
        has_more = len(preview) > lazy_preview_limit
        return _format_list_items(
            preview[:lazy_preview_limit], has_more=has_more, **options
        )
    if isinstance(value, tuple):
        inner = ", ".join(format_runtime_value(item, **options) for item in value)
        if tuple_single_comma and len(value) == 1:
            inner += ","
        return f"({inner})"
    if isinstance(value, RecordValue):
        items = ", ".join(
            f"{name} => {format_runtime_value(item, **options)}"
            for name, item in value.items()
        )
        return f"record{{{items}}}"
    if isinstance(value, dict):
        items = []
        for key, item in value.items():
            key = unwrap_runtime_value(key)
            rendered_key = (
                json.dumps(key, ensure_ascii=False)
                if isinstance(key, str)
                else format_runtime_value(key, **options)
            )
            items.append(f"{rendered_key} => {format_runtime_value(item, **options)}")
        return "{" + ", ".join(items) + "}"
    if isinstance(value, ObjectValue):
        items = ", ".join(
            f"{name}: {format_runtime_value(item, **options)}"
            for name, item in value.fields.items()
        )
        return f"{object_type_name(value)}{{{items}}}"
    return repr(value) if quote_strings else str(value)


def _format_list_items(
    items: Iterable[Any],
    *,
    quote_strings: bool,
    tuple_single_comma: bool,
    lazy_preview_limit: int | None,
    has_more: bool = False,
) -> str:
    """Format a sequence using the active recursive formatting options."""
    rendered = [
        format_runtime_value(
            item,
            quote_strings=quote_strings,
            tuple_single_comma=tuple_single_comma,
            lazy_preview_limit=lazy_preview_limit,
        )
        for item in items
    ]
    if has_more:
        rendered.append("...")
    return "[" + ", ".join(rendered) + "]"


def object_type_name(value: ObjectValue) -> str:
    """Return the canonical runtime name of an object value's type."""
    if not value.type_args:
        return value.type_name
    return f"{value.type_name}[{', '.join(value.type_args)}]"
