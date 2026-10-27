"""JSON token formatting and checked nested-data encoding."""

from __future__ import annotations

import json
from decimal import Decimal
from dataclasses import replace
from functools import lru_cache
from typing import Any

import valiance.vtypes as T
from valiance.elements.builtins import RuntimeContext
from valiance.elements.stdlib_native import stdlib_element
from valiance.runtime.runtime_values import (
    FFIHandleValue, FFIScalarValue, FFIStructValue, LazyList, ObjectValue, RuntimeNumber, TaggedValue,
)
from valiance.vtypes.symbols import Symbol


@stdlib_element("quote", (T.String,), (T.String,), param_names=("value",), exported=False)
def _quote(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Escape a complete JSON string token, including control codes and surrogates."""
    del ctx
    return (json.dumps(args[0], ensure_ascii=True),)


@stdlib_element(
    "number", (T.Number,), (T.Result(T.String, T.N(Symbol("ValueError"))),),
    param_names=("value",), exported=False,
)
def _number(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Format exact real digits without rounding through a host float."""
    del ctx
    value, = args
    if value.is_complex():
        return (ObjectValue("ValueError", {"message": "JSON numbers must be real"}),)
    return (str(value.real),)


class _JsonEncodingError(ValueError):
    """An unsupported value encountered while traversing JSON-compatible storage."""


def _collection_child_type(expected: T.Type | None) -> T.Type | None:
    """Carry type and depth-indexed tag evidence to each list element."""
    if expected is None:
        return None
    if isinstance(expected, T.TaggedType):
        child = _collection_child_type(expected.inner)
        if child is None:
            return None
        shifted = frozenset(
            replace(tag, depth=tag.depth - 1)
            for tag in expected.tags if tag.depth > 0
        )
        return T.TaggedType(child, shifted) if shifted else child
    if isinstance(expected, (T.NoVecType, T.ExactType)):
        return _collection_child_type(expected.inner)
    if isinstance(expected, T.CollectionType):
        return (expected.base if expected.rank == 1 else
                type(expected)(expected.base, expected.rank - 1))
    return None


def _optional_payload_type(expected: T.Type | None) -> T.Type | None:
    """Retain the payload type of a present value, including union optionals."""
    if isinstance(expected, (T.TaggedType, T.NoVecType, T.ExactType)):
        return _optional_payload_type(expected.inner)
    if isinstance(expected, T.UnionType):
        return next((_optional_payload_type(item) for item in expected.items
                     if isinstance(item, T.NominalType)
                     and item.name.text == "Some"), None)
    if (isinstance(expected, T.NominalType) and expected.name.text == "Some"
            and len(expected.args) == 1):
        return expected.args[0]
    return None


def _instantiate_json_type(typ: T.Type, arguments: dict[str, T.Type]) -> T.Type:
    """Instantiate declared field types using an object's reified generic arguments."""
    if isinstance(typ, T.NominalType):
        if not typ.args and typ.name.text in arguments:
            return arguments[typ.name.text]
        return replace(typ, args=tuple(_instantiate_json_type(arg, arguments)
                                       for arg in typ.args))
    if isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
        return replace(typ, inner=_instantiate_json_type(typ.inner, arguments))
    if isinstance(typ, T.CollectionType):
        return replace(typ, base=_instantiate_json_type(typ.base, arguments))
    if isinstance(typ, T.UnionType):
        return replace(typ, items=frozenset(
            _instantiate_json_type(item, arguments) for item in typ.items))
    return typ


def _render_json_value(
    value: Any, visiting: set[int], expected: T.Type | None, native_encode: Any = None,
) -> str:
    """Encode JSON-compatible data using trusted metadata for managed fields."""
    runtime_infinite = False
    while isinstance(value, TaggedValue):
        runtime_infinite |= any(
            tag.name == "infinite" and tag.depth == 0 and not tag.absent
            for tag in value.tags
        )
        if any(tag.name == "boolean" and tag.depth == 0 for tag in value.tags):
            return "true" if bool(value.value) else "false"
        value = value.value
    if isinstance(expected, (T.NoVecType, T.ExactType)):
        expected = expected.inner
    if isinstance(expected, T.TaggedType):
        if (any(tag.name == "boolean" and tag.depth == 0 and not tag.absent
                for tag in expected.tags) and isinstance(value, (int, RuntimeNumber))):
            return "true" if bool(value) else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, RuntimeNumber):
        if value.is_complex():
            raise _JsonEncodingError("JSON numbers must be real")
        return str(value.real)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise _JsonEncodingError("JSON numbers must be finite")
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if value is None:
        return "null"
    if isinstance(value, (FFIHandleValue, FFIScalarValue, FFIStructValue)):
        # A native value has no serializable layout. Only the embedding
        # application knows how to encode its identity without leaking pointers.
        if isinstance(value, FFIHandleValue) and (not value.alive or value.destroy_pending):
            raise _JsonEncodingError("Released native handles cannot be encoded")
        if native_encode is None:
            raise _JsonEncodingError(f"No JSON adapter for native type {value.ffi_type}")
        if id(value) in visiting:
            raise _JsonEncodingError("Cyclic JSON values cannot be encoded")
        visiting.add(id(value))
        try:
            try:
                representation = native_encode(value)
            except Exception as exc:
                raise _JsonEncodingError(f"Native JSON encoder failed: {exc}") from exc
            return _render_json_value(representation, visiting, None, native_encode)
        finally:
            visiting.remove(id(value))
    if isinstance(value, ObjectValue):
        if value.type_name == "None":
            return "null"
        if value.type_name == "Some":
            identity = id(value)
            if identity in visiting:
                raise _JsonEncodingError("Cyclic JSON values cannot be encoded")
            visiting.add(identity)
            try:
                return _render_json_value(value.fields["value"], visiting,
                                          _optional_payload_type(expected), native_encode)
            finally:
                visiting.remove(identity)
        runtime = value.runtime_type
        # Only explicitly published schemas authorize native object traversal;
        # in particular, a missing schema is not evidence of an empty object.
        if runtime is None or runtime.json_fields is None:
            raise _JsonEncodingError("Managed JSON values require checked field metadata")
        if runtime.dup_error is not None:
            raise _JsonEncodingError("Protected values cannot be JSON encoded")
        identity = id(value)
        if identity in visiting:
            raise _JsonEncodingError("Cyclic JSON values cannot be encoded")
        visiting.add(identity)
        try:
            if len(runtime.json_generics) != len(value.type_args):
                raise _JsonEncodingError("Incomplete managed JSON type information")
            arguments = dict(zip(runtime.json_generics,
                                 (_json_type(arg) for arg in value.type_args), strict=True))
            parts = []
            for name, visibility, field_type in runtime.json_fields:
                if visibility == "private":
                    continue
                if name not in value.fields:
                    raise _JsonEncodingError("Incomplete managed JSON storage")
                expected_field = (_instantiate_json_type(_json_type(field_type), arguments)
                                  if field_type is not None else None)
                parts.append(json.dumps(name, ensure_ascii=True) + ":" +
                             _render_json_value(value.fields[name], visiting, expected_field, native_encode))
            return "{" + ",".join(parts) + "}"
        finally:
            visiting.remove(identity)
    if isinstance(value, LazyList):
        # A lazy collection need not have a known length; iteration establishes
        # its contents as it goes. A declared infinite collection, however,
        # can never produce a complete JSON array.
        expected_infinite = (
            isinstance(expected, T.TaggedType)
            and any(tag.name == "infinite" and tag.depth == 0 and not tag.absent
                    for tag in expected.tags)
        )
        if runtime_infinite or expected_infinite:
            raise _JsonEncodingError("Infinite JSON lists cannot be encoded")
    if isinstance(value, (LazyList, list, tuple, dict)):
        identity = id(value)
        if identity in visiting:
            raise _JsonEncodingError("Cyclic JSON values cannot be encoded")
        visiting.add(identity)
        try:
            if isinstance(value, (LazyList, list, tuple)):
                child_type = _collection_child_type(expected)
                return "[" + ",".join(
                    _render_json_value(item, visiting, child_type, native_encode) for item in value
                ) + "]"
            if any(not isinstance(key, str) for key in value):
                raise _JsonEncodingError("JSON dictionary keys must be strings")
            dict_value_type = (expected.args[1] if isinstance(expected, T.NominalType)
                               and expected.name.text == "Dict" and len(expected.args) == 2
                               else None)
            return "{" + ",".join(
                json.dumps(key, ensure_ascii=True) + ":" +
                _render_json_value(item, visiting, dict_value_type, native_encode)
                for key, item in value.items()
            ) + "}"
        finally:
            visiting.remove(identity)
    raise _JsonEncodingError("Unsupported JSON value type")


