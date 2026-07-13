"""Shared analyser diagnostics, branch merging, and type refinement helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from itertools import count
from typing import cast

from valiance.runtime_values import RuntimeNumber
import valiance.types as T
from valiance.asts import (
    AnnotationNode,
    ASTNode,
    FunctionOverloadTyping,
    FunctionParam,
    ListLiteralNode,
    ObjectNode,
    StackShuffleNode,
    StringLiteralNode,
    TraitRequirementNode,
    TypedAtNode,
    TypedCallNode,
    TypedElementExtension,
    TypedElementNode,
    TypedExtensionPatternRule,
    TypedFunctionNode,
    TypedIfNode,
    TypedImportedFunctionNode,
    TypedImportedObjectNode,
    TypedLiteralNode,
    TypedNode,
    TypedTagApplicationNode,
    TypedUnfoldNode,
)
from valiance.symbols import Symbol

from . import analyser as _core
from . import _analyser_functions as _functions


def _show_stack(stack: T.TypeStack) -> str:
    """Format stack during static analysis."""
    if not stack:
        return "[]"
    return "[" + ", ".join(T.show(item) for item in stack.items) + "]"


def _diagnostic_message(message: str, node: ASTNode | None) -> str:
    """Format the message for diagnostic during static analysis."""
    if node is None or node.location is None:
        return message
    location = node.location
    return f"{location.line}:{location.column}: {message}"


def _show_overload_list(
    name: Symbol | None,
    overloads: Iterable[T.Overload],
) -> str:
    """Render available overloads as a scan-friendly signature list."""
    rendered = tuple(_show_overload_signature(name, overload) for overload in overloads)
    if not rendered:
        return "available overloads: none"
    return "available overloads:\n" + "\n".join(
        f"  - {signature}" for signature in rendered
    )


def _show_overload_signature(
    name: Symbol | None,
    overload: T.Overload,
    *,
    params: tuple[T.Type, ...] | None = None,
    returns: tuple[T.Type, ...] | None = None,
) -> str:
    """Render one callable signature without repeating ``Function``."""
    actual_params = overload.params if params is None else params
    actual_returns = overload.returns if returns is None else returns
    param_names = overload.param_names
    if len(param_names) < len(actual_params):
        param_names = (None,) * (len(actual_params) - len(param_names)) + param_names
    rendered_params = []
    for index, param in enumerate(actual_params):
        param_name = param_names[index] if index < len(param_names) else None
        rendered = T.show(param)
        rendered_params.append(
            f"{param_name}: {rendered}" if param_name is not None else rendered
        )
    if not actual_returns:
        rendered_returns = "()"
    elif len(actual_returns) == 1:
        rendered_returns = T.show(actual_returns[0])
    else:
        rendered_returns = (
            "(" + ", ".join(T.show(item) for item in actual_returns) + ")"
        )
    prefix = "" if name is None else str(name)
    signature = f"{prefix}({', '.join(rendered_params)}) -> {rendered_returns}"
    if overload.element_tags:
        tags = ", ".join(
            _show_element_tag(tag) for tag in sorted(overload.element_tags)
        )
        signature += f" <{tags}>"
    if overload.generic_constraints:
        constraints = ", ".join(
            f"{constraint.name}: {T.show(constraint.bound)}"
            for constraint in overload.generic_constraints
        )
        signature += f" where {constraints}"
    return signature


def _show_element_tag(tag: T.ElementTag) -> str:
    """Render one effect tag used by an overload signature."""
    prefix = "!" if tag.absent else ""
    if not tag.args:
        return f"{prefix}{tag.name}"
    return f"{prefix}{tag.name}[{', '.join(T.show(arg) for arg in tag.args)}]"


def _show_applied_overloads(
    candidates: Iterable[_core.CallCandidate],
) -> str:
    """Format applied overload candidates as a multiline signature list."""
    rendered = tuple(
        _show_overload_signature(
            None,
            candidate.applied.overload,
            params=candidate.applied.params,
            returns=candidate.applied.actual_returns,
        )
        for candidate in candidates
    )
    if not rendered:
        return "  - none"
    return "\n".join(f"  - {signature}" for signature in rendered)


def _name_similarity(attempted: str, candidate: str) -> float:
    """Return a transposition-aware, case-insensitive typo similarity score."""
    left = attempted.casefold()
    right = candidate.casefold()
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0

    rows = len(left) + 1
    columns = len(right) + 1
    distance = [[0] * columns for _ in range(rows)]
    for row in range(rows):
        distance[row][0] = row
    for column in range(columns):
        distance[0][column] = column

    for row in range(1, rows):
        for column in range(1, columns):
            substitution = 0 if left[row - 1] == right[column - 1] else 1
            distance[row][column] = min(
                distance[row - 1][column] + 1,
                distance[row][column - 1] + 1,
                distance[row - 1][column - 1] + substitution,
            )
            if (
                row > 1
                and column > 1
                and left[row - 1] == right[column - 2]
                and left[row - 2] == right[column - 1]
            ):
                distance[row][column] = min(
                    distance[row][column],
                    distance[row - 2][column - 2] + 1,
                )

    return 1.0 - distance[-1][-1] / max(len(left), len(right))


def _similar_names(
    attempted: str,
    names: Iterable[Symbol],
    *,
    limit: int = 3,
) -> tuple[str, ...]:
    """Return close source-visible names ordered by typo likelihood."""
    ranked = sorted(
        (
            (_name_similarity(attempted, str(name)), str(name))
            for name in names
            if not _internal_element_name(name)
        ),
        key=lambda item: (-item[0], item[1]),
    )
    return tuple(name for score, name in ranked if score >= 0.62)[:limit]


def _internal_element_name(name: Symbol) -> bool:
    """Return whether a compiler-generated callable should stay hidden."""
    return name.text.startswith("*::") or any(
        part.startswith("__valiance_") for part in name.namespace
    )


def _top_or_none(stack: T.TypeStack) -> T.Type:
    """Compute top or none during static analysis."""
    if stack:
        return stack[-1]
    return T.NoneType()


def _loop_break_result_type(break_types: tuple[T.Type, ...]) -> T.Type:
    """Determine the type of loop break result during static analysis."""
    if not break_types:
        return T.NoneType()
    if len(break_types) == 1:
        return T.optional(break_types[0])
    return T.optional(T.U(*break_types))


def _list_item_analysis(
    base: _core.AnalysisBranch,
    output: _core.AnalysisBranch,
) -> _core.ListItemAnalysis | None:
    """Compute list item analysis during static analysis."""
    if output.break_type is not None or not output.stack:
        return None
    return _core.ListItemAnalysis(
        branch=output,
        typ=output.stack[-1],
        consumed=_forked_stack_consumption(base.stack, output.stack.pop()),
        typed_body=output.typed_body[len(base.typed_body) :],
    )


def _literal_branch_results(
    branch: _core.AnalysisBranch,
    item_options: tuple[tuple[_core.ListItemAnalysis, ...], ...],
    node: ASTNode,
    literal_type: Callable[[tuple[_core.ListItemAnalysis, ...]], T.Type],
    ctx: T.Context,
) -> _core.BranchSet:
    """Compute the results for literal branch during static analysis."""
    results: list[_core.AnalysisBranch] = []
    for combo in _cartesian_product(item_options):
        inputs = _merge_inferred_inputs(branch.inputs, combo)
        if inputs is None:
            continue
        consumed = max((item.consumed for item in combo), default=0)
        typ = (
            T.Never()
            if any(_is_never(item.typ) for item in combo)
            else literal_type(combo)
        )
        variables = _merge_list_item_variables(
            branch.variables,
            combo,
            ctx,
        )
        element_tags = frozenset(
            tag for item in combo for tag in item.branch.element_tags
        )
        data_element_uses = frozenset(
            use for item in combo for use in item.branch.data_element_uses
        )
        results.append(
            replace(
                branch,
                stack=_pop_stack(branch.stack, consumed).push(typ),
                inputs=inputs,
                variables=variables,
            )
            .emit(
                TypedLiteralNode(
                    node,
                    typ,
                    tuple(item.typed_body for item in combo),
                )
            )
            .with_element_tags(element_tags)
            .with_data_element_uses(data_element_uses)
        )
    return _core.BranchSet.collect(results)


def _forked_stack_consumption(base: T.TypeStack, item_remainder: T.TypeStack) -> int:
    """Compute forked stack consumption during static analysis."""
    prefix = 0
    limit = min(len(base), len(item_remainder))
    while prefix < limit and T.same(base[prefix], item_remainder[prefix]):
        prefix += 1
    return len(base) - prefix


def _cartesian_product(
    options: tuple[tuple[_core.ListItemAnalysis, ...], ...],
) -> Iterator[tuple[_core.ListItemAnalysis, ...]]:
    """Compute cartesian product during static analysis."""
    if not options:
        yield ()
        return
    first, rest = options[0], options[1:]
    for item in first:
        for suffix in _cartesian_product(rest):
            yield (item, *suffix)


def _merge_inferred_inputs(
    base_inputs: tuple[T.Type, ...],
    items: tuple[_core.ListItemAnalysis, ...],
) -> tuple[T.Type, ...] | None:
    """Merge inferred inputs during static analysis."""
    suffixes: list[tuple[T.Type, ...]] = []
    for item in items:
        if item.branch.inputs[: len(base_inputs)] != base_inputs:
            return None
        suffixes.append(item.branch.inputs[len(base_inputs) :])

    merged: list[T.Type] = []
    max_len = max((len(suffix) for suffix in suffixes), default=0)
    for index in range(max_len):
        candidates = tuple(suffix[index] for suffix in suffixes if index < len(suffix))
        merged.append(candidates[0] if len(candidates) == 1 else T.U(*candidates))
    return base_inputs + tuple(merged)


def _merge_branch_inputs(
    left: tuple[T.Type, ...],
    right: tuple[T.Type, ...],
    ctx: T.Context,
) -> tuple[T.Type, ...] | None:
    """Merge branch inputs during static analysis."""
    if len(left) != len(right):
        return None
    return tuple(
        (
            left_item
            if T.same(left_item, right_item)
            else T.merge_types(left_item, right_item, ctx)
        )
        for left_item, right_item in zip(left, right, strict=True)
    )


def _merge_list_item_variables(
    before: _core.BranchVariables,
    items: tuple[_core.ListItemAnalysis, ...],
    ctx: T.Context,
) -> _core.BranchVariables:
    """Merge list item variables during static analysis."""
    merged = before
    for item in items:
        merged = merged.merge_against(item.branch.variables, before, ctx)
    return merged


def _pop_stack(stack: T.TypeStack, count: int) -> T.TypeStack:
    """Compute pop stack during static analysis."""
    if count == 0:
        return stack
    return stack.pop(count)


def _merge_loop_variables(
    before: _core.BranchVariables,
    outputs: _core.BranchSet,
    loop_locals: tuple[Symbol, ...],
    ctx: T.Context,
) -> _core.BranchVariables:
    """Merge loop variables during static analysis."""
    before_loop = _drop_named_block_locals(before, loop_locals)
    merged = before_loop
    for output in outputs:
        merged = merged.merge_against(
            _drop_named_block_locals(output.variables, loop_locals),
            before_loop,
            ctx,
        )
    return merged


def _drop_named_block_locals(
    variables: _core.BranchVariables,
    names: tuple[Symbol, ...],
) -> _core.BranchVariables:
    """Compute drop named block locals during static analysis."""
    blocked = set(names)
    return _core.BranchVariables(
        function_locals=variables.function_locals,
        parameters=variables.parameters,
        captures=variables.captures,
        block_locals=tuple(
            (name, typ) for name, typ in variables.block_locals if name not in blocked
        ),
    )


def _loop_variable_output_type(
    name: Symbol,
    outputs: _core.BranchSet,
    ctx: T.Context,
) -> T.Type | None:
    """Determine the type of loop variable output during static analysis."""
    types = tuple(
        typ for output in outputs if (typ := output.variables.read(name)) is not None
    )
    if not types:
        return None
    merged = types[0]
    for typ in types[1:]:
        merged = T.merge_types(merged, typ, ctx)
    return merged


def _has_never_return(overload: T.Overload) -> bool:
    """Return whether the analyser helper has never return."""
    return any(isinstance(T.normalize(ret), T.NeverType) for ret in overload.returns)


def _is_never(t: T.Type) -> bool:
    """Return whether the value is never."""
    return isinstance(T.normalize(t), T.NeverType)


def _split_terminal_branches(
    branches: _core.BranchSet,
) -> tuple[_core.BranchSet, _core.BranchSet]:
    """Partition non-returning ``Never`` paths from normally continuing paths."""
    terminal: list[_core.AnalysisBranch] = []
    live: list[_core.AnalysisBranch] = []
    for branch in branches:
        (terminal if branch.terminal else live).append(branch)
    return _core.BranchSet.collect(terminal), _core.BranchSet.collect(live)


def _param_type(param: FunctionParam, index: int) -> T.Type:
    """Determine the type of param during static analysis."""
    if param.typ is not None:
        return param.typ
    name = param.name.text if param.name is not None else f"_{index}"
    return T.V(name)


def _trait_requirement(node: TraitRequirementNode) -> T.TraitRequirement | None:
    """Compute trait requirement during static analysis."""
    params = tuple(
        _param_type(param, index) for index, param in enumerate(node.params or ())
    )
    returns = node.returns or ()
    return T.TraitRequirement(
        node.name,
        T.Overload(
            params=params,
            returns=returns,
            param_names=tuple(param.name for param in node.params or ()),
        ),
    )


def _trait_requirements(node: ObjectNode) -> tuple[T.TraitRequirement, ...]:
    """Compute trait requirements during static analysis."""
    return tuple(
        _functions._genericize_requirement(requirement, node.generics)
        for item in node.requirements
        if (requirement := _trait_requirement(item)) is not None
    )


def _declared_nominal(name: Symbol, generics: tuple[Symbol, ...]) -> T.Type:
    """Compute declared nominal during static analysis."""
    return T.N(name, *(T.V(generic.text) for generic in generics))


def _types_overlap(source: T.Type, target: T.Type, ctx: T.Context) -> bool:
    """Return the Boolean result of types overlap during static analysis."""
    source = T.normalize(source)
    target = T.normalize(target)
    if T.assignable(source, target, ctx) or T.assignable(target, source, ctx):
        return True
    if isinstance(source, T.UnionType):
        return any(_types_overlap(item, target, ctx) for item in source.items)
    if isinstance(target, T.UnionType):
        return any(_types_overlap(source, item, ctx) for item in target.items)
    return False


def _copied_stack_shuffle_types(
    node: StackShuffleNode,
    args: tuple[T.Type, ...],
    labelled: dict[Symbol, T.Type],
    stack_arg_start: int,
) -> tuple[T.Type, ...]:
    """Determine the types used for copied stack shuffle during static analysis."""
    if node.mode == Symbol("copy"):
        return tuple(dict.fromkeys(labelled[label] for label in node.poststack))

    copied: list[T.Type] = []
    counts: dict[Symbol, int] = {}
    for label in node.poststack:
        counts[label] = counts.get(label, 0) + 1

    for index, (label, typ) in enumerate(zip(node.prestack, args, strict=True)):
        if label is None:
            if index < stack_arg_start:
                copied.append(typ)
            continue
        count = counts.get(label, 0)
        retains = count if index < stack_arg_start else max(count - 1, 0)
        if retains:
            copied.append(typ)
    return tuple(dict.fromkeys(copied))


def _copy_diagnostic(typ: T.Type, env: T.Environment) -> str | None:
    """Compute copy diagnostic during static analysis."""
    reason = _noncopyable_reason(typ, env)
    if reason is None:
        return None
    return f"cannot copy value of type {T.show(typ)}: {reason}"


def _noncopyable_reason(typ: T.Type, env: T.Environment) -> str | None:
    """Compute noncopyable reason during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        return _noncopyable_reason(typ.inner, env)
    if isinstance(typ, T.UnionType):
        for item in typ.items:
            reason = _noncopyable_reason(item, env)
            if reason is not None:
                return reason
        return None
    if isinstance(typ, T.IntersectionType):
        for item in typ.items:
            reason = _noncopyable_reason(item, env)
            if reason is not None:
                return reason
        return None
    if isinstance(typ, T.CollectionType):
        return _noncopyable_reason(typ.base, env)
    if isinstance(typ, T.TupleType):
        for item in typ.params:
            reason = _noncopyable_reason(item, env)
            if reason is not None:
                return reason
        return None
    if isinstance(typ, T.VariadicTupleType):
        for item in typ.items:
            reason = _noncopyable_reason(item.typ, env)
            if reason is not None:
                return reason
        return None
    if isinstance(typ, T.NominalType):
        return _nominal_copy_error(typ.name, env)
    return None


