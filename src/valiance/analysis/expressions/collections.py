"""Concrete collections expression handlers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import fields, is_dataclass, replace
from typing import Callable, cast

import valiance.analysis.contracts.annotations as annotation_hooks
from valiance.analysis.lints import KNOWN_LINT_CODES, finding
import valiance.vtypes as T
from valiance.asts import (
    AnnotationNode,
    ASTNode,
    AssertNode,
    AtNode,
    CastNode,
    DictLiteralNode,
    ElementTagDeclarationNode,
    FileLintSuppressionNode,
    FunctionNode,
    FunctionParam,
    ImportNode,
    IndexAccessNode,
    IndexSetNode,
    ListLiteralNode,
    LintSuppressionNode,
    NumberLiteralNode,
    ObjectNode,
    PopNNode,
    RecordLiteralNode,
    ReturnNode,
    StackShuffleNode,
    StringInterpolationNode,
    StringLiteralNode,
    TagApplicationNode,
    TagDeclarationNode,
    TagOverlayNode,
    TupleLiteralNode,
    TypedAssertNode,
    TypedAtNode,
    TypedCallNode,
    TypedForNode,
    TypedFunctionNode,
    TypedIfNode,
    TypedNode,
    TypedTagApplicationNode,
    TypedUnfoldNode,
    TypedWhileNode,
    UnfoldNode,
)
from valiance.asts.nodes import (
    BreakNode,
    CallNode,
    FieldAccessNode,
    FieldSetNode,
    ForNode,
    GetVariableNode,
    IfNode,
    SetVariableNode,
    SetVariablesNode,
    WhileNode,
)
from valiance.modules_system.modules import (
    ModuleLoadError,
    import_environment_facts,
    import_objects,
)
from valiance.vtypes.symbols import Symbol
from valiance.vtypes.default_types import Boolean
from valiance.vtypes.relations import merge_stacks

from .. import analyser as _core
from ..calls import callable_values as _functions
from ..calls import candidates as _calls
from ..control_flow import patterns as _patterns
from ..support import analysis_utils as _utils



@_core.register(StringInterpolationNode)
def _string_interpolation_node(
    self: _core.Analyser,
    node: StringInterpolationNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `StringInterpolationNode` node and return the surviving branches."""
    current = _core.BranchSet((branch,))
    expression_count = 0
    for part in node.parts:
        if isinstance(part, str):
            continue

        expression_count += 1
        current = self.analyse_scoped_block(current, part)
        if not current:
            return _core.BranchSet()
        if any(not output.stack for output in current):
            self._diagnose(
                "string interpolation expression must leave a value",
                node,
            )
            return _core.BranchSet()

    terminal, current = _utils._split_terminal_branches(current)
    return _core.BranchSet.collect(
        (
            *terminal.branches,
            *(
                replace(
                    output,
                    stack=_utils._pop_stack(
                        output.stack,
                        expression_count,
                    ).push(T.String),
                    typed_body=branch.typed_body,
                ).emit(TypedNode(node, T.String))
                for output in current
                if len(output.stack) >= expression_count
            ),
        )
    )

def _direct_variable_expressions(value: object) -> Iterable[GetVariableNode]:
    """Yield direct variable expressions stored by one literal."""
    if isinstance(value, GetVariableNode):
        yield value
        return
    if isinstance(value, tuple):
        for item in value:
            yield from _direct_variable_expressions(item)
        return
    if is_dataclass(value) and not isinstance(value, type):
        for item in fields(value):
            yield from _direct_variable_expressions(getattr(value, item.name))


def _reject_noncopyable_literal_storage(
    self: _core.Analyser,
    node: ASTNode,
    branch: _core.AnalysisBranch,
    description: str,
) -> bool:
    """Reject definite variable duplication into persistent literal storage."""
    for source in _direct_variable_expressions(node):
        typ = branch.variables.read(source.name)
        if typ is None:
            continue
        reason = _utils._duplication_requirement(typ, self.env).reason
        if reason is None:
            continue
        self._diagnose(
            f"cannot store the value of '${source.name}' in {description}\n\n"
            f"the variable remains available, so the literal would create an "
            f"additional occurrence of {T.show(typ)}.\n\n{reason}",
            source,
        )
        return True
    return False


