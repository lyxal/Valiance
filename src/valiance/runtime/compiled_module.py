"""Versioned Valiance bytecode-module container support."""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path

from valiance.runtime.bytecode import Program
from valiance.runtime.serialization import BytecodeFormatError, dumps, loads

MAGIC = b"VLNCBM\x01"
FORMAT_VERSION = 1


@dataclass(frozen=True)
class CompiledModule:
    """A reusable module containing static input and compiled runtime bytecode."""

    module_name: str
    interface_source: str
    program: Program
    source_hash: str
    compiler_abi: int = 1


def build_module(module_name: str, source: str, program: Program) -> CompiledModule:
    """Create a compiled module from analysed source and its bytecode payload."""
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return CompiledModule(module_name, source, program, digest)


def dumps_module(module: CompiledModule) -> bytes:
    """Serialize a compiled module using a distinct, versioned container."""
    metadata = json.dumps(
        {
            "format": FORMAT_VERSION,
            "module": module.module_name,
            "source_hash": module.source_hash,
            "compiler_abi": module.compiler_abi,
            "interface_source": module.interface_source,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    bytecode = dumps(module.program)
    return MAGIC + struct.pack(">II", len(metadata), len(bytecode)) + metadata + bytecode


def loads_module(data: bytes) -> CompiledModule:
    """Decode and validate a Valiance bytecode module."""
    if not data.startswith(MAGIC):
        raise BytecodeFormatError("not a Valiance bytecode module")
    header = len(MAGIC)
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
    if metadata.get("format") != FORMAT_VERSION:
        raise BytecodeFormatError(
            f"unsupported Valiance bytecode module format {metadata.get('format')!r}"
        )
    module_name = metadata.get("module")
    source = metadata.get("interface_source")
    source_hash = metadata.get("source_hash")
    compiler_abi = metadata.get("compiler_abi")
    if not isinstance(module_name, str) or not isinstance(source, str):
        raise BytecodeFormatError("invalid Valiance bytecode module interface metadata")
    if not isinstance(source_hash, str) or not isinstance(compiler_abi, int):
        raise BytecodeFormatError("invalid Valiance bytecode module compatibility metadata")
    actual_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    if actual_hash != source_hash:
        raise BytecodeFormatError("Valiance bytecode module interface hash mismatch")
    return CompiledModule(
        module_name,
        source,
        loads(data[end_metadata:end_bytecode]),
        source_hash,
        compiler_abi,
    )


def load_module_file(path: Path) -> CompiledModule:
    """Read one compiled module from disk."""
    return loads_module(path.read_bytes())
