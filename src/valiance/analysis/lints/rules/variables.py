"""Lint rules for variables, parameters, and closure state."""

from __future__ import annotations

from collections import Counter
from dataclasses import fields, is_dataclass

import valiance.vtypes as T

from valiance.asts import (
    ASTNode,
    ForNode,
    FunctionNode,
    GetVariableNode,
    SetVariableNode,
    SetVariablesNode,
)

from ..contexts import BlockLintContext, NodeLintContext
from ..models import finding
from ..registry import LintRegistry


def register(registry: LintRegistry) -> None:
    """Register variable-use lint rules."""
    registry.register_node(ForNode, unused_foreach_index)
    registry.register_node(SetVariableNode, captured_variable_write)
    registry.register_block(constants_never_reassigned)


def unused_foreach_index(context: NodeLintContext):
    """Report a named foreach index that is not read by the loop body."""
    node = context.node
    if not isinstance(node, ForNode) or node.index_variable is None:
        return ()
    if context.branch.stack and T.collection_item_type(context.branch.top) is None:
        return ()
    if _reads_name(node.body, node.index_variable):
        return ()
    return (
        finding(
            "unused-loop-index",
            f"foreach index '${node.index_variable}' is never read; remove it from "
            "the loop binding",
            node,
        ),
    )


def captured_variable_write(context: NodeLintContext):
    """Explain that writes to captures are local to one closure invocation."""
    node = context.node
    if not isinstance(node, SetVariableNode):
        return ()
    captured = {name for name, _typ in context.branch.variables.captures}
    if node.name not in captured:
        return ()
    return (
        finding(
            "captured-write-not-persistent",
            f"writing captured '${node.name}' changes only this invocation's local "
            "copy; the value is not retained across calls",
            node,
        ),
    )


def constants_never_reassigned(context: BlockLintContext):
    """Suggest const for a mutable binding written exactly once in this block."""
    if not any(
        not _is_internal_name(write.name)
        for node in context.nodes
        for write in _direct_writes(node)
    ):
        # Pure expressions and most function bodies have no candidate writes.
        # Avoid traversing those ASTs just to build unused read facts.
        return ()
    # Collect read facts once instead of walking the entire prefix separately
    # for every binding. Nested functions are excluded from these facts, just
    # as they are from the recursive write search below.
    preceding_reads: set[object] = set()
    candidates = []
    for node in context.nodes:
        candidates.extend(
            write
            for write in _direct_writes(node)
            if not _is_internal_name(write.name)
            and write.name not in preceding_reads
        )
        preceding_reads.update(_read_names(node))

    write_counts = Counter(write.name for write in _nested_writes(context.nodes))
    findings = []
    for assignment in candidates:
        name = assignment.name
        if assignment.constant:
            continue
        if write_counts[name] != 1:
            continue
        if name not in preceding_reads:
            continue
        findings.append(
            finding(
                "constant-never-reassigned",
                f"'${name}' is never reassigned; declare it with const",
                assignment,
            )
        )
    return tuple(findings)


def _is_internal_name(name: object) -> bool:
    """Return whether a binding name is reserved for compiler-generated state."""
    return getattr(name, "text", "").startswith("\x00")


def _direct_writes(node: ASTNode):
    """Yield writes owned by this lexical block, excluding nested scopes."""
    if isinstance(node, FunctionNode):
        return
    if isinstance(node, SetVariableNode):
        yield node
        return
    if isinstance(node, SetVariablesNode):
        yield from node.targets
        return
    if isinstance(node, ForNode):
        return


def _nested_writes(values: object):
    """Yield assignments recursively while excluding nested function scopes."""
    if isinstance(values, FunctionNode):
        return
    if isinstance(values, SetVariableNode):
        yield values
        return
    if isinstance(values, SetVariablesNode):
        yield from values.targets
        return
    if isinstance(values, ASTNode) and is_dataclass(values):
        for field in fields(values):
            if field.name != "location":
                yield from _nested_writes(getattr(values, field.name))
    elif isinstance(values, (tuple, list)):
        for value in values:
            yield from _nested_writes(value)


def _reads_name(values: object, name: object) -> bool:
    """Return whether AST content reads a variable name outside nested functions."""
    if isinstance(values, FunctionNode):
        return False
    if isinstance(values, GetVariableNode):
        return values.name == name
    if isinstance(values, ASTNode) and is_dataclass(values):
        return any(
            _reads_name(getattr(values, field.name), name)
            for field in fields(values)
            if field.name != "location"
        )
    if isinstance(values, (tuple, list)):
        return any(_reads_name(value, name) for value in values)
    return False


def _read_names(values: object):
    """Yield reads in one AST subtree without entering nested function bodies."""
    if isinstance(values, FunctionNode):
        return
    if isinstance(values, GetVariableNode):
        yield values.name
        return
    if isinstance(values, ASTNode) and is_dataclass(values):
        for field in fields(values):
            if field.name != "location":
                yield from _read_names(getattr(values, field.name))
    elif isinstance(values, (tuple, list)):
        for value in values:
            yield from _read_names(value)