def _nominal_copy_error(name: Symbol, env: T.Environment) -> str | None:
    """Return the error description for nominal copy during static analysis."""
    overloads = env.overloads_for(Symbol(f"{name}::dup"))
    for overload in overloads:
        if overload.annotation_error is not None:
            return overload.annotation_error
    return None


def _number_literal_type(value: str) -> T.Type:
    """Determine the type of number literal during static analysis."""
    if "i" in value.lower():
        return T.Number
    try:
        parsed = RuntimeNumber(value)
    except InvalidOperation:
        return T.Number
    if parsed == parsed.to_integral_value():
        return T.Integer
    return T.Real


def _row_field_type(row: T.RowType, name: Symbol) -> T.Type | None:
    """Determine the type of row field during static analysis."""
    for row_field in row.fields:
        if row_field.name == name:
            return row_field.typ
    return None


def _refine_stack(stack: T.TypeStack, old: T.Type, new: T.Type) -> T.TypeStack:
    """Refine stack during static analysis."""
    return T.TypeStack(tuple(_refine_type(item, old, new) for item in stack.items))


def _refine_items(
    items: tuple[tuple[Symbol, T.Type], ...],
    old: T.Type,
    new: T.Type,
) -> tuple[tuple[Symbol, T.Type], ...]:
    """Refine items during static analysis."""
    return tuple((name, _refine_type(typ, old, new)) for name, typ in items)


