"""Concrete assignments expression handlers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import cast

import valiance.analysis.contracts.annotations as annotation_hooks
from valiance.analysis.lints import KNOWN_LINT_CODES, finding
import valiance.vtypes as T
from valiance.asts import (
    AnnotationNode,
    ArrayLiteralNode,
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



@_core.register(GetVariableNode)
def _get_variable(
    self: _core.Analyser,
    node: GetVariableNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `GetVariableNode` node and return the surviving branches."""
    typ = branch.variables.read(node.name)

    if typ is None:
        message = f"undefined variable '{node.name}'"
        suggestions = _utils._similar_names(
            str(node.name),
            branch.variables.visible_names(),
        )
        if suggestions:
            message += f"\ndid you mean '${suggestions[0]}'?"
        self._diagnose(message, node)
        return _core.BranchSet(
            (
                branch.error(
                    message,
                    node.location,
                    code="undefined-variable",
                ).emit(TypedNode(node, None)),
            )
        )

    return _core.BranchSet((branch.push(typ).emit(TypedNode(node, typ)),))

@_core.register(SetVariableNode)
def _set_variable(
    self: _core.Analyser,
    node: SetVariableNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `SetVariableNode` node and return the surviving branches."""
    if node.declared_type is not None and not self._validate_data_tags(
        ((node.declared_type,),),
        node,
    ):
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    if not branch.stack:
        if branch.input_mode is _core.InputMode.INFER_INPUTS:
            inferred = node.declared_type or T.M(
                f"_inferred_{node.name}",
                T.MetaVarId(
                    branch.origin,
                    40_000 + (node.location.offset if node.location is not None else 0),
                ),
            )
            write = branch.variables.write(
                node.name,
                inferred,
                constant=node.constant,
                ctx=self.env.context,
            )

            if write.error is not None:
                self._diagnose(write.error, node)
                return _core.BranchSet(
                    (
                        branch.error(
                            write.error,
                            node.location,
                            code="variable-write",
                        ),
                    )
                )

            if write.variables is None:
                return _core.BranchSet(
                    (
                        branch.error(
                            f"cannot assign to variable '{node.name}'",
                            node.location,
                            code="variable-write",
                        ),
                    )
                )

            return _core.BranchSet(
                (
                    branch.with_variables(write.variables).emit(
                        TypedNode(node, inferred)
                    ),
                )
            )

        return _core.BranchSet(
            (
                branch.error(
                    f"empty stack when trying to assign to variable '{node.name}'",
                    node.location,
                    code="stack-underflow",
                ),
            )
        )

    value_type = branch.stack[-1]
    variable_type = node.declared_type or value_type

    if node.declared_type is not None and not T.assignable(
        value_type,
        node.declared_type,
        self.env.context,
    ):
        self._diagnose(
            f"cannot assign {T.show(value_type)} to variable '{node.name}' "
            f"of declared type {T.show(node.declared_type)}",
            node,
        )
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))

    write = branch.variables.write(
        node.name,
        variable_type,
        block_local=True,
        constant=node.constant,
        ctx=self.env.context,
    )

    if write.error is not None:
        self._diagnose(write.error, node)
        return _core.BranchSet(
            (
                branch.error(
                    write.error,
                    node.location,
                    code="variable-write",
                ),
            )
        )

    if write.variables is None:
        return _core.BranchSet(
            (
                branch.error(
                    f"cannot assign to variable '{node.name}'",
                    node.location,
                    code="variable-write",
                ),
            )
        )

    return _core.BranchSet(
        (
            branch.with_variables(write.variables)
            .pop()
            .emit(TypedNode(node, variable_type)),
        )
    )

@_core.register(SetVariablesNode)
def _set_variables_node(
    self: _core.Analyser,
    node: SetVariablesNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `SetVariablesNode` node and return the surviving branches."""
    if not node.targets:
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))

    available = min(len(branch.stack), len(node.targets))
    missing = len(node.targets) - available
    if missing and branch.input_mode is not _core.InputMode.INFER_INPUTS:
        return _core.BranchSet(
            (
                branch.error(
                    "empty stack when trying to assign to multiple variables",
                    node.location,
                    code="stack-underflow",
                ),
            )
        )

    inferred = tuple(
        target.declared_type or T.M(
            f"_inferred_{target.name}",
            T.MetaVarId(
                branch.origin,
                50_000 + (target.location.offset if target.location is not None else 0),
            ),
        )
        for target in node.targets[:missing]
    )
    value_types = inferred + branch.stack.items[len(branch.stack) - available :]
    variables = branch.variables
    for target, value_type in zip(node.targets, value_types, strict=True):
        if target.declared_type is not None and not self._validate_data_tags(
            ((target.declared_type,),),
            target,
        ):
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))
        variable_type = target.declared_type or value_type
        if target.declared_type is not None and not T.assignable(
            value_type,
            target.declared_type,
            self.env.context,
        ):
            self._diagnose(
                f"cannot assign {T.show(value_type)} to variable "
                f"'{target.name}' of declared type {T.show(target.declared_type)}",
                target,
            )
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))

        write = variables.write(
            target.name,
            variable_type,
            block_local=True,
            constant=target.constant,
            ctx=self.env.context,
        )
        if write.error is not None:
            self._diagnose(write.error, target)
            return _core.BranchSet(
                (
                    branch.error(
                        write.error,
                        target.location,
                        code="variable-write",
                    ),
                )
            )
        if write.variables is None:
            return _core.BranchSet(
                (
                    branch.error(
                        f"cannot assign to variable '{target.name}'",
                        target.location,
                        code="variable-write",
                    ),
                )
            )
        variables = write.variables

    return _core.BranchSet(
        (branch.with_variables(variables).pop(available).emit(TypedNode(node, None)),)
    )

