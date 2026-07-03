"""Helpers for runtime values shared by builtins, the VM, and the CLI."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence, Sized
from dataclasses import dataclass
from decimal import Decimal
from itertools import islice
from typing import Any


@dataclass(frozen=True)
class LazyList:
    """A list-like value backed by an iterable that may be lazy or infinite."""

    iterable: Iterable[Any]

    def __iter__(self):
        return iter(self.iterable)


@dataclass(frozen=True)
class ObjectValue:
    """A nominal structured runtime value."""

    type_name: str
    fields: dict[str, Any]
    type_args: tuple[str, ...] = ()


class PanicSignal(Exception):
    """Internal runtime signal carrying a Valiance panic value."""

    def __init__(self, value: Any):
        super().__init__(value)
        self.value = value


DIAGNOSTIC_LIST_PREVIEW_LIMIT = 100


def is_list_like(value: Any) -> bool:
    """Return whether a runtime value behaves like a Valiance list."""
    return (
        isinstance(value, Iterable)
        and not isinstance(value, (str, bytes, tuple, Mapping))
    )


def is_finite_list_like(value: Any) -> bool:
    """Return whether a list-like value has a known finite length."""
    return is_list_like(value) and isinstance(value, Sized)


def is_eager_sequence(value: Any) -> bool:
    """Return whether a list-like value can be indexed without consumption."""
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, tuple))


def format_runtime_value(
    value: Any,
    *,
    quote_strings: bool = False,
    tuple_single_comma: bool = False,
    lazy_preview_limit: int | None = None,
) -> str:
    """Format a runtime value for user-visible output and diagnostics."""
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return format(value.quantize(Decimal(1)), "f")
        return format(value.normalize(), "f")
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
    rendered_key = _format_nested(
        key,
        quote_strings,
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
