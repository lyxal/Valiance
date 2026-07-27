"""Versioned Valiance bytecode-module and analysed-interface container support."""

from __future__ import annotations

import hashlib
import io
import json
import pickle
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from valiance.runtime.bytecode import Program
from valiance.runtime.serialization import BytecodeFormatError, dumps, loads

MAGIC_V1 = b"VLNCBM\x01"
MAGIC = b"VLNCBM\x02"
FORMAT_VERSION = 2
COMPILER_ABI = 2
INTERFACE_ABI = 1
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
    compiler_abi: int = COMPILER_ABI
    interface_abi: int = INTERFACE_ABI


def _interface_bytes(interface: Any | None) -> bytes:
    """Encode an internal analysed interface deterministically for this ABI."""
    if interface is None:
        return b""
    return pickle.dumps(interface, protocol=5)


def build_module(
    module_name: str,
    source: str,
    program: Program,
    *,
    analysed_interface: Any | None = None,
    dependency_hashes: tuple[tuple[str, str], ...] = (),
) -> CompiledModule:
    """Create a compiled module from analysed source, interface, and bytecode."""
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    interface = _interface_bytes(analysed_interface)
    interface_hash = hashlib.sha256(interface).hexdigest() if interface else ""
    return CompiledModule(
        module_name,
        source,
        program,
        source_hash,
        analysed_interface,
        interface_hash,
        tuple(sorted(dependency_hashes)),
    )


def dumps_module(module: CompiledModule) -> bytes:
    """Serialize a compiled module with independent interface and runtime sections."""
    interface = _interface_bytes(module.analysed_interface)
    actual_interface_hash = hashlib.sha256(interface).hexdigest() if interface else ""
    if module.interface_hash and module.interface_hash != actual_interface_hash:
        raise BytecodeFormatError("Valiance module analysed-interface hash mismatch")
    metadata = json.dumps(
        {
            "format": FORMAT_VERSION,
            "module": module.module_name,
            "source_hash": module.source_hash,
            "interface_hash": actual_interface_hash,
            "compiler_abi": module.compiler_abi,
            "interface_abi": module.interface_abi,
            "interface_source": module.interface_source,
            "dependencies": list(module.dependency_hashes),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    bytecode = dumps(module.program)
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
    interface_hash = metadata.get("interface_hash")
    if not all(isinstance(value, str) for value in (module_name, source, source_hash, interface_hash)):
        raise BytecodeFormatError("invalid Valiance bytecode module metadata")
    if hashlib.sha256(source.encode()).hexdigest() != source_hash:
        raise BytecodeFormatError("Valiance bytecode module source hash mismatch")
    interface_data = data[end_metadata:end_interface]
    if hashlib.sha256(interface_data).hexdigest() != interface_hash:
        raise BytecodeFormatError("Valiance bytecode module interface hash mismatch")
    try:
        analysed_interface = pickle.Unpickler(io.BytesIO(interface_data)).load()
    except Exception as exc:
        raise BytecodeFormatError("invalid analysed module interface") from exc
    dependencies = metadata.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(
        isinstance(item, list) and len(item) == 2 and all(isinstance(v, str) for v in item)
        for item in dependencies
    ):
        raise BytecodeFormatError("invalid module dependency metadata")
    return CompiledModule(
        module_name,
        source,
        loads(data[end_interface:end_bytecode]),
        source_hash,
        analysed_interface,
        interface_hash,
        tuple((name, digest) for name, digest in dependencies),
        COMPILER_ABI,
        INTERFACE_ABI,
    )


def load_module_file(path: Path) -> CompiledModule:
    """Read one compiled module from disk."""
    return loads_module(path.read_bytes())