@lru_cache(maxsize=512)
def _json_type(type_name: str) -> T.Type:
    """Parse a checked type name once for repeated nested-value encoding."""
    from valiance.parsing.parser import parse_type

    return parse_type(type_name)


@stdlib_element(
    "encodeDynamic", (T.V("T"), T.String),
    (T.Result(T.String, T.N(Symbol("ValueError"))),),
    param_names=("value", "typeName"), exported=False,
)
def _encode_dynamic(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Encode runtime data using checked constructor visibility metadata."""
    try:
        expected = _json_type(args[1])
        return (_render_json_value(args[0], set(), expected, ctx.json_native_encoder),)
    except _JsonEncodingError as exc:
        return (ObjectValue("ValueError", {"message": str(exc)}),)


class _JsonDecodingError(ValueError):
    """Malformed JSON or a value incompatible with the requested Valiance type."""


def _unique_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject ambiguous duplicate keys rather than silently overriding them."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _JsonDecodingError(f"Duplicate JSON key {key!r}")
        result[key] = value
    return result


def _invalid_json_constant(value: str) -> None:
    """Reject non-JSON numeric constants such as NaN and Infinity."""
    raise _JsonDecodingError(f"Invalid JSON numeric constant {value!r}")


def _json_decode_typed(
    value: Any, expected: T.Type, lookup: Any, path: str = "$",
    depth: int = 0, variants: Any = None, native_decode: Any = None,
) -> Any:
    """Construct checked JSON values recursively, using registered constructor layouts."""
    if depth > 512:
        raise _JsonDecodingError(f"{path}: JSON nesting exceeds supported depth")
    if isinstance(expected, (T.NoVecType, T.ExactType)):
        return _json_decode_typed(value, expected.inner, lookup, path, depth, variants, native_decode)
    if isinstance(expected, T.TaggedType):
        if any(tag.name == "boolean" and tag.depth == 0 and not tag.absent
               for tag in expected.tags):
            if not isinstance(value, bool):
                raise _JsonDecodingError(f"{path}: expected JSON Boolean")
            return TaggedValue(RuntimeNumber(int(value)), frozenset(
                tag for tag in expected.tags if tag.depth == 0 and not tag.absent
            ))
        # Collection-depth tags belong to the elements; the caller recurses
        # using the same tag-shifting convention as encode.
        if isinstance(expected.inner, T.CollectionType):
            return _json_decode_collection(value, expected, lookup, path, depth, variants, native_decode)
        if expected.tags:
            raise _JsonDecodingError(f"{path}: cannot decode tagged type {expected}")
        return _json_decode_typed(value, expected.inner, lookup, path, depth, variants, native_decode)
    if isinstance(expected, T.CollectionType):
        return _json_decode_collection(value, expected, lookup, path, depth, variants, native_decode)
    if isinstance(expected, T.UnionType):
        payload = _optional_payload_type(expected)
        if payload is not None:
            if value is None:
                return ObjectValue("None", {})
            return ObjectValue("Some", {"value": _json_decode_typed(value, payload, lookup, path, depth + 1, variants, native_decode)},
                               (str(payload),))
        raise _JsonDecodingError(f"{path}: ambiguous JSON union type {expected}")
    if isinstance(expected, T.RowType):
        if not (isinstance(expected.base, T.NominalType)
                and expected.base.name.text == "record"):
            raise _JsonDecodingError(f"{path}: cannot construct an abstract row type")
        if not isinstance(value, dict):
            raise _JsonDecodingError(f"{path}: expected JSON object")
        declared = {str(field.name) for field in expected.fields}
        if set(value) != declared:
            raise _JsonDecodingError(f"{path}: record fields mismatch (missing {sorted(declared - value.keys())}, unexpected {sorted(value.keys() - declared)})")
        fields = [(str(field.name), _json_decode_typed(value[str(field.name)], field.typ, lookup,
                          f"{path}.{field.name}", depth + 1, variants, native_decode)) for field in expected.fields]
        from valiance.runtime.runtime_values import RecordValue
        return RecordValue(fields, type_signature=str(expected))
    if isinstance(expected, T.NoneTypeNode):
        if value is None:
            return ObjectValue("None", {})
        raise _JsonDecodingError(f"{path}: expected JSON null")
    if isinstance(expected, T.FFINamedType):
        # Never manufacture a pointer from JSON bytes. The registered factory
        # must return a live native handle of precisely the requested type.
        if native_decode is None:
            raise _JsonDecodingError(f"{path}: no native JSON factory for {expected}")
        try:
            result = native_decode(str(expected), value)
        except Exception as exc:
            raise _JsonDecodingError(f"{path}: native JSON factory failed: {exc}") from exc
        if (not isinstance(result, (FFIHandleValue, FFIScalarValue, FFIStructValue)) or
                result.ffi_type != str(expected) or
                (isinstance(result, FFIHandleValue) and
                 (not result.alive or result.destroy_pending))):
            raise _JsonDecodingError(f"{path}: native factory returned an invalid {expected} handle")
        return result
    if not isinstance(expected, T.NominalType):
        raise _JsonDecodingError(f"{path}: unsupported JSON target type {expected}")
    name = expected.name.text
    if name == "String":
        if not isinstance(value, str):
            raise _JsonDecodingError(f"{path}: expected JSON string")
        return value
    if name in {"Int", "Real", "Number"}:
        from decimal import Decimal
        if not isinstance(value, Decimal):
            raise _JsonDecodingError(f"{path}: expected JSON number")
        if name == "Int" and value != value.to_integral_value():
            raise _JsonDecodingError(f"{path}: expected integral JSON number")
        return RuntimeNumber(value)
    if name == "None":
        if value is None:
            return ObjectValue("None", {})
        raise _JsonDecodingError(f"{path}: expected JSON null")
    if name == "Some" and len(expected.args) == 1:
        if value is None:
            raise _JsonDecodingError(f"{path}: expected present optional value")
        return ObjectValue("Some", {"value": _json_decode_typed(value, expected.args[0], lookup, path, depth + 1, variants, native_decode)},
                           (str(expected.args[0]),))
    if name == "Dict" and len(expected.args) == 2:
        if not (isinstance(expected.args[0], T.NominalType)
                and expected.args[0].name.text == "String"):
            raise _JsonDecodingError(f"{path}: JSON dictionary keys must be String")
        if not isinstance(value, dict):
            raise _JsonDecodingError(f"{path}: expected JSON object")
        from valiance.runtime.runtime_values import DictValue
        return DictValue((key, _json_decode_typed(item, expected.args[1], lookup,
                f"{path}.{key}", depth + 1, variants, native_decode)) for key, item in value.items())
    if not isinstance(value, dict):
        raise _JsonDecodingError(f"{path}: expected JSON object for {expected}")
    constructor = None if lookup is None else lookup(name)
    if constructor is None:
        candidates = () if variants is None else variants(name)
        if candidates:
            successes = []
            for candidate in candidates:
                candidate_type = T.NominalType(
                    type(expected.name)(candidate.type_name), expected.args
                )
                try:
                    successes.append(_json_decode_typed(
                        value, candidate_type, lookup, path, depth + 1, variants, native_decode
                    ))
                except _JsonDecodingError:
                    continue
            if len(successes) == 1:
                return successes[0]
            if len(successes) > 1:
                raise _JsonDecodingError(f"{path}: ambiguous JSON variant for {expected}")
            raise _JsonDecodingError(f"{path}: JSON does not match a case of {expected}")
        raise _JsonDecodingError(f"{path}: no checked constructor available for {expected}")
    runtime = constructor.runtime_type
    if runtime is None or runtime.json_fields is None:
        raise _JsonDecodingError(f"{path}: missing checked field layout for {expected}")
    if runtime.dup_error is not None:
        raise _JsonDecodingError(f"{path}: protected objects cannot be decoded")
    if constructor.initializer is not None:
        raise _JsonDecodingError(f"{path}: custom-initialized objects cannot be decoded")
    if len(runtime.json_generics) != len(expected.args):
        raise _JsonDecodingError(f"{path}: generic arguments do not match {name}")
    arguments = dict(zip(runtime.json_generics, expected.args, strict=True))
    public_names = {key for key, visibility, _ in runtime.json_fields
                    if visibility != "private"}
    extras = set(value) - public_names
    if extras:
        raise _JsonDecodingError(f"{path}: unexpected JSON fields {sorted(extras)}")
    fields = dict(constructor.defaults)
    for key, visibility, declaration in runtime.json_fields:
        if visibility == "private":
            if key not in fields:
                raise _JsonDecodingError(f"{path}: private field {key!r} has no default")
            continue
        if key not in value:
            if key not in fields:
                raise _JsonDecodingError(f"{path}: missing required field {key!r}")
            continue
        if declaration is None:
            raise _JsonDecodingError(f"{path}: incomplete type information for {key!r}")
        field_type = _instantiate_json_type(_json_type(declaration), arguments)
        fields[key] = _json_decode_typed(value[key], field_type, lookup, f"{path}.{key}", depth + 1, variants, native_decode)
    if set(fields) != set(constructor.fields):
        raise _JsonDecodingError(f"{path}: incomplete object storage")
    return ObjectValue(name, fields, tuple(str(arg) for arg in expected.args), runtime_type=runtime)


def _json_decode_collection(
    value: Any, expected: T.Type, lookup: Any, path: str, depth: int,
    variants: Any, native_decode: Any = None,
) -> Any:
    """Reconstruct nested JSON arrays using the expected typed collection rank."""
    if not isinstance(value, list):
        raise _JsonDecodingError(f"{path}: expected JSON array")
    child_type = _collection_child_type(expected)
    if child_type is None:
        raise _JsonDecodingError(f"{path}: invalid collection target type {expected}")
    from valiance.runtime.runtime_values import ListValue
    collection = expected.inner if isinstance(expected, T.TaggedType) else expected
    rank = collection.rank if isinstance(collection, T.CollectionType) and isinstance(collection.rank, int) else None
    return ListValue(
        (_json_decode_typed(item, child_type, lookup, f"{path}[{index}]", depth + 1, variants, native_decode)
         for index, item in enumerate(value)),
        runtime_rank=rank,
    )


@stdlib_element(
    "decodeDynamic", (T.String, T.String),
    (T.Result(T.V("T"), T.N(Symbol("ValueError"))),),
    param_names=("text", "typeName"), exported=False,
)
def _decode_dynamic(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Parse strict JSON into the requested statically checked target schema."""
    from decimal import Decimal
    try:
        decoded = json.loads(args[0], parse_int=Decimal, parse_float=Decimal,
                             parse_constant=_invalid_json_constant,
                             object_pairs_hook=_unique_json_pairs)
        return (_json_decode_typed(decoded, _json_type(args[1]), ctx.json_constructor,
                                  variants=ctx.json_variant_constructors,
                                   native_decode=ctx.json_native_decoder),)
    except (ValueError, RecursionError) as exc:
        return (ObjectValue("ValueError", {"message": str(exc)}),)
