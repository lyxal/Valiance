"""Helpers for runtime values shared by builtins, the VM, and the CLI."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence, Sized
from dataclasses import dataclass, field
from functools import lru_cache
from decimal import Decimal, localcontext
from itertools import islice
import json
from typing import Any

from valiance.vtypes import DataTag


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

        return RuntimeNumber((self.real - other.real, self.imag - other.imag))

    def __rsub__(self, other):
        """Subtract this runtime number from another value."""
        return RuntimeNumber.from_value(other) - self

    def __mul__(self, other):
        """Multiply this runtime number by another value."""
        other = RuntimeNumber.from_value(other)

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
        """Raise this runtime number to the power of another value."""
        other = RuntimeNumber.from_value(other)

        if other.imag.is_zero() and other.real.is_integer():
            exponent = other.real.to_int()

            if exponent >= 0:
                result = RuntimeNumber(1)

                for _ in range(exponent):
                    result *= self

                return result

        precision = inferred_precision(
            self.real,
            other.real,
        )

        with localcontext() as ctx:
            ctx.prec = precision

            result = self.real.to_decimal(precision) ** other.real.to_decimal(precision)

        return RuntimeNumber(BigDecimal.from_decimal(result))

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
    """Format a runtime value for user-visible output and diagnostics."""
    value = unwrap_runtime_value(value)
    options = {
        "quote_strings": quote_strings,
        "tuple_single_comma": tuple_single_comma,
        "lazy_preview_limit": lazy_preview_limit,
    }
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
    if isinstance(value, dict):
        items = []
        for key, item in value.items():
            key = unwrap_runtime_value(key)
            rendered_key = (
                json.dumps(key, ensure_ascii=False)
                if isinstance(key, str)
                else format_runtime_value(key, **(options | {"quote_strings": True}))
            )
            items.append(f"{rendered_key}: {format_runtime_value(item, **options)}")
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
