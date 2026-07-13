"""Helpers for runtime values shared by builtins, the VM, and the CLI."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence, Sized
from dataclasses import dataclass, field
from functools import lru_cache
from decimal import Decimal
from itertools import islice
import json
from typing import Any

from valiance.types import DataTag


@dataclass
class LazyList:
    """A list-like value backed by an iterable that may be lazy or infinite."""

    iterable: Iterable[Any]
    runtime_rank: int | None = field(default=None, compare=False, repr=False)
    owned_values: tuple[Any, ...] = field(default=(), compare=False, repr=False)
    refcount: int = field(default=1, compare=False, repr=False)

    def __iter__(self):
        """Iterate over values stored by this lazy list."""
        return iter(self.iterable)

    def __eq__(self, other: object) -> bool:
        """Return whether this lazy list equals another value."""
        if isinstance(other, LazyList):
            return list(self) == list(other)
        if isinstance(other, Sequence) and not isinstance(other, (str, bytes, tuple)):
            return list(self) == list(other)
        return False


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

    def _invalidate_ownership_cache(self) -> None:
        """Forget whether every direct item is ownership-trivial."""
        self._ownership_trivial = None

    def __setitem__(self, key: Any, value: Any) -> None:
        """Set one item and invalidate cached ownership metadata."""
        super().__setitem__(key, value)
        self._invalidate_ownership_cache()

    def __delitem__(self, key: Any) -> None:
        """Delete one item and invalidate cached ownership metadata."""
        super().__delitem__(key)
        self._invalidate_ownership_cache()

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
        super().__setitem__(key, value)
        self._invalidate_ownership_cache()

    def __delitem__(self, key: Any) -> None:
        """Delete one item and invalidate cached ownership metadata."""
        super().__delitem__(key)
        self._invalidate_ownership_cache()

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


@dataclass(frozen=True, eq=False)
class TaggedValue:
    """A runtime value carrying reified data-tag evidence."""

    value: Any
    tags: frozenset[DataTag] = field(default_factory=frozenset)

    def __iter__(self):
        """Iterate over values stored by this tagged value."""
        return iter(self.value)

    def __len__(self) -> int:
        """Return the number of values stored by this tagged value."""
        return len(self.value)

    def __getitem__(self, index: Any) -> Any:
        """Return an item selected from this tagged value."""
        return self.value[index]

    def __eq__(self, other: object) -> bool:
        """Return whether this tagged value equals another value."""
        return self.value == unwrap_runtime_value(other)


def unwrap_runtime_value(value: Any) -> Any:
    """Return the payload beneath any runtime tag evidence wrapper."""
    return value.value if isinstance(value, TaggedValue) else value


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
    else:
        payload = value
        current = frozenset()

    if not remove:
        additions = _cached_runtime_tag_additions(add)
        tags = additions if not current else current | additions
        if tags == current:
            return value
    else:
        removed = {(tag.name, tag.depth) for tag in remove}
        tags = frozenset(tag for tag in current if (tag.name, tag.depth) not in removed)
        tags = tags.union(tag for tag in add if not tag.absent)

    if not tags:
        return payload
    if isinstance(payload, (Decimal, str, int, bool, type(None))):
        return _cached_tagged_scalar(payload, tags)
    return TaggedValue(payload, tags)


@dataclass(frozen=True, slots=True)
class ObjectRuntimeType:
    """Runtime lifecycle metadata attached to nominal object values."""

    destructor_name: str | None = None
    pop_name: str | None = None
    dup_name: str | None = None
    dup_error: str | None = None
    mustcall_mode: str | None = None
    mustcall_methods: tuple[str, ...] = ()
    accepted_names: tuple[str, ...] = ()
    generic_variances: tuple[str, ...] = ()
    type_facts: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = ()
    generic_supertypes: tuple[tuple[str, tuple[str, ...]], ...] = ()


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
    mustcall_called: frozenset[str] = field(
        default_factory=frozenset,
        compare=False,
        repr=False,
    )
    cleaning_up: bool = field(default=False, compare=False, repr=False)
    destroyed: bool = field(default=False, compare=False, repr=False)


class PanicSignal(Exception):
    """Internal runtime signal carrying a Valiance panic value."""

    def __init__(self, value: Any):
        """Initialize this panic signal."""
        super().__init__(value)
        self.value = value


ZERO = Decimal(0)


class Number:
    __slots__ = ("real", "imag")

    @staticmethod
    def _to_decimal(value: object) -> Decimal:
        if isinstance(value, Decimal):
            return value
        if isinstance(value, int):
            return Decimal(value)
        if isinstance(value, str):
            return Decimal(value)
        raise TypeError(f"Unsupported numeric value: {value!r}")

    def __init__(self, value: object = 0):
        if isinstance(value, Number):
            self.real = value.real
            self.imag = value.imag

        elif isinstance(value, complex):
            self.real = self._to_decimal(str(value.real))
            self.imag = self._to_decimal(str(value.imag))

        elif isinstance(value, tuple) and len(value) == 2:
            self.real = self._to_decimal(value[0])
            self.imag = self._to_decimal(value[1])

        elif isinstance(value, str):
            if "i" in value.lower():
                parts = value.lower().split("i")
                if len(parts) == 2:
                    self.real = self._to_decimal(parts[0]) if parts[0] else ZERO
                    self.imag = self._to_decimal(parts[1]) if parts[1] else ZERO
                else:
                    raise ValueError(f"Invalid complex number string: {value}")
            else:
                self.real = self._to_decimal(value)
                self.imag = ZERO

        elif isinstance(value, (int, float, Decimal)):
            self.real = self._to_decimal(value)
            self.imag = ZERO

        else:
            self.real = self._to_decimal(value)
            self.imag = ZERO

    def __repr__(self) -> str:
        if self.imag == ZERO:
            return f"Number({self.real})"
        return f"Number({self.real}, {self.imag})"

    def __str__(self) -> str:
        if self.imag == ZERO:
            return str(self.real)
        return f"{self.real}i{self.imag}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Number):
            return NotImplemented
        return self.real == other.real and self.imag == other.imag

    def __add__(self, other: object) -> Number:
        if not isinstance(other, Number):
            return NotImplemented
        return Number((self.real + other.real, self.imag + other.imag))

    def __sub__(self, other: object) -> Number:
        if not isinstance(other, Number):
            return NotImplemented
        return Number((self.real - other.real, self.imag - other.imag))

    def __mul__(self, other: object) -> Number:
        if not isinstance(other, Number):
            return NotImplemented
        return Number(
            (
                self.real * other.real - self.imag * other.imag,
                self.real * other.imag + self.imag * other.real,
            )
        )

    def __truediv__(self, other: object) -> Number:
        if not isinstance(other, Number):
            return NotImplemented
        denom = other.real**2 + other.imag**2
        return Number(
            (
                (self.real * other.real + self.imag * other.imag) / denom,
                (self.imag * other.real - self.real * other.imag) / denom,
            )
        )

    def __mod__(self, other: object) -> Number:
        if not isinstance(other, Number):
            return NotImplemented
        if self.imag != ZERO or other.imag != ZERO:
            raise ValueError("Modulo operation is only defined for real numbers.")
        return Number(self.real % other.real)

    def __pow__(self, other: object) -> Number:
        if not isinstance(other, Number):
            return NotImplemented
        if self.imag != ZERO or other.imag != ZERO:
            raise ValueError("Power operation is only defined for real numbers.")
        return Number(self.real**other.real)

    def __neg__(self) -> Number:
        return Number((-self.real, -self.imag))

    def __abs__(self) -> Number:
        return Number((abs(self.real), abs(self.imag)))

    def __bool__(self) -> bool:
        return self.real != ZERO or self.imag != ZERO

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Number):
            return NotImplemented
        return self.real <= other.real and self.imag <= other.imag

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Number):
            return NotImplemented
        return self.real < other.real and self.imag < other.imag

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Number):
            return NotImplemented
        return self.real >= other.real and self.imag >= other.imag

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Number):
            return NotImplemented
        return self.real > other.real and self.imag > other.imag

    def to_integral_value(self) -> Number:
        """Return the integral part of this number."""
        return Number((self.real.to_integral_value(), self.imag.to_integral_value()))


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
    """Format a runtime value for user-visible output and diagnostics."""
    value = unwrap_runtime_value(value)
    if isinstance(value, Decimal):
        rendered = format(value, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return rendered
    if isinstance(value, str):
        return repr(value) if quote_strings else value
    if isinstance(value, list):
        items: Iterable[Any] = value
        has_more = False
        if lazy_preview_limit is not None and len(value) > lazy_preview_limit:
            items = value[:lazy_preview_limit]
            has_more = True
        return _format_list_items(
            items,
            quote_strings=quote_strings,
            tuple_single_comma=tuple_single_comma,
            lazy_preview_limit=lazy_preview_limit,
            has_more=has_more,
        )
    if is_list_like(value):
        return _format_lazy_list(
            value,
            quote_strings=quote_strings,
            tuple_single_comma=tuple_single_comma,
            lazy_preview_limit=lazy_preview_limit,
        )
    if isinstance(value, tuple):
        inner = ", ".join(
            format_runtime_value(
                item,
                quote_strings=quote_strings,
                tuple_single_comma=tuple_single_comma,
                lazy_preview_limit=lazy_preview_limit,
            )
            for item in value
        )
        if tuple_single_comma and len(value) == 1:
            inner += ","
        return f"({inner})"
    if isinstance(value, dict):
        items = ", ".join(
            _format_mapping_item(
                key,
                item,
                quote_strings,
                tuple_single_comma,
                lazy_preview_limit,
            )
            for key, item in value.items()
        )
        return "{" + items + "}"
    if isinstance(value, ObjectValue):
        items = ", ".join(
            _format_field_item(
                name,
                item,
                quote_strings,
                tuple_single_comma,
                lazy_preview_limit,
            )
            for name, item in value.fields.items()
        )
        return f"{_object_type_name(value)}{{{items}}}"
    return repr(value) if quote_strings else str(value)


def _format_lazy_list(
    value: Any,
    *,
    quote_strings: bool,
    tuple_single_comma: bool,
    lazy_preview_limit: int | None,
) -> str:
    """Format lazy list for shared runtime-value behaviour."""
    if lazy_preview_limit is None:
        return _format_list_items(
            value,
            quote_strings=quote_strings,
            tuple_single_comma=tuple_single_comma,
            lazy_preview_limit=lazy_preview_limit,
        )

    preview = list(islice(iter(value), lazy_preview_limit + 1))
    has_more = len(preview) > lazy_preview_limit
    if has_more:
        preview = preview[:lazy_preview_limit]
    return _format_list_items(
        preview,
        quote_strings=quote_strings,
        tuple_single_comma=tuple_single_comma,
        lazy_preview_limit=lazy_preview_limit,
        has_more=has_more,
    )


def _format_list_items(
    items: Iterable[Any],
    *,
    quote_strings: bool,
    tuple_single_comma: bool,
    lazy_preview_limit: int | None,
    has_more: bool = False,
) -> str:
    """Format list items for shared runtime-value behaviour."""
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


def _format_nested(
    value: Any,
    quote_strings: bool,
    tuple_single_comma: bool,
    lazy_preview_limit: int | None,
) -> str:
    """Format nested for shared runtime-value behaviour."""
    return format_runtime_value(
        value,
        quote_strings=quote_strings,
        tuple_single_comma=tuple_single_comma,
        lazy_preview_limit=lazy_preview_limit,
    )


def _format_mapping_item(
    key: Any,
    item: Any,
    quote_strings: bool,
    tuple_single_comma: bool,
    lazy_preview_limit: int | None,
) -> str:
    """Format mapping item for shared runtime-value behaviour."""
    rendered_key = _format_mapping_key(
        key,
        tuple_single_comma,
        lazy_preview_limit,
    )
    rendered_item = _format_nested(
        item,
        quote_strings,
        tuple_single_comma,
        lazy_preview_limit,
    )
    return f"{rendered_key}: {rendered_item}"


def _format_mapping_key(
    key: Any,
    tuple_single_comma: bool,
    lazy_preview_limit: int | None,
) -> str:
    """Format mapping key for shared runtime-value behaviour."""
    key = unwrap_runtime_value(key)
    if isinstance(key, str):
        return json.dumps(key, ensure_ascii=False)
    return _format_nested(
        key,
        True,
        tuple_single_comma,
        lazy_preview_limit,
    )


def _format_field_item(
    name: str,
    item: Any,
    quote_strings: bool,
    tuple_single_comma: bool,
    lazy_preview_limit: int | None,
) -> str:
    """Format field item for shared runtime-value behaviour."""
    rendered_item = _format_nested(
        item,
        quote_strings,
        tuple_single_comma,
        lazy_preview_limit,
    )
    return f"{name}: {rendered_item}"


def _object_type_name(value: ObjectValue) -> str:
    """Return the canonical name for object type for shared runtime-value behaviour."""
    if not value.type_args:
        return value.type_name
    return f"{value.type_name}[{', '.join(value.type_args)}]"