def _refine_typed_body(
    typed_body: tuple[TypedNode, ...],
    old: T.Type,
    new: T.Type,
) -> tuple[TypedNode, ...]:
    """Refine typed body during static analysis."""
    return tuple(_refine_typed_node(node, old, new) for node in typed_body)


def _refine_typed_node(typed_node: TypedNode, old: T.Type, new: T.Type) -> TypedNode:
    """Refine typed node during static analysis."""
    typ = None if typed_node.typ is None else _refine_type(typed_node.typ, old, new)
    if isinstance(typed_node, TypedImportedFunctionNode):
        return TypedImportedFunctionNode(
            typed_node.node,
            typ,
            tuple(
                FunctionOverloadTyping(
                    _refine_type(overload.typ, old, new),
                    _refine_typed_body(overload.body, old, new),
                    overload.overload,
                )
                for overload in typed_node.overloads
            ),
            typed_node.dispatch_plan,
            typed_node.runtime_name,
        )
    if isinstance(typed_node, TypedFunctionNode):
        return TypedFunctionNode(
            typed_node.node,
            typ,
            tuple(
                FunctionOverloadTyping(
                    _refine_type(overload.typ, old, new),
                    _refine_typed_body(overload.body, old, new),
                    overload.overload,
                )
                for overload in typed_node.overloads
            ),
            typed_node.dispatch_plan,
        )
    if isinstance(typed_node, TypedLiteralNode):
        return TypedLiteralNode(
            typed_node.node,
            typ,
            tuple(_refine_typed_body(item, old, new) for item in typed_node.items),
        )
    if isinstance(typed_node, TypedTagApplicationNode):
        return TypedTagApplicationNode(
            typed_node.node,
            typ,
            typed_node.validator,
            typed_node.validator_index,
            typed_node.added_tags,
            typed_node.removed_tags,
            typed_node.validator_runtime_name,
            typed_node.validator_plans,
        )
    if isinstance(typed_node, TypedElementNode):
        return TypedElementNode(
            typed_node.node,
            typ,
            typed_node.overload,
            typed_node.overload_index,
            typed_node.modifier_args,
            typed_node.call_arg_order,
            typed_node.call_overload_index,
            _refine_typed_extension(typed_node.extension, old, new),
            typed_node.runtime_name,
        )
    if isinstance(typed_node, TypedCallNode):
        return TypedCallNode(
            typed_node.node,
            typ,
            typed_node.overload,
        )
    if isinstance(typed_node, TypedIfNode):
        return TypedIfNode(
            typed_node.node,
            typ,
            _refine_typed_body(typed_node.condition, old, new),
            _refine_typed_body(typed_node.then_branch, old, new),
            _refine_typed_body(typed_node.else_branch, old, new),
        )
    if isinstance(typed_node, TypedUnfoldNode):
        function = typed_node.function
        refined_function = (
            None
            if function is None
            else cast(
                TypedFunctionNode,
                _refine_typed_node(function, old, new),
            )
        )
        return TypedUnfoldNode(
            typed_node.node,
            typ,
            typed_node.state_arity,
            refined_function,
        )
    if isinstance(typed_node, TypedAtNode):
        function = typed_node.function
        refined_function = (
            None
            if function is None
            else cast(
                TypedFunctionNode,
                _refine_typed_node(function, old, new),
            )
        )
        return TypedAtNode(
            typed_node.node,
            typ,
            refined_function,
            typed_node.overload,
            typed_node.function_overload_index,
        )
    if isinstance(typed_node, TypedImportedObjectNode):
        return TypedImportedObjectNode(
            typed_node.node,
            typ,
            typed_node.runtime_name,
        )
    return TypedNode(typed_node.node, typ)


