"""Stable source identities for compiler-linkable user function overloads."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha1
from pathlib import Path

from valiance.asts.nodes import SourceLocation
from valiance.vtypes.nodes import Overload
from valiance.vtypes.identities import overload_implementation_id


def declaration_prefix(source_file: Path | None, location: SourceLocation) -> str:
    """Name one source declaration independently of its inferred alternatives."""
    source = str(source_file.resolve()) if source_file is not None else "<inline>"
    fingerprint = sha1(source.encode("utf-8")).hexdigest()[:16]
    return f"{fingerprint}:{location.offset}:"


def declaration_id(
    source_file: Path | None, location: SourceLocation | None, variant: Overload,
) -> str:
    """Identify a source variant by callable shape, never by overload-table order."""
    signature = overload_implementation_id(replace(variant, implementation_id=None))
    prefix = declaration_prefix(source_file, location) if location is not None else "synthetic:"
    return prefix + signature.removeprefix("callable:")
