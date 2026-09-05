"""Explicit canonical schema for persisted analysed module interfaces."""

from __future__ import annotations

import dataclasses
import importlib
import json
from decimal import Decimal
from enum import Enum
from typing import Any

from valiance.runtime.serialization import BytecodeFormatError

MAGIC = b"VLNI\x02"
SCHEMA_VERSION = 2
MAX_DEPTH = 256
MAX_ITEMS = 1_000_000

# Persisted semantic records are restricted to these compiler model modules.
_ALLOWED_MODULES = (
    "valiance.asts.nodes",
    "valiance.modules_system.modules",
    "valiance.vtypes.context",
    "valiance.vtypes.environment",
    "valiance.vtypes.nodes",
    "valiance.vtypes.symbols",
)


def _registry() -> dict[str, type[Any]]:
    """Build the closed record-tag registry from approved compiler model modules."""
    result: dict[str, type[Any]] = {}
    for module_name in _ALLOWED_MODULES:
        module = importlib.import_module(module_name)
        for name, value in vars(module).items():
            if not isinstance(value, type):
                continue
            if value.__module__ != module_name:
                continue
            if not dataclasses.is_dataclass(value) and not issubclass(value, Enum):
                continue
            tag = f"{module_name}:{name}"
            result[tag] = value
    return result


def _encode(value: Any, *, depth: int = 0, include_locations: bool = True) -> Any:
    """Convert one interface value into canonical tagged JSON-compatible data."""
    if depth > MAX_DEPTH:
        raise BytecodeFormatError("analysed interface exceeds nesting limit")
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return {"$": "decimal", "value": str(value)}
    if isinstance(value, tuple):
        return {"$": "tuple", "items": [_encode(item, depth=depth + 1, include_locations=include_locations) for item in value]}
    if isinstance(value, frozenset):
        items = [_encode(item, depth=depth + 1, include_locations=include_locations) for item in value]
        items.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
        return {"$": "frozenset", "items": items}
    if isinstance(value, list):
        return {"$": "list", "items": [_encode(item, depth=depth + 1, include_locations=include_locations) for item in value]}
    if isinstance(value, dict):
        items = [
            (_encode(key, depth=depth + 1, include_locations=include_locations), _encode(item, depth=depth + 1, include_locations=include_locations))
            for key, item in value.items()
        ]
        items.sort(key=lambda pair: json.dumps(pair[0], sort_keys=True, separators=(",", ":")))
        return {"$": "dict", "items": items}
    if isinstance(value, Enum):
        tag = f"{type(value).__module__}:{type(value).__name__}"
        if tag not in _registry():
            raise BytecodeFormatError(f"unsupported interface enum {tag}")
        return {"$": "enum", "type": tag, "name": value.name}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        tag = f"{type(value).__module__}:{type(value).__name__}"
        if tag not in _registry():
            raise BytecodeFormatError(f"unsupported interface record {tag}")
        return {
            "$": "record",
            "type": tag,
            "fields": [
                [
                    field.name,
                    None
                    if field.name == "location" and not include_locations
                    else _encode(
                        getattr(value, field.name),
                        depth=depth + 1,
                        include_locations=include_locations,
                    ),
                ]
                for field in dataclasses.fields(value)
            ],
        }
    raise BytecodeFormatError(
        f"unsupported analysed interface value {type(value).__module__}.{type(value).__name__}"
    )


def _decode(value: Any, *, depth: int = 0) -> Any:
    """Validate and reconstruct one value from the closed interface schema."""
    if depth > MAX_DEPTH:
        raise BytecodeFormatError("analysed interface exceeds nesting limit")
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if not isinstance(value, dict) or not isinstance(value.get("$"), str):
        raise BytecodeFormatError("invalid analysed interface value")
    kind = value["$"]
    if kind == "decimal":
        if set(value) != {"$", "value"} or not isinstance(value["value"], str):
            raise BytecodeFormatError("invalid interface decimal")
        try:
            return Decimal(value["value"])
        except Exception as exc:
            raise BytecodeFormatError("invalid interface decimal") from exc
    if kind in {"tuple", "frozenset", "list"}:
        if set(value) != {"$", "items"} or not isinstance(value["items"], list):
            raise BytecodeFormatError(f"invalid interface {kind}")
        if len(value["items"]) > MAX_ITEMS:
            raise BytecodeFormatError("analysed interface collection exceeds item limit")
        items = [_decode(item, depth=depth + 1) for item in value["items"]]
        return tuple(items) if kind == "tuple" else frozenset(items) if kind == "frozenset" else items
    if kind == "dict":
        if set(value) != {"$", "items"} or not isinstance(value["items"], list):
            raise BytecodeFormatError("invalid interface dictionary")
        result = {}
        for pair in value["items"]:
            if not isinstance(pair, list) or len(pair) != 2:
                raise BytecodeFormatError("invalid interface dictionary entry")
            key = _decode(pair[0], depth=depth + 1)
            if key in result:
                raise BytecodeFormatError("duplicate interface dictionary key")
            result[key] = _decode(pair[1], depth=depth + 1)
        return result
    registry = _registry()
    if kind == "enum":
        if set(value) != {"$", "type", "name"}:
            raise BytecodeFormatError("invalid interface enum")
        cls = registry.get(value.get("type"))
        if cls is None or not issubclass(cls, Enum) or not isinstance(value.get("name"), str):
            raise BytecodeFormatError("unknown interface enum tag")
        try:
            return cls[value["name"]]
        except KeyError as exc:
            raise BytecodeFormatError("unknown interface enum value") from exc
    if kind == "record":
        if set(value) != {"$", "type", "fields"} or not isinstance(value["fields"], list):
            raise BytecodeFormatError("invalid interface record")
        cls = registry.get(value.get("type"))
        if cls is None or not dataclasses.is_dataclass(cls):
            raise BytecodeFormatError("unknown interface record tag")
        expected = [field.name for field in dataclasses.fields(cls)]
        names = [pair[0] for pair in value["fields"] if isinstance(pair, list) and len(pair) == 2]
        if names != expected or len(names) != len(value["fields"]):
            raise BytecodeFormatError("invalid interface record fields")
        fields = {
            name: _decode(encoded, depth=depth + 1)
            for name, encoded in value["fields"]
        }
        try:
            return cls(**fields)
        except Exception as exc:
            raise BytecodeFormatError("invalid interface record payload") from exc
    raise BytecodeFormatError(f"unknown analysed interface tag {kind!r}")


def dumps_interface(interface: Any | None, *, include_locations: bool = True) -> bytes:
    """Encode an interface using a deterministic versioned non-executable schema."""
    if interface is None:
        return b""
    payload = json.dumps(
        {
            "schema": SCHEMA_VERSION,
            "value": _encode(interface, include_locations=include_locations),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return MAGIC + payload


def loads_interface(data: bytes) -> Any | None:
    """Decode one complete interface and reject malformed or incompatible bytes."""
    if not data:
        return None
    if not data.startswith(MAGIC):
        raise BytecodeFormatError("unsupported analysed interface schema")
    try:
        document = json.loads(data[len(MAGIC) :].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BytecodeFormatError("invalid analysed interface encoding") from exc
    if not isinstance(document, dict) or set(document) != {"schema", "value"}:
        raise BytecodeFormatError("invalid analysed interface document")
    if document["schema"] != SCHEMA_VERSION:
        raise BytecodeFormatError("incompatible analysed interface ABI")
    return _decode(document["value"])