def _refine_typed_extension(
    extension: TypedElementExtension | None,
    old: T.Type,
    new: T.Type,
) -> TypedElementExtension | None:
    """Refine typed extension during static analysis."""
    if extension is None:
        return None

    def refine_function(function: TypedFunctionNode | None) -> TypedFunctionNode | None:
        """Refine function during static analysis."""
        if function is None:
            return None
        refined = _refine_typed_node(function, old, new)
        assert isinstance(refined, TypedFunctionNode)
        return refined

    return TypedElementExtension(
        default=refine_function(extension.default),
        rules=tuple(
            TypedExtensionPatternRule(
                rule.pattern,
                cast(TypedFunctionNode, _refine_typed_node(rule.function, old, new)),
            )
            for rule in extension.rules
        ),
        selector=refine_function(extension.selector),
    )


def _refine_type(typ: T.Type, old: T.Type, new: T.Type) -> T.Type:
    """Refine type during static analysis."""
    typ = T.normalize(typ)
    new = _erase_absent_tag_requirements(new)
    if T.same(typ, old):
        return new
    if isinstance(typ, T.AnonymousTraitType):
        return T.AnonymousTrait(
            typ.generics,
            (
                T.AnonymousTraitRequirement(
                    requirement.name,
                    _functions._transform_overload_types(
                        requirement.overload,
                        lambda item: _refine_type(item, old, new),
                    ),
                )
                for requirement in typ.requirements
            ),
        )
    if isinstance(typ, T.AtomicType):
        return T.Atomic(_refine_type(typ.inner, old, new))
    return _functions._transform_type_children(
        typ,
        lambda child: _refine_type(child, old, new),
    )


