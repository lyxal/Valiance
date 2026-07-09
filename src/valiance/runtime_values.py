"""Helpers for runtime values shared by builtins, the VM, and the CLI."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence, Sized
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import islice
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
        return iter(self.iterable)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, LazyList):
            return list(self) == list(other)
        if isinstance(other, Sequence) and not isinstance(other, (str, bytes, tuple)):
            return list(self) == list(other)
        return False


class ListValue(list[Any]):
    """An eager Valiance list carrying its known uniform runtime rank."""

    def __init__(
        self,
        iterable: Iterable[Any] = (),
        *,
        runtime_rank: int | None = None,
    ) -> None:
        super().__init__(iterable)
        self.runtime_rank = runtime_rank


@dataclass(frozen=True, eq=False)
class TaggedValue:
    """A runtime value carrying reified data-tag evidence."""

    value: Any
    tags: frozenset[DataTag] = field(default_factory=frozenset)

    def __iter__(self):
        return iter(self.value)

    def __len__(self) -> int:
        return len(self.value)

    def __getitem__(self, index: Any) -> Any:
        return self.value[index]

    def __eq__(self, other: object) -> bool:
        return self.value == unwrap_runtime_value(other)


def unwrap_runtime_value(value: Any) -> Any:
    """Return the payload beneath any runtime tag evidence wrapper."""
    return value.value if isinstance(value, TaggedValue) else value


def runtime_value_tags(value: Any) -> frozenset[DataTag]:
    """Return the reified data tags attached to a runtime value."""
    return value.tags if isinstance(value, TaggedValue) else frozenset()


def update_runtime_tags(
    value: Any,
    *,
    add: tuple[DataTag, ...] = (),
    remove: tuple[DataTag, ...] = (),
) -> Any:
    """Apply a tag-evidence delta without nesting wrappers."""
    payload = unwrap_runtime_value(value)
    tags = set(runtime_value_tags(value))
    removed = {(tag.name, tag.depth) for tag in remove}
    tags = {tag for tag in tags if (tag.name, tag.depth) not in removed}
    tags.update(tag for tag in add if not tag.absent)
    return TaggedValue(payload, frozenset(tags)) if tags else payload


@dataclass(frozen=True, slots=True)
class ObjectRuntimeType:
    """Runtime lifecycle metadata attached to nominal object values."""

    destructor_name: str | None = None
    pop_name: str | None = None
    dup_name: str | None = None
    dup_error: str | None = None
    mustcall_mode: str | None = None
    mustcall_methods: tuple[str, ...] = ()


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
        super().__init__(value)
        self.value = value


DIAGNOSTIC_LIST_PREVIEW_LIMIT = 100


def is_list_like(value: Any) -> bool:
    """Return whether a runtime value behaves like a Valiance list."""
    value = unwrap_runtime_value(value)
    return (
        isinstance(value, Iterable)
        and not isinstance(value, (str, bytes, tuple, Mapping))
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
    rendered_item = _format_nested(
        item,
        quote_strings,
        tuple_single_comma,
        lazy_preview_limit,
    )
    return f"{name}: {rendered_item}"


def _object_type_name(value: ObjectValue) -> str:
    if not value.type_args:
        return value.type_name
    return f"{value.type_name}[{', '.join(value.type_args)}]"
