"""Concrete AST node handlers registered with the analyser."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import cast

import valiance.analysis.annotations as annotation_hooks
import valiance.types as T
from valiance.asts import (
    AnnotationNode,
    ArrayLiteralNode,
    AssertNode,
    AtNode,
    CastNode,
    DictLiteralNode,
    ElementTagDeclarationNode,
    FunctionNode,
    FunctionParam,
    ImportNode,
    IndexAccessNode,
    IndexSetNode,
    ListLiteralNode,
    NumberLiteralNode,
    ObjectNode,
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
from valiance.modules import ModuleLoadError, import_environment_facts, import_objects
from valiance.symbols import Symbol
from valiance.types.default_types import Boolean
from valiance.types.relations import merge_stacks

from . import analyser as _core
from . import _analyser_functions as _functions
from . import _analyser_calls as _calls
from . import _analyser_patterns as _patterns
from . import _analyser_utils as _utils


@_core.register(NumberLiteralNode)
def _number_literal(
    self: _core.Analyser,
    node: NumberLiteralNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `NumberLiteralNode` node and return the surviving branches."""
    typ = _utils._number_literal_type(node.value)
    return _core.BranchSet((branch.push(typ).emit(TypedNode(node, typ)),))


@_core.register(StringLiteralNode)
def _string_literal(
    self: _core.Analyser,
    node: StringLiteralNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `StringLiteralNode` node and return the surviving branches."""
    return _core.BranchSet((branch.push(T.String).emit(TypedNode(node, T.String)),))


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
            inferred = node.declared_type or T.V(f"_inferred_{node.name}")
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
        target.declared_type or T.V(f"_inferred_{target.name}")
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
        (
            branch.with_variables(variables)
            .pop(available)
            .emit(TypedNode(node, None)),
        )
    )


@_core.register(IfNode)
def _if_node(
    self: _core.Analyser,
    node: IfNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a conditional and retain typed nodes for both runtime branches."""
    diagnostics_before = len(self.diagnostics)
    condition = self.analyse_from(branch, node.condition)
    terminal, condition = _utils._split_terminal_branches(condition)
    if not condition:
        if terminal:
            return terminal
        if len(self.diagnostics) == diagnostics_before:
            self._diagnose("if condition must be a boolean value", node)
        return _core.BranchSet()
    body_inputs = self.require_stack_top_assignable(
        condition,
        expected=Boolean,
        location=node.location,
        message="if condition must be a boolean value",
        code="if-condition-type",
    )

    if not body_inputs or any(output.failed for output in body_inputs):
        self._diagnose("if condition must be a boolean value", node)
        return terminal

    outputs: list[_core.AnalysisBranch] = list(terminal.branches)
    saw_mismatched_inputs = False
    for body_input in body_inputs:
        condition_body = body_input.typed_body[len(branch.typed_body) :]
        then_outputs = self.analyse_from(body_input, node.then_branch)
        else_outputs = self.analyse_from(body_input, node.else_branch)

        for left in then_outputs:
            for right in else_outputs:
                if left.inputs != right.inputs:
                    saw_mismatched_inputs = True
                    continue

                if left.break_type is not None or right.break_type is not None:
                    for output in (left, right):
                        typ = output.break_type
                        if typ is None:
                            typ = _calls._returns_result_type(output.stack.items)
                        outputs.append(output.emit(TypedNode(node, typ)))
                    continue

                stack = merge_stacks(left.stack, right.stack, self.env.context)
                base = replace(
                    _calls._refine_branch_like(branch, left),
                    inputs=left.inputs,
                ).with_element_tags(right.element_tags).with_data_element_uses(
                    right.data_element_uses
                )
                variables = left.variables.merge_against(
                    right.variables,
                    base.variables,
                    self.env.context,
                )
                typ = _calls._returns_result_type(stack.items)
                typed_if = TypedIfNode(
                    node,
                    typ,
                    condition=condition_body,
                    then_branch=left.typed_body[len(body_input.typed_body) :],
                    else_branch=right.typed_body[len(body_input.typed_body) :],
                )
                outputs.append(
                    base.with_stack(stack).with_variables(variables).emit(typed_if)
                )

    if not outputs and saw_mismatched_inputs:
        self._diagnose("if branches inferred different inputs", node)

    return _core.BranchSet.collect(outputs)


@_core.register(AssertNode)
def _assert_node(
    self: _core.Analyser,
    node: AssertNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `AssertNode` node and return the surviving branches."""
    diagnostics_before = len(self.diagnostics)
    condition = self.analyse_from(branch, node.condition)
    terminal, condition = _utils._split_terminal_branches(condition)
    if not condition:
        if terminal:
            return terminal
        if len(self.diagnostics) == diagnostics_before:
            self._diagnose("assert condition must be a boolean value", node)
        return _core.BranchSet()
    condition = self.require_stack_top_assignable(
        condition,
        expected=Boolean,
        location=node.location,
        message="assert condition must be a boolean value",
        code="assert-condition-type",
    )

    if not condition or any(output.failed for output in condition):
        self._diagnose("assert condition must be a boolean value", node)
        return terminal

    condition_tags = frozenset(
        tag for output in condition for tag in output.element_tags
    )
    condition_uses = frozenset(
        use for output in condition for use in output.data_element_uses
    )
    typed_condition = _patterns._typed_block(
        condition,
        len(branch.typed_body),
        node.condition,
    )
    typed_assert = TypedAssertNode(
        node,
        None,
        condition=typed_condition,
    )
    success = (
        branch.with_element_tags(condition_tags)
        .with_data_element_uses(condition_uses)
        .emit(typed_assert)
    )
    if not node.else_branch:
        return _core.BranchSet.collect((*terminal.branches, success))

    else_outputs = self.analyse_from(branch, node.else_branch)
    typed_assert = TypedAssertNode(
        node,
        None,
        condition=typed_condition,
        else_branch=_patterns._typed_block(
            else_outputs,
            len(branch.typed_body),
            node.else_branch,
        ),
    )
    success = replace(
        success,
        typed_body=(*success.typed_body[:-1], typed_assert),
    ).with_element_tags(
        tag for output in else_outputs for tag in output.element_tags
    ).with_data_element_uses(
        use for output in else_outputs for use in output.data_element_uses
    )
    error_types = tuple(_utils._top_or_none(output.stack) for output in else_outputs)
    error_type = T.U(*error_types) if error_types else T.NoneType()
    assert_error = T.N(Symbol("AssertError"), error_type)
    return _core.BranchSet.collect((*terminal.branches, success.push(assert_error)))


@_core.register(BreakNode)
def _break_node(
    self: _core.Analyser,
    node: BreakNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `BreakNode` node and return the surviving branches."""
    value_outputs = self.analyse_from(branch, node.values)
    return _core.BranchSet.collect(
        value_branch.emit(
            TypedNode(node, _utils._top_or_none(value_branch.stack))
        ).with_break(_utils._top_or_none(value_branch.stack))
        for value_branch in value_outputs
    )


@_core.register(WhileNode)
def _while_node(
    self: _core.Analyser,
    node: WhileNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `WhileNode` node and return the surviving branches."""
    loop_input = branch
    if node.params is not None:
        params = _functions._params_to_types(node.params)
        sourced = branch.source_arguments(params)
        if sourced is None:
            self._diagnose("while loop inputs do not match stack", node)
            return _core.BranchSet()

        _, loop_input = sourced
        loop_input = loop_input.push(*params)
        named = tuple(
            (param.name, typ)
            for param, typ in zip(node.params, params, strict=True)
            if param.name is not None
        )
        if named:
            loop_input = loop_input.with_variables(
                _core.BranchVariables.from_parameters(
                    named,
                    captures=loop_input.variables,
                )
            )
        loop_input = replace(
            loop_input,
            input_mode=_core.InputMode.CYCLE_EXPLICIT_PARAMS,
            cycle_params=params,
        )

    diagnostics_before = len(self.diagnostics)
    condition = self.analyse_from(loop_input, node.condition)
    terminal, condition = _utils._split_terminal_branches(condition)
    if not condition:
        if terminal:
            return terminal
        if len(self.diagnostics) == diagnostics_before:
            self._diagnose("while condition must be a boolean value", node)
        return _core.BranchSet()
    body_inputs = self.require_stack_top_assignable(
        condition,
        expected=Boolean,
        location=node.location,
        message="while condition must be a boolean value",
        code="while-condition-type",
    )
    if not body_inputs or any(output.failed for output in body_inputs):
        self._diagnose("while condition must be a boolean value", node)
        return terminal

    body_outputs = self.analyse_scoped_block(body_inputs, node.body)
    if not body_outputs:
        return _core.BranchSet()

    joined: _core.AnalysisBranch | None = None
    for output in body_outputs:
        if joined is None:
            joined = output
            continue

        if joined.inputs != output.inputs:
            self._diagnose("while body inferred different inputs", node)
            return _core.BranchSet()

        stack = merge_stacks(joined.stack, output.stack, self.env.context)
        variables = joined.variables.merge_against(
            output.variables,
            loop_input.variables,
            self.env.context,
        )
        joined = joined.with_stack(stack).with_variables(variables)
        joined = joined.with_element_tags(output.element_tags)
        joined = joined.with_data_element_uses(output.data_element_uses)

    if joined is None:
        return _core.BranchSet()

    variables = (
        joined.variables
        if node.params is None
        else joined.variables.merge_against(
            loop_input.variables,
            branch.variables,
            self.env.context,
        )
    )
    result = _calls._refine_branch_like(branch, joined).with_variables(variables)
    condition_body = _patterns._typed_block(
        condition,
        len(loop_input.typed_body),
        node.condition,
    )
    body_start = (
        len(body_inputs.branches[0].typed_body)
        if body_inputs.branches
        else len(loop_input.typed_body)
    )
    body = _patterns._typed_block(body_outputs, body_start, node.body)
    return _core.BranchSet.collect(
        (
            *terminal.branches,
            result.emit(
                TypedWhileNode(
                    node,
                    _calls._returns_result_type(result.stack.items),
                    condition=condition_body,
                    body=body,
                )
            ),
        )
    )


@_core.register(ReturnNode)
def _return_node(
    self: _core.Analyser,
    node: ReturnNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `ReturnNode` node and return the surviving branches."""
    return _core.BranchSet((branch.emit(TypedNode(node, None)),))


def _at_collection_view(typ: T.Type) -> T.CollectionType | None:
    """Build the view of at collection during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        return _at_collection_view(typ.inner)
    return typ if isinstance(typ, T.CollectionType) else None


def _at_level_type(source: T.Type, target_rank: int) -> T.Type | None:
    """Determine the type of at level during static analysis."""
    source = T.normalize(source)
    if isinstance(source, (T.TaggedType, T.ExactType, T.AtomicType)):
        return _at_level_type(source.inner, target_rank)
    if not isinstance(source, T.CollectionType):
        return source if target_rank == 0 else None
    if not isinstance(source.rank, int) or source.rank < target_rank:
        return None
    if target_rank == 0:
        return source.base
    collection_type: type[T.CollectionType]
    if isinstance(source, (T.ListExactType, T.ListMinType)):
        collection_type = T.ListExactType
    elif isinstance(source, T.ListRuggedType):
        collection_type = T.ListRuggedType
    elif isinstance(source, (T.ArrayExactType, T.ArrayMinType)):
        collection_type = T.ArrayExactType
    else:
        return None
    return T.C(collection_type, source.base, target_rank)


@_core.register(AtNode)
def _at_node(
    self: _core.Analyser,
    node: AtNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `AtNode` node and return the surviving branches."""
    arity = len(node.levels)
    source_hints = tuple(
        T.V(f"_at_{branch.origin}_{index}") for index in range(arity)
    )
    sourced = branch.source_arguments(source_hints)
    if sourced is None:
        self._diagnose(
            f"at requires {arity} value(s) on the stack",
            node,
        )
        return _core.BranchSet()

    source_types, popped = sourced
    target_types: list[T.Type] = []
    explicit_target_ranks: list[int | None] = []
    minimum_depths: list[int] = []
    for level, source_type in zip(node.levels, source_types, strict=True):
        target = _at_level_type(source_type, level.depth)
        if target is None:
            self._diagnose(
                f"at level '{level.name}' requires rank {level.depth}, "
                f"but received {T.show(source_type)}",
                node,
            )
            return _core.BranchSet()
        target_types.append(target)
        collection = _at_collection_view(source_type)
        if collection is None:
            explicit_target_ranks.append(None)
            minimum_depths.append(0)
        else:
            explicit_target_ranks.append(level.depth)
            minimum_depths.append(
                max(collection.rank - level.depth, 0)
                if isinstance(collection.rank, int)
                else 0
            )

    params = tuple(
        FunctionParam(
            None if level.name.text == "_" else level.name,
            target_type,
        )
        for level, target_type in zip(node.levels, target_types, strict=True)
    )
    function_node = FunctionNode(
        params=params,
        body=node.body,
        location=node.location,
    )
    analysed = self._analyse_function_literal(popped, function_node)
    if analysed is None:
        return _core.BranchSet()
    function, _ = analysed
    typed_function = TypedFunctionNode(
        function_node,
        function.typ,
        function.overloads,
    )

    candidates: list[tuple[int, T.AppliedOverload]] = []
    for index, overload_typing in enumerate(function.overloads):
        overload = overload_typing.overload
        if not isinstance(overload, T.Overload):
            continue
        applied = T.apply_overload(overload, source_types, self.env.context)
        if applied is None:
            continue
        applied = replace(
            applied,
            vectorised=any(depth > 0 for depth in minimum_depths),
            vectorised_depths=tuple(minimum_depths),
            vectorised_target_ranks=tuple(explicit_target_ranks),
        )
        candidates.append((index, applied))

    if not candidates:
        self._diagnose("at body does not accept the selected level values", node)
        return _core.BranchSet()
    if len(candidates) > 1:
        self._diagnose("at body has ambiguous inferred overloads", node)
        return _core.BranchSet()

    overload_index, applied = candidates[0]
    result = popped.with_stack(popped.stack.push(*applied.actual_returns))
    result = result.with_element_tags(applied.element_tags)
    return _core.BranchSet(
        (
            result.emit(
                TypedAtNode(
                    node,
                    _calls._returns_result_type(applied.actual_returns),
                    typed_function,
                    applied,
                    overload_index,
                )
            ),
        )
    )


@_core.register(FunctionNode)
def _function_node(
    self: _core.Analyser,
    node: FunctionNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `FunctionNode` node and return the surviving branches."""
    if not self._validate_annotations(node.annotations, "fn", node):
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))

    function_node = _functions._genericize_function_node(node, node.generics)
    self._validate_function_element_tags(function_node, node)
    result = self._analyse_function_literal(branch, function_node)
    if result is None:
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))

    function, typed_branch = result
    typed_node = TypedFunctionNode(function_node, function.typ, function.overloads)
    return _core.BranchSet((typed_branch.push(function.typ).emit(typed_node),))


@_core.register(CastNode)
def _cast_node(
    self: _core.Analyser,
    node: CastNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `CastNode` node and return the surviving branches."""
    # ``exact`` and ``atomic`` are callable-parameter policy, not value
    # constructors.  A cast may target a callable whose own parameters carry
    # those policies, but a marker wrapped around the cast value itself is
    # erased.
    target = _functions._parameter_value_type(T.normalize(node.typ))
    self._validate_element_tags_in_types((target,), node)
    if not self._validate_data_tags(
        ((target,),),
        node,
        allow_variants=False,
        require_declared=True,
    ):
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    if not branch.stack:
        self._diagnose(
            f"empty stack when casting to {T.show(target)}",
            node,
        )
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))

    source = branch.stack[-1]
    if node.checked:
        if T.assignable(source, target, self.env.context):
            node = replace(node, checked=False)
        elif (
            invalid_runtime_type := _patterns._uncheckable_runtime_type(target)
        ) is not None:
            self._diagnose(
                f"{T.show(invalid_runtime_type)} cannot be checked at runtime",
                node,
            )
            return _core.BranchSet()
        elif not _utils._types_overlap(source, target, self.env.context):
            if _functions._type_contains_rank_var(target):
                stack = T.TypeStack((*branch.stack.items[:-1], target))
                return _core.BranchSet(
                    (branch.with_stack(stack).emit(TypedNode(node, target)),)
                )
            self._diagnose(
                f"cannot cast {T.show(source)} to {T.show(target)}",
                node,
            )
            return _core.BranchSet()
    elif not T.assignable(source, target, self.env.context):
        self._diagnose(
            f"cannot safely cast {T.show(source)} to {T.show(target)}",
            node,
        )
        return _core.BranchSet()

    stack = T.TypeStack((*branch.stack.items[:-1], target))
    return _core.BranchSet((branch.with_stack(stack).emit(TypedNode(node, target)),))


@_core.register(StackShuffleNode)
def _stack_shuffle_node(
    self: _core.Analyser,
    node: StackShuffleNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `StackShuffleNode` node and return the surviving branches."""
    params = tuple(
        T.V(f"_shuffle_{index}") for index, _ in enumerate(node.prestack)
    )
    sourced = branch.source_arguments(params)
    if sourced is None:
        self._diagnose(
            f"stack underflow for {node.mode}; expected "
            f"{len(node.prestack)} value(s)",
            node,
        )
        return _core.BranchSet()

    args, popped = sourced
    labelled = {
        label: typ
        for label, typ in zip(node.prestack, args, strict=True)
        if label is not None
    }
    stack_arg_start = len(node.prestack) - min(
        len(branch.stack),
        len(node.prestack),
    )
    copy_errors = tuple(
        _utils._copy_diagnostic(typ, self.env)
        for typ in _utils._copied_stack_shuffle_types(
            node,
            args,
            labelled,
            stack_arg_start,
        )
    )
    for error in copy_errors:
        if error is not None:
            self._diagnose(error, node)
            return _core.BranchSet()

    post_types = tuple(labelled[label] for label in node.poststack)
    if node.mode == Symbol("copy"):
        stack = branch.stack.push(*post_types)
    else:
        kept = tuple(
            typ
            for label, typ in zip(node.prestack, args, strict=True)
            if label is None
        )
        stack = popped.stack.push(*kept, *post_types)

    return _core.BranchSet(
        (
            popped.with_stack(stack).emit(
                TypedNode(node, _calls._returns_result_type(post_types))
            ),
        )
    )


@_core.register(FieldAccessNode)
def _field_access_node(
    self: _core.Analyser,
    node: FieldAccessNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `FieldAccessNode` node and return the surviving branches."""
    sourced = self._source_field_receiver(
        branch,
        node.name,
        optional_safe=node.optional_safe,
    )
    if sourced is None:
        action = "safely access" if node.optional_safe else "access"
        self._diagnose(
            f"empty stack when trying to {action} field '{node.name}'",
            node,
        )
        return _core.BranchSet()

    receiver_type, field_type, branch = sourced
    if field_type is None:
        if node.optional_safe:
            self._diagnose(
                f"optional type {T.show(receiver_type)} has no known field "
                f"'{node.name}' on its present value",
                node,
            )
        else:
            self._diagnose(
                f"type {T.show(receiver_type)} has no known field '{node.name}'",
                node,
            )
        return _core.BranchSet()

    return _core.BranchSet((branch.push(field_type).emit(TypedNode(node, field_type)),))


@_core.register(FieldSetNode)
def _field_set_node(
    self: _core.Analyser,
    node: FieldSetNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `FieldSetNode` node and return the surviving branches."""
    if len(branch.stack) < 2:
        self._diagnose(
            f"field assignment to '{node.name}' requires receiver and value",
            node,
        )
        return _core.BranchSet()

    receiver_type = branch.stack[-2]
    value_type = branch.stack[-1]
    if node.optional_safe:
        field_type, refined_receiver = self._safe_field_type(
            receiver_type,
            node.name,
            branch,
            write=True,
        )
    else:
        field_type, refined_receiver = self._field_type(
            receiver_type,
            node.name,
            branch,
            write=True,
        )
    if field_type is None:
        if node.optional_safe:
            self._diagnose(
                f"optional type {T.show(receiver_type)} has no writable field "
                f"'{node.name}' on its present value",
                node,
            )
        else:
            self._diagnose(
                f"type {T.show(receiver_type)} has no writable field '{node.name}'",
                node,
            )
        return _core.BranchSet()

    if not T.assignable(value_type, field_type, self.env.context):
        self._diagnose(
            f"cannot assign {T.show(value_type)} to field '{node.name}' "
            f"of type {T.show(field_type)}",
            node,
        )
        return _core.BranchSet()

    result_type = receiver_type if refined_receiver is None else refined_receiver
    stack = T.TypeStack(branch.stack.items[:-2]).push(result_type)
    return _core.BranchSet(
        (branch.with_stack(stack).emit(TypedNode(node, result_type)),)
    )


@_core.register(IndexAccessNode)
def _index_access_node(
    self: _core.Analyser,
    node: IndexAccessNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `IndexAccessNode` node and return the surviving branches."""
    selector_values = _patterns._selector_value_count(node.selectors)
    required = selector_values + 1
    if len(branch.stack) >= required:
        receiver_type = branch.stack[-required]
        base_branch = branch.with_stack(T.TypeStack(branch.stack.items[:-required]))
    elif len(branch.stack) == selector_values:
        source_branch = branch.with_stack(
            T.TypeStack(branch.stack.items[: len(branch.stack) - selector_values])
        )
        sourced = source_branch.source_arguments((T.V("IndexReceiver"),))
        if sourced is None:
            self._diagnose("indexing requires receiver and index value(s)", node)
            return _core.BranchSet()
        (receiver_type,), base_branch = sourced
    else:
        self._diagnose("indexing requires receiver and index value(s)", node)
        return _core.BranchSet()

    index_types = branch.stack.items[-selector_values:] if selector_values else ()
    if not _patterns._selectors_assignable(
        receiver_type,
        node.selectors,
        index_types,
        self.env.context,
    ):
        self._diagnose("list indexing requires Integer index value(s)", node)
        return _core.BranchSet()

    result_type = _patterns._indexed_type(receiver_type, node.selectors, node.spread)
    return _core.BranchSet(
        (base_branch.push(result_type).emit(TypedNode(node, result_type)),)
    )


@_core.register(IndexSetNode)
def _index_set_node(
    self: _core.Analyser,
    node: IndexSetNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `IndexSetNode` node and return the surviving branches."""
    selector_values = _patterns._selector_value_count(node.selectors)
    required = selector_values + 2
    if len(branch.stack) < required:
        self._diagnose(
            "indexed assignment requires value, receiver, and index",
            node,
        )
        return _core.BranchSet()

    value_type = branch.stack[-required]
    receiver_type = branch.stack[-selector_values - 1]
    index_types = branch.stack.items[-selector_values:] if selector_values else ()
    if not _patterns._selectors_assignable(
        receiver_type,
        node.selectors,
        index_types,
        self.env.context,
    ):
        self._diagnose("list indexing requires Integer index value(s)", node)
        return _core.BranchSet()

    item_type = _patterns._indexed_type(receiver_type, node.selectors, spread=False)
    updated_receiver_type = _patterns._indexed_assignment_type(
        receiver_type,
        node.selectors,
        value_type,
        self.env.context,
    )
    if updated_receiver_type is None:
        self._diagnose(
            f"cannot assign {T.show(value_type)} to indexed item "
            f"of type {T.show(item_type)}",
            node,
        )
        return _core.BranchSet()

    stack = T.TypeStack(branch.stack.items[:-required]).push(updated_receiver_type)
    return _core.BranchSet(
        (branch.with_stack(stack).emit(TypedNode(node, updated_receiver_type)),)
    )


@_core.register(CallNode)
def _call_node(
    self: _core.Analyser,
    node: CallNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `CallNode` node and return the surviving branches."""
    if not branch.stack:
        self._diagnose("call requires a function on the stack", node)
        return _core.BranchSet()

    callable_type = T.normalize(branch.stack[-1])
    overloads = _functions._callable_overloads(callable_type)
    if not overloads:
        self._diagnose(
            f"cannot call non-function value of type {T.show(callable_type)}",
            node,
        )
        return _core.BranchSet()

    callable_popped = branch.pop()
    diagnostics_before = len(self.diagnostics)
    arg_branches = self.analyse_from(callable_popped, node.args)
    terminal, arg_branches = _utils._split_terminal_branches(arg_branches)
    if not arg_branches:
        if terminal:
            return terminal
        if len(self.diagnostics) > diagnostics_before:
            return _core.BranchSet()

    candidates: list[_core.CallCandidate] = []
    for arg_branch in arg_branches:
        for overload in overloads:
            sourced = arg_branch.source_arguments(overload.params)
            if sourced is None:
                continue
            args, popped = sourced
            candidate = _calls._apply_overload_to_branch(
                overload,
                args,
                popped,
                self.env.context,
                analyser=self,
            )
            if candidate is None:
                continue

            candidates.append(_core.CallCandidate(candidate.applied, candidate.branch))

    winners = self.select_call_winners(
        candidates=candidates,
        branch=callable_popped,
        node=node,
        no_match_message=(
            f"no overloads for call target {T.show(callable_type)} match stack "
            f"{_utils._show_stack(callable_popped.stack)}\n"
            f"{_utils._show_overload_list(None, overloads)}"
        ),
        ambiguous_message=(
            f"ambiguous call target {T.show(callable_type)} with stack "
            f"{_utils._show_stack(callable_popped.stack)}"
        ),
    )
    if winners is None:
        return terminal

    return _core.BranchSet.collect(
        (
            *terminal.branches,
            *(
                candidate.branch.push(*candidate.applied.actual_returns).emit(
                    TypedCallNode(
                        node,
                        _calls._returns_result_type(candidate.applied.actual_returns),
                        candidate.applied,
                    )
                )
                for candidate in winners
            ),
        )
    )


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


@_core.register(ListLiteralNode)
def _list_literal_node(
    self: _core.Analyser,
    node: ListLiteralNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `ListLiteralNode` node and return the surviving branches."""
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
    """Build a list type, lifting tags common to every item by one depth."""
    base = T.normalize(T.U(*items))
    if not isinstance(base, T.TaggedType):
        return T.C(T.ListExactType, base)
    lifted = tuple(
        T.DataTag(tag.name, tag.depth + 1, tag.absent) for tag in base.tags
    )
    return T.Tagged(
        T.C(T.ListExactType, base.inner),
        *lifted,
        exact=base.exact,
    )


@_core.register(TupleLiteralNode)
def _tuple_literal_node(
    self: _core.Analyser,
    node: TupleLiteralNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `TupleLiteralNode` node and return the surviving branches."""
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


@_core.register(RecordLiteralNode)
def _record_literal_node(
    self: _core.Analyser,
    node: RecordLiteralNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `RecordLiteralNode` node and return the surviving branches."""
    expressions = tuple(expr for _, expr in node.fields)
    item_options = self._literal_item_options(branch, expressions, node)
    if item_options is None:
        return _core.BranchSet()

    return _utils._literal_branch_results(
        branch,
        item_options,
        node,
        lambda combo: T.Row(
            T.N(Symbol("record")),
            *(
                T.Field(name, item.typ)
                for (name, _), item in zip(node.fields, combo, strict=True)
            ),
        ),
        self.env.context,
    )


@_core.register(DictLiteralNode)
def _dict_literal_node(
    self: _core.Analyser,
    node: DictLiteralNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `DictLiteralNode` node and return the surviving branches."""
    expressions = tuple(expr for entry in node.entries for expr in entry)
    item_options = self._literal_item_options(branch, expressions, node)
    if item_options is None:
        return _core.BranchSet()

    return _utils._literal_branch_results(
        branch,
        item_options,
        node,
        lambda combo: T.N(
            Symbol("Dict"),
            T.U(*(item.typ for item in combo[::2])),
            T.U(*(item.typ for item in combo[1::2])),
        ),
        self.env.context,
    )


@_core.register(ArrayLiteralNode)
def _array_literal_node(
    self: _core.Analyser,
    node: ArrayLiteralNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `ArrayLiteralNode` node and return the surviving branches."""
    return _core.BranchSet((branch.emit(TypedNode(node, None)),))


@_core.register(ForNode)
def _for_node(
    self: _core.Analyser,
    node: ForNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `ForNode` node and return the surviving branches."""
    consumes_stack_iterable = bool(branch.stack)
    if not branch.stack:
        item = _functions._anonymous_type_var(branch, 1)
        sourced = branch.source_arguments((T.ExactList(item),))
        if sourced is None:
            self._diagnose("for loop requires iterable on the stack", node)
            return _core.BranchSet()
        (iterable_type,), branch = sourced
    else:
        iterable_type = branch.stack[-1]

    item_type = T.collection_item_type(iterable_type)
    if not item_type:
        self._diagnose(
            "for loop iterable must actually be iterable. "
            f"Got {T.show(iterable_type)}",
            node,
        )
        return _core.BranchSet()

    body_stack = branch.stack.pop() if consumes_stack_iterable else branch.stack
    body_branch = branch.with_stack(body_stack)
    cycle_params = (item_type,)
    if node.index_variable is not None:
        cycle_params = (item_type, T.Integer)
    body_branch = replace(
        body_branch,
        input_mode=_core.InputMode.CYCLE_EXPLICIT_PARAMS,
        cycle_params=cycle_params,
    )
    body_branch = body_branch.with_variables(
        body_branch.variables.with_block_local(node.variable, item_type)
    )
    if node.index_variable is not None:
        body_branch = body_branch.with_variables(
            body_branch.variables.with_block_local(node.index_variable, T.Integer)
        )

    body_outputs = self.analyse_from(body_branch, node.body)
    if not body_outputs:
        return _core.BranchSet()

    refined_item_type = _utils._loop_variable_output_type(
        node.variable,
        body_outputs,
        self.env.context,
    )
    if (
        refined_item_type is not None
        and _functions._contains_type_var(item_type)
        and not T.same(item_type, refined_item_type)
    ):
        body_branch = body_branch.refine_type(item_type, refined_item_type)
        body_outputs = _core.BranchSet.collect(
            output.refine_type(item_type, refined_item_type)
            for output in body_outputs
        )

    break_types = tuple(
        output.break_type for output in body_outputs if output.break_type is not None
    )
    result_type = _utils._loop_break_result_type(break_types)
    loop_locals = (node.variable,) + (
        (node.index_variable,) if node.index_variable is not None else ()
    )
    variables = _utils._merge_loop_variables(
        body_branch.variables,
        body_outputs,
        loop_locals,
        self.env.context,
    )
    typed_for = TypedForNode(
        node,
        result_type,
        body=_patterns._typed_block(
            body_outputs,
            len(body_branch.typed_body),
            node.body,
        ),
    )
    body_element_tags = frozenset(
        tag for output in body_outputs for tag in output.element_tags
    )
    body_data_element_uses = frozenset(
        use for output in body_outputs for use in output.data_element_uses
    )
    return _core.BranchSet(
        (
            _calls._refine_branch_like(branch, body_branch)
            .with_element_tags(body_element_tags)
            .with_data_element_uses(body_data_element_uses)
            .with_stack(body_branch.stack.push(result_type))
            .with_variables(variables)
            .emit(typed_for),
        )
    )


@_core.register(UnfoldNode)
def _unfold_node(
    self: _core.Analyser,
    node: UnfoldNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `UnfoldNode` node and return the surviving branches."""
    body_function = FunctionNode(
        params=node.params,
        body=node.body,
        annotations=(AnnotationNode(Symbol("returnAll")),),
        element_tags=frozenset(),
        location=node.location,
    )
    body_analysis = self._analyse_unfold_body_function(branch, body_function)
    if body_analysis is None:
        return _core.BranchSet()

    candidates: list[_core.CallCandidate] = []
    for overload in _functions._callable_overloads(body_analysis.typ):
        condition_element_tags: frozenset[T.ElementTag] = frozenset()
        state_arity = len(overload.params)
        if state_arity == 0:
            self._diagnose("unfold requires at least one state value", node)
            continue
        if len(overload.returns) > state_arity + 1:
            self._diagnose(
                "unfold body may not produce more than state arity plus one value",
                node,
            )
            continue

        if node.condition:
            condition_function = FunctionNode(
                params=(
                    tuple(
                        FunctionParam(param.name, typ)
                        for param, typ in zip(
                            node.params or (),
                            overload.params,
                            strict=False,
                        )
                    )
                    if node.params is not None
                    else tuple(FunctionParam(None, typ) for typ in overload.params)
                ),
                body=node.condition,
                returns=(Boolean,),
                element_tags=frozenset(),
                location=node.location,
            )
            condition_result = self._analyse_function_literal(
                branch,
                condition_function,
            )
            if condition_result is None:
                self._diagnose("unfold condition must return a boolean value", node)
                continue
            condition_analysis, _ = condition_result
            condition_element_tags = frozenset(
                tag
                for candidate_overload in _functions._callable_overloads(
                    condition_analysis.typ
                )
                for tag in candidate_overload.element_tags
                if not tag.absent
            )

        sourced = branch.source_arguments(overload.params)
        if sourced is None:
            self._diagnose("unfold inputs do not match stack", node)
            continue
        args, popped = sourced
        applied = T.try_apply_overload(overload, args, self.env.context).applied
        if applied is None:
            continue
        candidates.append(
            _core.CallCandidate(
                applied=applied,
                branch=popped,
                callable_overload_index=state_arity,
            )
        )

    results: list[_core.AnalysisBranch] = []
    for candidate in _functions._best_candidates(candidates, branch):
        generated = _patterns._unfold_emitted_type(
            candidate.applied.params,
            candidate.applied.actual_returns,
        )
        list_type = T.WithTag(T.ExactList(generated), "infinite")
        results.append(
            candidate.branch.with_element_tags(
                (*candidate.applied.element_tags, *condition_element_tags)
            ).push(list_type).emit(
                TypedUnfoldNode(
                    node,
                    list_type,
                    state_arity=cast(int, candidate.callable_overload_index),
                    function=TypedFunctionNode(
                        body_function,
                        body_analysis.typ,
                        body_analysis.overloads,
                    ),
                )
            )
        )
    return _core.BranchSet.collect(results)


def _runtime_tag_removal_closure(
    tags: Iterable[T.DataTag],
    ctx: T.Context,
) -> tuple[T.DataTag, ...]:
    """Remove variant evidence whenever its computed parent is removed."""
    pending = list(tags)
    removed = set(pending)
    while pending:
        current = pending.pop()
        for variant, parent in ctx.tag_parents.items():
            candidate = T.DataTag(variant.text, current.depth)
            if parent.text == current.name and candidate not in removed:
                removed.add(candidate)
                pending.append(candidate)
    return tuple(sorted(removed))


@_core.register(TagDeclarationNode)
def _tag_declaration_node(
    self: _core.Analyser,
    node: TagDeclarationNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `TagDeclarationNode` node and return the surviving branches."""
    if node.disjoint is not None:
        if isinstance(node.disjoint, T.DataTag):
            self.env.add_disjoint_tags(node.tag.name, node.disjoint.name)
        else:
            self.env.add_disjoint_data_element_tags(node.tag.name, node.disjoint)
    elif node.parent is not None:
        parent = self.env.lookup_tag(node.parent.name)
        if parent is None:
            self._diagnose(
                f"variant tag '#{node.tag.name}' requires declared computed "
                f"parent '#{node.parent.name}'",
                node,
            )
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))
        if parent.kind is not T.TagKind.COMPUTED:
            self._diagnose(
                f"variant tag '#{node.tag.name}' parent '#{node.parent.name}' "
                "must be computed",
                node,
            )
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))
        self.env.add_variant_tag(node.tag.name, node.parent.name)
    elif node.kind == Symbol("constructed"):
        self.env.add_constructed_tag(node.tag.name)
    elif node.kind == Symbol("unit"):
        self.env.add_unit_tag(node.tag.name)
    else:
        self.env.add_computed_tag(node.tag.name)

    return _core.BranchSet((branch.emit(TypedNode(node, None)),))


@_core.register(ElementTagDeclarationNode)
def _element_tag_declaration_node(
    self: _core.Analyser,
    node: ElementTagDeclarationNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `ElementTagDeclarationNode` node and return the surviving branches."""
    if node.disjoint is not None:
        if isinstance(node.disjoint, T.DataTag):
            self.env.add_disjoint_data_element_tags(node.disjoint.name, node.name)
        else:
            self.env.add_disjoint_element_tags(node.name, node.disjoint)
    elif node.kind == Symbol("companion"):
        self.env.add_companion_element_tag(node.name)
    else:
        self.env.add_property_element_tag(node.name)

    return _core.BranchSet((branch.emit(TypedNode(node, None)),))


@_core.register(TagOverlayNode)
def _tag_overlay_node(
    self: _core.Analyser,
    node: TagOverlayNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `TagOverlayNode` node and return the surviving branches."""
    if self.env.lookup_tag(node.tag.name) is None:
        self._diagnose(f"unknown data tag '#{node.tag.name}'", node)
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    public = node.visibility == Symbol("public")
    for element in node.elements:
        for params, returns in node.signatures:
            if not self._validate_data_tags((params, returns), node):
                continue
            overlay_error = _tag_overlay_contract_error(
                node.tag.name,
                params,
                returns,
                self.env.context,
            )
            if overlay_error is not None:
                self._diagnose(overlay_error, node)
                continue
            overload = T.Overload(params=params, returns=returns)
            if node.generics:
                overload = _functions._genericize_overload(overload, node.generics)
            self.env.define_tag_overlay(
                node.tag.name,
                element,
                overload,
                public=public,
            )

    return _core.BranchSet((branch.emit(TypedNode(node, None)),))


def _tag_overlay_contract_error(
    name: str,
    params: tuple[T.Type, ...],
    returns: tuple[T.Type, ...],
    ctx: T.Context,
) -> str | None:
    """Return why an overlay would violate tag ownership or rank flow."""
    source_tags = tuple(
        (tag, _calls._type_rank(T.normalize(param)))
        for param in params
        for tag in _top_level_data_tags(param)
        if tag.name == name and not tag.absent
    )
    if not source_tags:
        return f"tag overlay '#{name}' must require that tag on an input"

    for ret in returns:
        return_rank = _calls._type_rank(T.normalize(ret))
        for tag in _top_level_data_tags(ret):
            if tag.name != name:
                return (
                    f"tag overlay '#{name}' cannot add, remove, or preserve "
                    f"foreign tag '#{tag.name}' in its return contract"
                )
            if tag.absent or not ctx.is_constructed_like_tag(name):
                continue
            valid_source = any(
                return_rank >= max(source_rank - source.depth, 0)
                and tag.depth == max(return_rank - 1, 0)
                for source, source_rank in source_tags
            )
            if not valid_source:
                return (
                    f"constructed tag overlay '#{name}' has unsafe rank/depth "
                    "flow in its return contract"
                )
    return None


def _top_level_data_tags(typ: T.Type) -> tuple[T.DataTag, ...]:
    """Return tags decorating the value represented by ``typ``."""
    normalized = T.normalize(typ)
    return tuple(normalized.tags) if isinstance(normalized, T.TaggedType) else ()


@_core.register(TagApplicationNode)
def _tag_application_node(
    self: _core.Analyser,
    node: TagApplicationNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `TagApplicationNode` node and return the surviving branches."""
    definition = self.env.lookup_tag(node.tag.name)
    if definition is None:
        self._diagnose(f"unknown data tag '#{node.tag.name}'", node)
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    sourced = branch.source_arguments((T.V("_tagged_value"),))
    if sourced is None:
        self._diagnose(
            f"empty stack when applying tag '{_calls._show_tag(node.tag)}'",
            node,
        )
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))

    (value_type,), base_branch = sourced
    value_rank = _calls._type_rank(T.normalize(value_type))
    if node.tag.depth > value_rank:
        self._diagnose(
            f"data tag '{_calls._show_tag(node.tag)}' has depth "
            f"{node.tag.depth}, but {T.show(value_type)} has rank {value_rank}",
            node,
        )
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))

    validator: T.AppliedOverload | None = None
    validator_index: int | None = None
    validator_runtime_name: Symbol | None = None
    validator_plans: list[tuple[Symbol, int]] = []
    added_tags: tuple[T.DataTag, ...] = ()
    removed_tags: tuple[T.DataTag, ...] = ()
    if node.tag.absent:
        tagged = _calls._remove_data_tag(value_type, node.tag)
        if tagged is None:
            self._diagnose(
                f"cannot remove absent tag '{_calls._show_tag(node.tag)}' from "
                f"{value_type}",
                node,
            )
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))
        removed_tags = _runtime_tag_removal_closure(
            (T.DataTag(node.tag.name, node.tag.depth),),
            self.env.context,
        )
    else:
        added = [T.DataTag(node.tag.name, node.tag.depth)]
        parent = self.env.context.tag_parent(node.tag.name)
        if parent is not None:
            added.append(T.DataTag(parent.text, node.tag.depth))
            tagged = _calls._with_data_tags(
                value_type,
                (T.DataTag(parent.text, node.tag.depth),),
                self.env.context,
            )
        else:
            tagged = _calls._with_data_tags(value_type, (node.tag,), self.env.context)
        added_tags = tuple(added)
        disjoint_names: set[Symbol] = set()
        for added_tag in added_tags:
            disjoint_names.update(
                self.env.context.tag_disjoints(added_tag.name)
            )
        removed_tags = _runtime_tag_removal_closure(
            (
                T.DataTag(str(name), node.tag.depth)
                for name in sorted(disjoint_names, key=str)
            ),
            self.env.context,
        )
        for removed_tag in removed_tags:
            without = _calls._remove_data_tag(tagged, removed_tag)
            if without is not None:
                tagged = without
        for applied_tag in added_tags:
            validator_name = Symbol(f"#{applied_tag.name}")
            validator_overloads = self.env.overloads_for(validator_name)
            if not validator_overloads:
                continue
            resolved = T.resolve_overload_result(
                validator_overloads,
                (value_type,),
                self.env.context,
            )
            matching = tuple(
                (index, overload)
                for index, overload in enumerate(validator_overloads)
                if T.try_apply_overload(
                    overload,
                    (value_type,),
                    self.env.context,
                ).applied
                is not None
            )
            if resolved is None:
                if matching:
                    self._diagnose(
                        f"ambiguous validator overloads for '{validator_name}' "
                        f"with {T.show(value_type)}",
                        node,
                    )
                else:
                    self._diagnose(
                        f"no validator overload for '{validator_name}' matches "
                        f"{T.show(value_type)}",
                        node,
                    )
                return _core.BranchSet((branch.emit(TypedNode(node, None)),))
            selected_index = next(
                index
                for index, overload in enumerate(validator_overloads)
                if overload is resolved.overload or overload == resolved.overload
            )
            selected = validator_overloads[selected_index]
            if not _calls._validator_overload_ok(selected, self.env.context):
                self._diagnose(
                    f"tag validator '{validator_name}' must return "
                    "#boolean Number",
                    node,
                )
                return _core.BranchSet((branch.emit(TypedNode(node, None)),))
            applied = T.try_apply_overload(
                selected,
                (value_type,),
                self.env.context,
            ).applied
            assert applied is not None
            static_result = self.env.tag_validator_static_result(
                validator_name,
                selected_index,
            )
            if static_result is True:
                continue
            elif static_result is False:
                self._diagnose(
                    f"tag validator '{validator_name}' is statically false",
                    node,
                )
                return _core.BranchSet((branch.emit(TypedNode(node, None)),))
            runtime_name = self.env.runtime_name_for(validator_name) or validator_name
            validator_plans.append((runtime_name, selected_index))
            if applied_tag.name == node.tag.name:
                validator = applied
                validator_index = selected_index
                validator_runtime_name = runtime_name

    stack = base_branch.stack.push(tagged)
    typed = TypedTagApplicationNode(
        node,
        tagged,
        validator,
        validator_index,
        added_tags,
        removed_tags,
        validator_runtime_name,
        tuple(validator_plans),
    )
    return _core.BranchSet((base_branch.with_stack(stack).emit(typed),))


@_core.register(ImportNode)
def _import_node(
    self: _core.Analyser,
    node: ImportNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `ImportNode` node and return the surviving branches."""
    for spec in node.specs:
        try:
            exports, resolved_spec, definitions = self._load_import_definitions(spec)
            objects = import_objects(exports, resolved_spec)
            import_environment_facts(exports, resolved_spec, self.env)
        except ModuleLoadError as exc:
            self._diagnose(str(exc), node)
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))

        for typed_node in exports.runtime_prelude:
            self._prelude.add(typed_node)
        for obj in objects:
            runtime_name = self._prelude.add_declaration(obj.typed, obj.name)
            self._register_imported_object(obj, runtime_name)
        for definition in definitions:
            runtime_name = self._prelude.add_declaration(
                definition.typed,
                definition.name,
            )
            self._register_imported_definition(
                definition.name,
                definition.typed,
                runtime_name,
            )

    return _core.BranchSet((branch.emit(TypedNode(node, None)),))


@_core.register(ObjectNode)
def _object_node(
    self: _core.Analyser,
    node: ObjectNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `ObjectNode` node and return the surviving branches."""
    if not self._validate_annotations(node.annotations, node.kind.text, node):
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))

    node = annotation_hooks.DEFAULT_REGISTRY.transform_object(node)
    kind = node.kind.text
    if kind == "object":
        return self._object_definition(branch, node)
    if kind == "trait":
        return self._trait_definition(branch, node)
    if kind == "variant":
        return self._variant_definition(branch, node)
    if kind == "enum":
        return self._enum_definition(branch, node)

    self._diagnose(f"unknown object-like declaration '{node.kind}'", node)
    return _core.BranchSet((branch.emit(TypedNode(node, None)),))
