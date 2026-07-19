"""Imported declaration runtime naming and recursive self-call retargeting."""

from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from hashlib import sha1
from pathlib import Path
from typing import Any

from valiance.asts import ASTNode, DefineNode, ElementNode, ObjectNode
from valiance.asts.nodes import (
    TypedElementNode,
    TypedFunctionNode,
    TypedImportedFunctionNode,
    TypedImportedObjectNode,
    TypedNode,
)
from valiance.vtypes.symbols import Symbol


def prelude_seed(source_file: Path | None) -> str:
    """Return a stable internal namespace seed for imported declarations."""
    identity = "<inline>" if source_file is None else str(source_file.resolve())
    return sha1(identity.encode("utf-8")).hexdigest()[:12]


def with_import_runtime_name(
    node: TypedNode,
    runtime_name: Symbol,
) -> TypedNode:
    """Attach one hidden runtime binding and retarget recursive self-calls."""
    if isinstance(node, TypedFunctionNode):
        source_name = (
            node.node.name if isinstance(node.node, DefineNode) else Symbol("")
        )
        overloads = _rewrite_imported_self_calls(
            node.overloads,
            source_name,
            runtime_name,
        )
        return TypedImportedFunctionNode(
            node.node,
            node.typ,
            overloads,
            node.dispatch_plan,
            runtime_name,
        )
    if isinstance(node.node, ObjectNode):
        return TypedImportedObjectNode(node.node, node.typ, runtime_name)
    return node


def _rewrite_imported_self_calls(
    value: Any,
    source_name: Symbol,
    runtime_name: Symbol,
) -> Any:
    """Retarget recursive calls in an imported definition to its hidden binding."""
    if isinstance(value, TypedElementNode):
        rewritten = {
            field.name: _rewrite_imported_self_calls(
                getattr(value, field.name), source_name, runtime_name
            )
            for field in fields(value)
        }
        if isinstance(value.node, ElementNode) and value.node.name == source_name:
            rewritten["runtime_name"] = runtime_name
            rewritten["overload_index"] = 0
        return replace(value, **rewritten)
    if isinstance(value, tuple):
        return tuple(
            _rewrite_imported_self_calls(item, source_name, runtime_name)
            for item in value
        )
    if is_dataclass(value) and not isinstance(value, ASTNode):
        return replace(
            value,
            **{
                field.name: _rewrite_imported_self_calls(
                    getattr(value, field.name), source_name, runtime_name
                )
                for field in fields(value)
            },
        )
    return value
