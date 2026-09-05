"""Versioned Valiance bytecode-module and analysed-interface container support."""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from valiance.runtime.bytecode import Program
from valiance.runtime.interface_serialization import dumps_interface, loads_interface
from valiance.runtime.serialization import BytecodeFormatError, dumps, loads

MAGIC_V1 = b"VLNCBM\x01"
MAGIC = b"VLNCBM\x04"
FORMAT_VERSION = 4
COMPILER_ABI = 2
INTERFACE_ABI = 2
MAX_INTERFACE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class CompiledModule:
    """A module containing its analysed interface and compiled runtime program."""

    module_name: str
    interface_source: str
    program: Program
    source_hash: str
    analysed_interface: Any | None = None
    interface_hash: str = ""
    dependency_hashes: tuple[tuple[str, str], ...] = ()
    implementation_hash: str = ""
    implementation_options: str = "optimize=true"
    compiler_abi: int = COMPILER_ABI
    interface_abi: int = INTERFACE_ABI


def interface_bytes(interface: Any | None) -> bytes:
    """Encode an internal analysed interface deterministically for this ABI."""
    if interface is None:
        return b""
    return dumps_interface(interface)


def _bodyless_definition(definition: Any) -> Any:
    """Return a declaration header without its implementation body."""
    function = getattr(definition, "function", None)
    if function is None:
        return definition
    return replace(definition, function=replace(function, body=()))


def _semantic_interface_value(interface: Any | None) -> Any | None:
    """Project ModuleExports onto facts that affect importing semantic analysis."""
    if interface is None:
        return None
    from valiance.modules_system.modules import ModuleExports

    if not isinstance(interface, ModuleExports):
        return interface
    definitions = tuple(
        (
            item.name,
            item.public,
            item.attached_tag,
            tuple((overload.typ, overload.overload) for overload in item.typed.overloads),
        )
        for item in interface.definitions
    )
    objects = []
    for item in interface.objects:
        node = item.typed.node
        bodyless_node = replace(
            node,
            definitions=tuple(_bodyless_definition(value) for value in node.definitions),
        )
        objects.append(
            (
                item.name,
                item.public,
                item.import_friendly,
                bodyless_node,
                item.typed.typ,
                tuple(_bodyless_definition(value) for value in item.friendly_definitions),
                tuple(
                    _bodyless_definition(value)
                    for value in item.private_friendly_definitions
                ),
            )
        )
    implementations = tuple(
        (
            item.object_name,
            item.trait_name,
            tuple(_bodyless_definition(value) for value in item.definitions),
            item.owned,
            item.object_pattern,
            item.trait_pattern,
            item.generics,
            item.generic_constraints,
            item.subject_kind,
        )
        for item in interface.trait_implementations
    )
    return (
        interface.module_name,
        definitions,
        tuple(objects),
        interface.tags,
        interface.overlays,
        implementations,
    )


def interface_hash(interface: Any | None) -> str:
    """Return the semantic fingerprint of facts visible to module importers."""
    semantic = _semantic_interface_value(interface)
    encoded = dumps_interface(semantic, include_locations=False)
    return hashlib.sha256(encoded).hexdigest() if encoded else ""