@_core.register(ListLiteralNode)
def _list_literal_node(
    self: _core.Analyser,
    node: ListLiteralNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `ListLiteralNode` node and return the surviving branches."""
    if _reject_noncopyable_literal_storage(self, node, branch, "a list element"):
        return _core.BranchSet()
    if not node.items:
        if node.typ is not None:
            typ = T.normalize(node.typ)
            if not isinstance(typ, T.CollectionType):
                self._diagnose(
                    f"empty list cast needs a list type, got {T.show(typ)}",
                    node,
                )
                return _core.BranchSet()
            return _core.BranchSet((branch.push(typ).emit(TypedNode(node, typ)),))

        self._diagnose(
            "empty list literal requires a type annotation or cast",
            node,
        )
        return _core.BranchSet()

    item_options = self._literal_item_options(
        branch,
        node.items,
        node,
        message="list item must leave a value on the stack",
    )
    if item_options is None:
        return _core.BranchSet()

    return _utils._literal_branch_results(
        branch,
        item_options,
        node,
        lambda combo: _list_literal_type(tuple(item.typ for item in combo)),
        self.env.context,
    )

def _list_literal_type(items: tuple[T.Type, ...]) -> T.Type:
    """Build the simplest exact-list type and lift tags shared by every item."""
    normalized_items = tuple(T.normalize(item) for item in items)
    base = _factor_common_exact_list_rank(normalized_items)
    if not isinstance(base, T.TaggedType):
        return T.normalize(T.C(T.ListExactType, base))
    lifted = tuple(T.DataTag(tag.name, tag.depth + 1, tag.absent) for tag in base.tags)
    return T.Tagged(
        T.normalize(T.C(T.ListExactType, base.inner)),
        *lifted,
        exact=base.exact,
    )


def _factor_common_exact_list_rank(items: tuple[T.Type, ...]) -> T.Type:
    """Factor the common exact-list prefix shared by every literal item."""
    if not items or not all(isinstance(item, T.ListExactType) for item in items):
        return T.normalize(T.U(*items))

    exact_items = cast(tuple[T.ListExactType, ...], items)
    if not all(isinstance(item.rank, int) for item in exact_items):
        return T.normalize(T.U(*items))

    common_rank = min(cast(int, item.rank) for item in exact_items)
    remainders = tuple(
        item.base
        if item.rank == common_rank
        else T.ExactList(item.base, cast(int, item.rank) - common_rank)
        for item in exact_items
    )
    return T.normalize(T.ExactList(T.U(*remainders), common_rank))


@_core.register(TupleLiteralNode)
def _tuple_literal_node(
    self: _core.Analyser,
    node: TupleLiteralNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `TupleLiteralNode` node and return the surviving branches."""
    if _reject_noncopyable_literal_storage(self, node, branch, "a tuple element"):
        return _core.BranchSet()
    item_options = self._literal_item_options(branch, node.items, node)
    if item_options is None:
        return _core.BranchSet()

    return _utils._literal_branch_results(
        branch,
        item_options,
        node,
        lambda combo: T.Tup(*(item.typ for item in combo)),
        self.env.context,
    )

def _isolated_literal_item_options(
    self: _core.Analyser,
    branch: _core.AnalysisBranch,
    expressions: tuple[tuple[ASTNode, ...], ...],
    node: ASTNode,
) -> tuple[tuple[_core.ListItemAnalysis, ...], ...] | None:
    """Analyse each record/dictionary expression on its own empty stack."""
    isolated = branch.with_stack(T.TypeStack())
    return self._literal_item_options(
        isolated,
        expressions,
        node,
        message=(
            "record/dictionary entry expression must produce a value without "
            "reading the outer stack"
        ),
    )


def _isolated_literal_results(
    self: _core.Analyser,
    branch: _core.AnalysisBranch,
    item_options: tuple[tuple[_core.ListItemAnalysis, ...], ...],
    node: ASTNode,
    literal_type: Callable[[tuple[_core.ListItemAnalysis, ...]], T.Type],
) -> _core.BranchSet:
    """Build a literal while preserving the caller's complete outer stack."""
    isolated = branch.with_stack(T.TypeStack())
    outputs = _utils._literal_branch_results(
        isolated, item_options, node, literal_type, self.env.context
    )
    return _core.BranchSet.collect(
        replace(output, stack=branch.stack.push(output.stack[-1]))
        for output in outputs
    )


@_core.register(RecordLiteralNode)
def _record_literal_node(
    self: _core.Analyser,
    node: RecordLiteralNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse each record field independently on an empty stack."""
    if _reject_noncopyable_literal_storage(self, node, branch, "a record field"):
        return _core.BranchSet()
    expressions = tuple(expr for _, expr in node.fields)
    item_options = _isolated_literal_item_options(self, branch, expressions, node)
    if item_options is None:
        return _core.BranchSet()
    return _isolated_literal_results(
        self, branch, item_options, node,
        lambda combo: T.Row(
            T.N(Symbol("record")),
            *(T.Field(name, item.typ) for (name, _), item in zip(node.fields, combo, strict=True)),
        ),
    )


@_core.register(DictLiteralNode)
def _dict_literal_node(
    self: _core.Analyser,
    node: DictLiteralNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse every dictionary key and value independently on an empty stack."""
    if _reject_noncopyable_literal_storage(self, node, branch, "a dictionary entry"):
        return _core.BranchSet()
    expressions = tuple(expr for entry in node.entries for expr in entry)
    item_options = _isolated_literal_item_options(self, branch, expressions, node)
    if item_options is None:
        return _core.BranchSet()
    return _isolated_literal_results(
        self, branch, item_options, node,
        lambda combo: T.N(
            Symbol("Dict"),
            T.U(*(item.typ for item in combo[::2])),
            T.U(*(item.typ for item in combo[1::2])),
        ),
    )