def _refine_input_requirement(typ: T.Type, old: T.Type, new: T.Type) -> T.Type:
    """Refine a function-input fact while preserving negative tag constraints."""
    typ = T.normalize(typ)
    if T.same(typ, old):
        return new
    if isinstance(typ, T.AnonymousTraitType):
        return T.AnonymousTrait(
            typ.generics,
            (
                T.AnonymousTraitRequirement(
                    requirement.name,
                    _functions._transform_overload_types(
                        requirement.overload,
                        lambda item: _refine_input_requirement(item, old, new),
                    ),
                )
                for requirement in typ.requirements
            ),
        )
    if isinstance(typ, T.AtomicType):
        return T.Atomic(_refine_input_requirement(typ.inner, old, new))
    return _functions._transform_type_children(
        typ,
        lambda child: _refine_input_requirement(child, old, new),
    )


def _refine_input_requirement_items(
    items: tuple[tuple[Symbol, T.Type], ...], old: T.Type, new: T.Type
) -> tuple[tuple[Symbol, T.Type], ...]:
    """Refine named input facts while preserving negative tag constraints."""
    return tuple(
        (name, _refine_input_requirement(typ, old, new)) for name, typ in items
    )


def _erase_absent_tag_requirements(typ: T.Type) -> T.Type:
    """Compute erase absent tag requirements during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType) and all(tag.absent for tag in typ.tags):
        return typ.inner
    return typ


def _stack_assignable(
    actual: T.TypeStack,
    expected: T.TypeStack,
    ctx: T.Context,
) -> bool:
    """Return the Boolean result of stack assignable during static analysis."""
    if len(actual) < len(expected):
        return False
    actual_returns = actual.items[-len(expected) :] if expected else ()
    return all(
        T.assignable(a, e, ctx) for a, e in zip(actual_returns, expected, strict=True)
    )


def _stack_returns(
    actual: T.TypeStack,
    expected: T.TypeStack,
) -> tuple[T.Type, ...]:
    """Determine the return types for stack during static analysis."""
    return actual.items[-len(expected) :] if expected else ()


def _return_value_shape(typ: T.Type) -> T.Type:
    """Return the underlying value shape checked inside a function body.

    Top-level return tags are guarantees made by the function signature. The
    compiler applies those tags to returned runtime values, so body checking
    must validate the underlying value rather than require the body to apply
    the same tags explicitly. Nested tags remain part of the value shape.
    """
    normalized = T.normalize(typ)
    if isinstance(normalized, T.TaggedType):
        return normalized.inner
    return normalized


def _lookup(
    items: tuple[tuple[Symbol, T.Type], ...],
    name: Symbol,
) -> T.Type | None:
    """Compute lookup during static analysis."""
    for key, typ in items:
        if key == name:
            return typ
    return None


def _assignment_error(
    name: Symbol,
    source: T.Type,
    target: T.Type,
    ctx: T.Context,
) -> str | None:
    """Return the error description for assignment during static analysis."""
    if _assignment_stored_type(target, source, ctx) is not None:
        return None
    return (
        f"cannot assign {T.show(source)} to variable '{name}' of type {T.show(target)}"
    )


def _assignment_stored_type(
    existing: T.Type,
    source: T.Type,
    ctx: T.Context,
) -> T.Type | None:
    """Determine the type of assignment stored during static analysis."""
    if T.assignable(source, existing, ctx):
        return existing
    if T.assignable(existing, source, ctx):
        return source
    return None


def _mustcall_methods(annotations: tuple[ASTNode, ...]) -> tuple[str, ...]:
    """Compute mustcall methods during static analysis."""
    for annotation in annotations:
        if not isinstance(annotation, AnnotationNode):
            continue
        if annotation.name.text != "mustcall":
            continue
        kwargs = dict(annotation.kwargs)
        for key in (Symbol("all"), Symbol("any")):
            value = kwargs.get(key)
            if not isinstance(value, ListLiteralNode):
                continue
            methods: list[str] = []
            for item in value.items:
                if len(item) != 1 or not isinstance(item[0], StringLiteralNode):
                    return ()
                methods.append(item[0].value)
            return tuple(methods)
    return ()


def _child_symbol(parent: Symbol, child: Symbol) -> Symbol:
    """Compute child symbol during static analysis."""
    return Symbol(child.text, (*parent.namespace, parent.text, *child.namespace))


def _set_item(
    items: tuple[tuple[Symbol, T.Type], ...],
    name: Symbol,
    typ: T.Type,
) -> tuple[tuple[Symbol, T.Type], ...]:
    """Compute set item during static analysis."""
    result = {key: value for key, value in items}
    result[name] = typ
    return _sorted_items(result.items())


def _set_symbol_flag(
    items: tuple[Symbol, ...],
    name: Symbol,
    enabled: bool,
) -> tuple[Symbol, ...]:
    """Compute set symbol flag during static analysis."""
    result = set(items)
    if enabled:
        result.add(name)
    else:
        result.discard(name)
    return tuple(sorted(result))


def _sorted_items(
    items: Iterable[tuple[Symbol, T.Type]],
) -> tuple[tuple[Symbol, T.Type], ...]:
    """Collect the items for sorted during static analysis."""
    return tuple(sorted(items, key=lambda item: item[0]))