def implementation_hash(program: Program, *, options: str = "optimize=true") -> str:
    """Return a canonical runtime-implementation fingerprint for compiled code."""
    context = json.dumps(
        {"compiler_abi": COMPILER_ABI, "options": options},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    bytecode = dumps(program)
    return hashlib.sha256(
        struct.pack(">I", len(context)) + context + bytecode
    ).hexdigest()


def build_module(
    module_name: str,
    source: str,
    program: Program,
    *,
    analysed_interface: Any | None = None,
    dependency_hashes: tuple[tuple[str, str], ...] = (),
    implementation_options: str = "optimize=true",
) -> CompiledModule:
    """Create a compiled module from analysed source, interface, and bytecode."""
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    semantic_hash = interface_hash(analysed_interface)
    return CompiledModule(
        module_name,
        source,
        program,
        source_hash,
        analysed_interface,
        semantic_hash,
        tuple(sorted(dependency_hashes)),
        implementation_hash(program, options=implementation_options),
        implementation_options,
    )


def dumps_module(module: CompiledModule) -> bytes:
    """Serialize a compiled module with independent interface and runtime sections."""
    interface = interface_bytes(module.analysed_interface)
    actual_interface_hash = interface_hash(module.analysed_interface)
    interface_content_hash = hashlib.sha256(interface).hexdigest() if interface else ""
    if module.interface_hash and module.interface_hash != actual_interface_hash:
        raise BytecodeFormatError("Valiance module analysed-interface hash mismatch")
    bytecode = dumps(module.program)
    actual_implementation_hash = implementation_hash(
        module.program, options=module.implementation_options
    )
    if module.implementation_hash and module.implementation_hash != actual_implementation_hash:
        raise BytecodeFormatError("Valiance module implementation hash mismatch")
    metadata = json.dumps(
        {
            "format": FORMAT_VERSION,
            "module": module.module_name,
            "source_hash": module.source_hash,
            "interface_hash": actual_interface_hash,
            "interface_content_hash": interface_content_hash,
            "compiler_abi": module.compiler_abi,
            "interface_abi": module.interface_abi,
            "interface_source": module.interface_source,
            "dependencies": list(module.dependency_hashes),
            "implementation_hash": actual_implementation_hash,
            "implementation_options": module.implementation_options,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return (
        MAGIC
        + struct.pack(">III", len(metadata), len(interface), len(bytecode))
        + metadata
        + interface
        + bytecode
    )


def _load_v1(data: bytes) -> CompiledModule:
    """Load the source-interface-only format for backwards compatibility."""
    header = len(MAGIC_V1)
    if len(data) < header + 8:
        raise BytecodeFormatError("truncated Valiance bytecode module")
    metadata_size, bytecode_size = struct.unpack(">II", data[header : header + 8])
    start = header + 8
    end_metadata = start + metadata_size
    end_bytecode = end_metadata + bytecode_size
    if end_bytecode != len(data):
        raise BytecodeFormatError("invalid Valiance bytecode module section lengths")
    try:
        metadata = json.loads(data[start:end_metadata].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BytecodeFormatError("invalid Valiance bytecode module metadata") from exc
    source = metadata.get("interface_source")
    module_name = metadata.get("module")
    source_hash = metadata.get("source_hash")
    if not all(isinstance(value, str) for value in (source, module_name, source_hash)):
        raise BytecodeFormatError("invalid Valiance bytecode module metadata")
    if hashlib.sha256(source.encode()).hexdigest() != source_hash:
        raise BytecodeFormatError("Valiance bytecode module interface hash mismatch")
    return CompiledModule(module_name, source, loads(data[end_metadata:end_bytecode]), source_hash)


def loads_module(data: bytes) -> CompiledModule:
    """Decode and validate a Valiance bytecode module."""
    if data.startswith(MAGIC_V1):
        return _load_v1(data)
    if not data.startswith(MAGIC):
        raise BytecodeFormatError("not a Valiance bytecode module")
    header = len(MAGIC)
    if len(data) < header + 12:
        raise BytecodeFormatError("truncated Valiance bytecode module")
    metadata_size, interface_size, bytecode_size = struct.unpack(
        ">III", data[header : header + 12]
    )
    if interface_size > MAX_INTERFACE_BYTES:
        raise BytecodeFormatError("Valiance module interface exceeds size limit")
    start = header + 12
    end_metadata = start + metadata_size
    end_interface = end_metadata + interface_size
    end_bytecode = end_interface + bytecode_size
    if end_bytecode != len(data):
        raise BytecodeFormatError("invalid Valiance bytecode module section lengths")
    try:
        metadata = json.loads(data[start:end_metadata].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BytecodeFormatError("invalid Valiance bytecode module metadata") from exc
    if metadata.get("format") != FORMAT_VERSION:
        raise BytecodeFormatError("unsupported Valiance bytecode module format")
    if metadata.get("compiler_abi") != COMPILER_ABI:
        raise BytecodeFormatError("incompatible Valiance compiler ABI")
    if metadata.get("interface_abi") != INTERFACE_ABI:
        raise BytecodeFormatError("incompatible Valiance interface ABI")
    module_name = metadata.get("module")
    source = metadata.get("interface_source")
    source_hash = metadata.get("source_hash")
    stored_interface_hash = metadata.get("interface_hash")
    interface_content_hash = metadata.get("interface_content_hash")
    stored_implementation_hash = metadata.get("implementation_hash")
    implementation_options = metadata.get("implementation_options")
    metadata_strings = (
        module_name,
        source,
        source_hash,
        stored_interface_hash,
        interface_content_hash,
        stored_implementation_hash,
        implementation_options,
    )
    if not all(isinstance(value, str) for value in metadata_strings):
        raise BytecodeFormatError("invalid Valiance bytecode module metadata")
    if hashlib.sha256(source.encode()).hexdigest() != source_hash:
        raise BytecodeFormatError("Valiance bytecode module source hash mismatch")
    interface_data = data[end_metadata:end_interface]
    if hashlib.sha256(interface_data).hexdigest() != interface_content_hash:
        raise BytecodeFormatError("Valiance bytecode module interface content hash mismatch")
    analysed_interface = loads_interface(interface_data)
    if interface_hash(analysed_interface) != stored_interface_hash:
        raise BytecodeFormatError("Valiance bytecode module semantic interface hash mismatch")
    dependencies = metadata.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(
        isinstance(item, list) and len(item) == 2 and all(isinstance(v, str) for v in item)
        for item in dependencies
    ):
        raise BytecodeFormatError("invalid module dependency metadata")
    program = loads(data[end_interface:end_bytecode])
    actual_implementation_hash = implementation_hash(program, options=implementation_options)
    if actual_implementation_hash != stored_implementation_hash:
        raise BytecodeFormatError("Valiance module implementation hash mismatch")
    return CompiledModule(
        module_name,
        source,
        program,
        source_hash,
        analysed_interface,
        stored_interface_hash,
        tuple((name, digest) for name, digest in dependencies),
        stored_implementation_hash,
        implementation_options,
        COMPILER_ABI,
        INTERFACE_ABI,
    )


def load_module_file(path: Path) -> CompiledModule:
    """Read one compiled module from disk."""
    return loads_module(path.read_bytes())
