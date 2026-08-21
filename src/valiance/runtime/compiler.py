"""AST to bytecode compiler."""

from __future__ import annotations

import re

from dataclasses import dataclass, fields, is_dataclass
from typing import TYPE_CHECKING, Iterator, NoReturn

import valiance.analysis.contracts.where_clauses as static_where
from valiance.elements.builtins import BUILTIN_ELEMENTS, runtime_elements
from valiance.asts import (
    AnnotationNode,
    ArrayLiteralNode,
    AssertNode,
    ASTNode,
    AtNode,
    BindingPatternNode,
    BreakNode,
    CastNode,
    DefineNode,
    DictLiteralNode,
    ElementNode,
    ElementTagDeclarationNode,
    ExpressionPatternNode,
    ExtractPatternNode,
    FieldAccessNode,
    FieldSetNode,
    ForNode,
    FunctionNode,
    FunctionOverloadTyping,
    FunctionParam,
    GetVariableNode,
    GuardPatternNode,
    IfNode,
    ImportNode,
    IndexAccessNode,
    IndexSelector,
    IndexSetNode,
    ListLiteralNode,
    ListPatternNode,
    LiteralPatternNode,
    MatchCaseNode,
    MatchNode,
    MinimumRankNode,
    MatchPatternNode,
    NumberLiteralNode,
    ObjectNode,
    PopNNode,
    OrPatternNode,
    RecordLiteralNode,
    RestPatternNode,
    ReturnNode,
    SetVariableNode,
    SetVariablesNode,
    StackShuffleNode,
    StringInterpolationNode,
    StringLiteralNode,
    TagApplicationNode,
    TagDeclarationNode,
    TagOverlayNode,
    TryHandlerNode,
    TryNode,
    TupleLiteralNode,
    TypedAssertNode,
    TypedChannelNode,
    TypedConcurrentNode,
    TypedSpawnNode,
    TypedWaitNode,
    TypedAtNode,
    TypedElementExtension,
    TypedElementNode,
    TypedForNode,
    TypedFunctionNode,
    TypedIfNode,
    TypedImportedFunctionNode,
    TypedImportedObjectNode,
    TypedLiteralNode,
    TypedMatchNode,
    TypedNode,
    TypedTagApplicationNode,
    TypedTryNode,
    TypedReturnNode,
    TypedUnfoldNode,
    TypedWhileNode,
    TypePatternNode,
    UnfoldNode,
    WhileNode,
    WildcardPatternNode,
    is_catch_all_match_case,
)
from valiance.asts.object_constructors import (
    constructor_definitions,
    prepare_constructor_body,
)
from valiance.runtime.bytecode import (
    ExtensionRuleReference,
    FunctionCode,
    FunctionSetCode,
    IndexOperationSpec,
    IndexSelectorSpec,
    Instruction,
    ObjectConstructorReference,
    OpCode,
    Program,
    ResolvedElementReference,
    VectorExtensionReference,
)
from valiance.runtime.runtime_values import RuntimeNumber
from valiance.vtypes.symbols import Symbol
from valiance.vtypes import (
    AppliedOverload,
    ArrayExactType,
    ArrayMinType,
    ExactType,
    CollectionType,
    DataTag,
    NoVecType,
    FunctionType,
    IntersectionType,
    ListExactType,
    ListMinType,
    ListRuggedType,
    NominalType,
    NoneTypeNode,
    Overload,
    RankVariable,
    TaggedType,
    TupleType,
    Type,
    UnionType,
    VariadicTupleType,
    VarType,
    RowType,
    AnonymousTraitType,
    normalize,
    show,
)

if TYPE_CHECKING:
    from valiance.runtime.optimizer import OptimizationPipeline


class CompileError(Exception):
    """Raised when AST nodes cannot yet be lowered to bytecode."""


@dataclass(slots=True)
class _LoopPatch:
    break_jumps: list[int]


class _Compiler:
    def __init__(
        self,
        *,
        break_as_signal: bool = False,
        return_as_signal: bool = False,
        break_result_count: int | None = None,
    ) -> None:
        """Initialize this compiler."""
        self.instructions: list[Instruction] = []
        self.loops: list[_LoopPatch] = []
        self.break_as_signal = break_as_signal
        self.return_as_signal = return_as_signal
        self.break_result_count = break_result_count
        self.object_runtime_metadata: dict[str, tuple[object, ...]] = {}
        self.runtime_type_facts: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
        self.runtime_supertype_templates: dict[
            str, tuple[tuple[str, tuple[str, ...]], ...]
        ] = {}
        self.tag_disjoints: dict[str, set[str]] = {}
        self.tag_parents: dict[str, str] = {}
        self._temporary_index = 0
        self._borrowed_assignment_nodes: set[int] = set()

    def prepare_runtime_type_facts(
        self,
        body: tuple[ASTNode | TypedNode, ...],
    ) -> None:
        """Collect nominal runtime subtype and variance facts before lowering.

        These facts are attached to constructors rather than inferred from
        diagnostic strings at runtime.  A future optimiser can therefore use
        the same closed-world facts without changing bytecode semantics.
        """
        declarations: dict[str, tuple[str, ...]] = {}
        implementations: dict[str, set[str]] = {}
        projections: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
        for typed in body:
            node = _unwrap(typed)
            if not isinstance(node, ObjectNode):
                continue
            name = _symbol_runtime_name(node.name)
            if node.kind.text == "trait":
                declarations.setdefault(name, _runtime_generic_variances(node))
                if node.target is not None:
                    projection = _runtime_supertype_template(
                        node.target,
                        node.generics,
                    )
                    if projection is not None:
                        target, _ = projection
                        implementations.setdefault(name, set()).add(target)
                        projections.setdefault(name, []).append(projection)
                continue
            if node.kind.text == "object":
                if node.target is None:
                    declarations[name] = _runtime_generic_variances(node)
                else:
                    projection = _runtime_supertype_template(
                        node.target,
                        node.generics,
                    )
                    if projection is not None:
                        target, _ = projection
                        implementations.setdefault(name, set()).add(target)
                        projections.setdefault(name, []).append(projection)
                continue
            if node.kind.text == "variant":
                declarations.setdefault(name, _runtime_generic_variances(node))
                for member in node.variants:
                    member_name = f"{name}.{_symbol_runtime_name(member.name)}"
                    declarations[member_name] = _runtime_generic_variances(node)
                    implementations.setdefault(member_name, set()).add(name)

        names = set(declarations) | set(implementations)
        for targets in implementations.values():
            names.update(targets)
        facts: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
        for name in names:
            accepted = {name}
            pending = list(implementations.get(name, ()))
            while pending:
                target = pending.pop()
                if target in accepted:
                    continue
                accepted.add(target)
                pending.extend(implementations.get(target, ()))
            facts[name] = (
                tuple(sorted(accepted)),
                declarations.get(name, ()),
            )
        self.runtime_type_facts = facts

        expanded: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {}

        def expand(
            name: str,
            visiting: frozenset[str] = frozenset(),
        ) -> tuple[tuple[str, tuple[str, ...]], ...]:
            """Compose direct generic supertype projections transitively."""
            if name in expanded:
                return expanded[name]
            if name in visiting:
                return ()
            items: list[tuple[str, tuple[str, ...]]] = []
            for target, arguments in projections.get(name, ()):
                candidate = (target, arguments)
                if candidate not in items:
                    items.append(candidate)
                for ancestor, ancestor_arguments in expand(
                    target,
                    visiting | {name},
                ):
                    composed = (
                        ancestor,
                        tuple(
                            _substitute_runtime_type_template(
                                template,
                                arguments,
                            )
                            for template in ancestor_arguments
                        ),
                    )
                    if composed not in items:
                        items.append(composed)
            result = tuple(items)
            expanded[name] = result
            return result

        self.runtime_supertype_templates = {name: expand(name) for name in names}

    def _runtime_metadata(
        self,
        name: str,
        lifecycle: tuple[object, ...],
    ) -> tuple[object, ...]:
        """Attach closed-world nominal facts to one constructor."""
        accepted, variances = self.runtime_type_facts.get(name, ((name,), ()))
        registry = tuple(
            (fact_name, fact[0], fact[1])
            for fact_name, fact in sorted(self.runtime_type_facts.items())
        )
        return (
            *lifecycle,
            accepted,
            variances,
            registry,
            self.runtime_supertype_templates.get(name, ()),
        )

    def compile_function(
        self,
        body: tuple[ASTNode | TypedNode, ...],
        *,
        params: tuple[str, ...] = (),
        name: str | None = None,
        cycle_params: bool = False,
        cycle_param_offset: int = 0,
        accepts_stack_inputs: bool = False,
        element_tags: tuple[str, ...] = (),
        recursive: bool = False,
        multi: bool = False,
        dispatch_types: tuple[str | None, ...] = (),
        return_count: int | None = None,
        return_tags: tuple[tuple[DataTag, ...], ...] = (),
        return_tag_specs: tuple[object, ...] = (),
        return_collection_ranks: tuple[int | None, ...] = (),
        param_collection_ranks: tuple[int | None, ...] = (),
    ) -> FunctionCode:
        """Compile a typed function body and its captured runtime metadata."""
        self._borrowed_assignment_nodes = _borrowed_assignment_receivers(body)
        for index, node in enumerate(body):
            self.node(node)
            ast = _unwrap(node)
            if (
                index + 1 < len(body)
                and isinstance(ast, ForNode)
                and isinstance(node, TypedNode)
                and isinstance(node.typ, NoneTypeNode)
            ):
                self.emit(OpCode.POP)
        self.emit(OpCode.RETURN)
        return FunctionCode(
            instructions=tuple(self.instructions),
            params=params,
            name=name,
            cycle_params=cycle_params,
            cycle_param_offset=cycle_param_offset,
            accepts_stack_inputs=accepts_stack_inputs,
            element_tags=element_tags,
            recursive=recursive,
            multi=multi,
            dispatch_types=dispatch_types,
            return_count=return_count,
            return_tags=return_tags,
            return_tag_specs=return_tag_specs,
            return_collection_ranks=return_collection_ranks,
            param_collection_ranks=param_collection_ranks,
        )

    def node(self, node: ASTNode | TypedNode) -> None:
        """Lower one typed AST node into bytecode instructions."""
        source_node = node
        typed_node = node if isinstance(node, TypedNode) else None
        if isinstance(typed_node, TypedChannelNode):
            operations = {
                "new": OpCode.CHANNEL_NEW,
                "send": OpCode.CHANNEL_SEND,
                "receive": OpCode.CHANNEL_RECEIVE,
                "close": OpCode.CHANNEL_CLOSE,
            }
            self.emit(
                operations[typed_node.operation],
                (typed_node.has_capacity, _source_site(typed_node.node))
                if typed_node.operation == "new"
                else _source_site(typed_node.node),
            )
            return
        if isinstance(typed_node, TypedConcurrentNode):
            self.emit(
                OpCode.SCOPE_BEGIN,
                (
                    len(typed_node.input_stack), len(typed_node.output_stack),
                    _source_site(typed_node.node),
                ),
            )
            for child in typed_node.body:
                self.node(child)
            self.emit(
                OpCode.SCOPE_END,
                (
                    len(typed_node.input_stack), len(typed_node.output_stack),
                    _source_site(typed_node.node),
                ),
            )
            return
        if isinstance(typed_node, TypedSpawnNode):
            if typed_node.callable_node is not None:
                self.node(typed_node.callable_node)
            self.emit(
                OpCode.SPAWN_CALL,
                (
                    len(typed_node.input_types),
                    len(typed_node.output_types),
                    typed_node.overload_index,
                    typed_node.unique_inputs,
                    typed_node.vectorised,
                    typed_node.vectorised_depths,
                    typed_node.vectorised_target_ranks,
                    typed_node.runtime_static_values,
                    _source_site(typed_node.node),
                ),
            )
            return
        if isinstance(typed_node, TypedWaitNode):
            self.emit(
                OpCode.WAIT_TASKS_VECTORISED
                if typed_node.vectorised
                else OpCode.WAIT_TASK,
                (len(typed_node.output_types), _source_site(typed_node.node)),
            )
            return
        node = _unwrap(node)
        match node:
            case NumberLiteralNode(value):
                self.emit(OpCode.PUSH_CONST, _number(value, node))
            case StringLiteralNode(value):
                self.emit(OpCode.PUSH_CONST, value)
            case StringInterpolationNode(parts):
                self.string_interpolation(parts)
            case GetVariableNode(name):
                opcode = (
                    OpCode.LOAD_VAR_BORROW
                    if id(source_node) in self._borrowed_assignment_nodes
                    else OpCode.LOAD_VAR
                )
                self.emit(opcode, _symbol_runtime_name(name))
                if (
                    isinstance(typed_node, TypedFunctionNode)
                    and typed_node.dispatch_plan is not None
                ):
                    self.emit(
                        OpCode.APPLY_DISPATCH_PLAN,
                        FunctionSetCode((), typed_node.dispatch_plan.branches),
                    )
            case SetVariableNode(name):
                self.emit(OpCode.STORE_VAR, _symbol_runtime_name(name))
            case SetVariablesNode(targets):
                for target in reversed(targets):
                    self.emit(OpCode.STORE_VAR, _symbol_runtime_name(target.name))
            case ElementNode(name, modifier_args):
                runtime_name = (
                    typed_node.runtime_name
                    if isinstance(typed_node, TypedElementNode)
                    and typed_node.runtime_name is not None
                    else name
                )
                if typed_node is None and node.call_args:
                    for arg in node.call_args:
                        if arg.placeholder or arg.name is not None:
                            raise CompileError(
                                "named or placeholder element call arguments require "
                                "resolved typed compilation"
                            )
                        self.expression(arg.value)
                if (
                    isinstance(typed_node, TypedElementNode)
                    and name.text != "call"
                    and typed_node.call_arg_order
                ):
                    labels = tuple(
                        f"_element_arg_{index}"
                        for index in range(len(typed_node.call_arg_order))
                    )
                    self.emit(
                        OpCode.STACK_SHUFFLE,
                        (
                            "move",
                            labels,
                            tuple(labels[index] for index in typed_node.call_arg_order),
                        ),
                    )
                args: tuple[ASTNode | TypedNode, ...] = modifier_args
                if (
                    isinstance(typed_node, TypedElementNode)
                    and typed_node.modifier_args
                ):
                    args = typed_node.modifier_args
                for arg in args:
                    self.node(arg)
                if (
                    isinstance(typed_node, TypedElementNode)
                    and typed_node.overload_index is not None
                    and name.text == "?"
                ):
                    self.emit(OpCode.TRY_UNWRAP)
                    return
                if (
                    isinstance(typed_node, TypedElementNode)
                    and name.text == "call"
                    and typed_node.call_arg_order
                ):
                    labels = tuple(
                        f"_call_arg_{index}"
                        for index in range(len(typed_node.call_arg_order))
                    )
                    self.emit(
                        OpCode.STACK_SHUFFLE,
                        (
                            "move",
                            labels,
                            tuple(labels[index] for index in typed_node.call_arg_order),
                        ),
                    )
                resolved = _resolved_element_reference(typed_node)
                if resolved is None:
                    self.emit(
                        OpCode.LOAD_ELEMENT,
                        _symbol_runtime_name(runtime_name),
                    )
                    return_tag_specs = (
                        _call_site_return_tag_specs(typed_node.overload)
                        if isinstance(typed_node, TypedElementNode)
                        and typed_node.overload is not None
                        else ()
                    )
                    self.emit(OpCode.CALL, return_tag_specs or None)
                else:
                    self.emit(OpCode.CALL_RESOLVED_ELEMENT, resolved)
                if (
                    isinstance(typed_node, TypedElementNode)
                    and typed_node.overload is not None
                    and any(
                        isinstance(annotation, AnnotationNode)
                        and annotation.name.text == "@@tupled"
                        for annotation in node.annotations
                    )
                ):
                    self.emit(
                        OpCode.BUILD_TUPLE,
                        len(typed_node.overload.actual_returns),
                    )
            case TagApplicationNode():
                # Explicit function parameters are present on the analyser's
                # conceptual stack, but runtime parameter values are sourced
                # lazily from the cycle. Re-source the top value so direct tag
                # applications behave like ordinary one-argument elements.
                self.emit(OpCode.SOURCE_ARGS, 1)
                if isinstance(typed_node, TypedTagApplicationNode):
                    validator_index = typed_node.validator_index
                    added_tags = typed_node.added_tags
                    removed_tags = typed_node.removed_tags
                    validator_runtime_name = typed_node.validator_runtime_name
                    validator_plans = typed_node.validator_plans
                elif node.tag.absent:
                    validator_index = None
                    added_tags = ()
                    removed_names = {node.tag.name}
                    pending = [node.tag.name]
                    while pending:
                        parent = pending.pop()
                        for variant, variant_parent in self.tag_parents.items():
                            if (
                                variant_parent == parent
                                and variant not in removed_names
                            ):
                                removed_names.add(variant)
                                pending.append(variant)
                    removed_tags = tuple(
                        DataTag(name, node.tag.depth) for name in sorted(removed_names)
                    )
                    validator_runtime_name = None
                    validator_plans = ()
                else:
                    validator_index = None
                    validator_runtime_name = None
                    validator_plans = ()
                    added = [DataTag(node.tag.name, node.tag.depth)]
                    parent = self.tag_parents.get(node.tag.name)
                    if parent is not None:
                        added.append(DataTag(parent, node.tag.depth))
                    added_tags = tuple(added)
                    disjoint_names = {
                        name
                        for tag in added_tags
                        for name in self.tag_disjoints.get(tag.name, ())
                    }
                    pending = list(disjoint_names)
                    while pending:
                        parent = pending.pop()
                        for variant, variant_parent in self.tag_parents.items():
                            if (
                                variant_parent == parent
                                and variant not in disjoint_names
                            ):
                                disjoint_names.add(variant)
                                pending.append(variant)
                    removed_tags = tuple(
                        DataTag(name, node.tag.depth) for name in sorted(disjoint_names)
                    )
                if validator_plans:
                    for index, (runtime_name, overload_index) in enumerate(
                        validator_plans
                    ):
                        final = index + 1 == len(validator_plans)
                        self.emit(
                            OpCode.VALIDATE_TAG,
                            (
                                _symbol_runtime_name(runtime_name),
                                overload_index,
                                (
                                    tuple((tag.name, tag.depth) for tag in added_tags)
                                    if final
                                    else ()
                                ),
                                (
                                    tuple((tag.name, tag.depth) for tag in removed_tags)
                                    if final
                                    else ()
                                ),
                            ),
                        )
                else:
                    self.emit(
                        OpCode.VALIDATE_TAG,
                        (
                            (
                                f"#{node.tag.name}"
                                if validator_runtime_name is None
                                else _symbol_runtime_name(validator_runtime_name)
                            ),
                            validator_index,
                            tuple((tag.name, tag.depth) for tag in added_tags),
                            tuple((tag.name, tag.depth) for tag in removed_tags),
                        ),
                    )
            case TagDeclarationNode():
                self._register_runtime_tag_declaration(node)
            case ElementTagDeclarationNode() | TagOverlayNode():
                pass
            case CastNode(typ, checked, optional):
                if optional:
                    self.emit(OpCode.TRY_CAST, (_cast_type_spec(typ), _runtime_tag_contract_spec(typ)))
                    return
                if checked:
                    self.emit(OpCode.CHECK_CAST, _cast_type_spec(typ))
                contract_type = typed_node.typ if isinstance(typed_node, TypedNode) and typed_node.typ is not None else typ
                self.emit(OpCode.CANONICALIZE_TAGS, _runtime_tag_contract_spec(contract_type))
            case MinimumRankNode(rank):
                self.emit(OpCode.SOURCE_ARGS, 1)
                self.emit(OpCode.ENSURE_MIN_RANK, rank)
            case PopNNode(count):
                operand: object = (
                    count if isinstance(count, int) else ("static", count.text)
                )
                if operand != 0:
                    self.emit(OpCode.SOURCE_ARGS, operand)
                    self.emit(OpCode.POP_N, operand)
            case StackShuffleNode(mode, prestack, poststack):
                self.emit(
                    OpCode.STACK_SHUFFLE,
                    (
                        mode.text,
                        tuple(None if item is None else item.text for item in prestack),
                        tuple(item.text for item in poststack),
                    ),
                )
            case FunctionNode():
                self.emit(
                    OpCode.MAKE_FUNCTION,
                    _compile_function_value(typed_node or node),
                )
            case DefineNode(name, function):
                runtime_name = (
                    typed_node.runtime_name
                    if isinstance(typed_node, TypedImportedFunctionNode)
                    and typed_node.runtime_name is not None
                    else name
                )
                self.emit(
                    OpCode.MAKE_FUNCTION,
                    _compile_function_value(
                        typed_node or function,
                        _symbol_runtime_name(runtime_name),
                    ),
                )
                self.emit(OpCode.STORE_VAR, _symbol_runtime_name(runtime_name))
            case ImportNode():
                pass
            case ListLiteralNode(items) | ArrayLiteralNode(items):
                compiled_items = (
                    typed_node.items
                    if isinstance(typed_node, TypedLiteralNode)
                    else items
                )
                rank = (
                    _runtime_collection_rank(typed_node.typ)
                    if isinstance(typed_node, TypedLiteralNode)
                    else None
                )
                self.collection(
                    compiled_items,
                    OpCode.BUILD_LIST,
                    argument=(len(compiled_items), rank),
                )
            case TupleLiteralNode(items):
                compiled_items = (
                    typed_node.items
                    if isinstance(typed_node, TypedLiteralNode)
                    else items
                )
                self.collection(compiled_items, OpCode.BUILD_TUPLE)
            case RecordLiteralNode(fields):
                typed_items = typed_node.items if isinstance(typed_node, TypedLiteralNode) else ()
                keys = []
                for index, (key, expr) in enumerate(fields):
                    self.emit(OpCode.ISOLATE_STACK_BEGIN)
                    self.expression(typed_items[index] if typed_items else expr)
                    self.emit(OpCode.ISOLATE_STACK_END)
                    keys.append(key.text)
                self.emit(OpCode.BUILD_RECORD, tuple(keys))
            case DictLiteralNode(entries):
                typed_items = typed_node.items if isinstance(typed_node, TypedLiteralNode) else ()
                expressions = tuple(expr for entry in entries for expr in entry)
                for index, expr in enumerate(expressions):
                    self.emit(OpCode.ISOLATE_STACK_BEGIN)
                    self.expression(typed_items[index] if typed_items else expr)
                    self.emit(OpCode.ISOLATE_STACK_END)
                self.emit(OpCode.BUILD_DICT, len(entries))
            case ObjectNode():
                runtime_name = (
                    typed_node.runtime_name
                    if isinstance(typed_node, TypedImportedObjectNode)
                    and typed_node.runtime_name is not None
                    else None
                )
                self.object_declaration(node, runtime_name=runtime_name)
            case FieldAccessNode(name):
                argument = (name.text, "optional") if node.optional_safe else name.text
                self.emit(OpCode.GET_FIELD, argument)
            case FieldSetNode(name):
                argument = (name.text, "optional") if node.optional_safe else name.text
                self.emit(OpCode.SET_FIELD, argument)
            case IndexAccessNode(selectors, spread):
                self.emit(
                    OpCode.GET_INDEX,
                    _index_spec(selectors, spread, node.grouped_update),
                )
            case IndexSetNode(selectors):
                self.emit(
                    OpCode.SET_INDEX,
                    _index_spec(selectors, False, node.grouped_update),
                )
            case IfNode():
                self.if_node(node, typed_node)
            case AssertNode():
                self.assert_node(node, typed_node)
            case MatchNode():
                self.match_node(node, typed_node)
            case TryNode():
                self.try_node(node, typed_node)
            case WhileNode():
                self.while_node(node, typed_node)
            case UnfoldNode():
                self.unfold_node(node, typed_node)
            case AtNode():
                self.at_node(node, typed_node)
            case ForNode():
                self.foreach_node(node, typed_node)
            case BreakNode():
                self.break_node(node, values_already_compiled=typed_node is not None)
            case ReturnNode():
                expressions = (
                    typed_node.expressions
                    if isinstance(typed_node, TypedReturnNode)
                    else node.values
                )
                if node.explicit_values:
                    temporaries: list[str] = []
                    for expression in expressions:
                        name = f"\x00return_{self._temporary_index}"
                        self._temporary_index += 1
                        self.emit(OpCode.CYCLE_BEGIN, ("current", 0))
                        self.expression(expression)
                        self.emit(OpCode.CYCLE_END)
                        self.emit(OpCode.STORE_VAR, name)
                        temporaries.append(name)
                    for name in temporaries:
                        self.emit(OpCode.LOAD_VAR, name)
                    count: int | None = len(expressions)
                elif expressions:
                    self.expression(expressions[0])
                    count = 1
                else:
                    count = None
                self.emit(
                    OpCode.RETURN_SIGNAL if self.return_as_signal else OpCode.RETURN,
                    count,
                )
            case _:
                self.unsupported(node, type(node).__name__)

    def expression(self, nodes: tuple[ASTNode | TypedNode, ...]) -> None:
        """Lower an expression body with assignment-receiver borrow metadata."""
        previous = self._borrowed_assignment_nodes
        self._borrowed_assignment_nodes = previous | _borrowed_assignment_receivers(
            nodes
        )
        try:
            for node in nodes:
                self.node(node)
        finally:
            self._borrowed_assignment_nodes = previous

    def collection(
        self,
        items: tuple[tuple[ASTNode | TypedNode, ...], ...],
        op: OpCode,
        *,
        argument: object | None = None,
    ) -> None:
        """Compile collection items as isolated expressions."""
        temporaries: list[str] = []
        for item in items:
            name = f"\x00literal_{self._temporary_index}"
            self._temporary_index += 1
            self.emit(OpCode.CYCLE_BEGIN, ("current", 0))
            self.expression(item)
            self.emit(OpCode.CYCLE_END)
            self.emit(OpCode.STORE_VAR, name)
            temporaries.append(name)
        for name in temporaries:
            self.emit(OpCode.LOAD_VAR, name)
        self.emit(op, len(items) if argument is None else argument)

    def _register_runtime_tag_declaration(self, node: TagDeclarationNode) -> None:
        """Lower register runtime tag declaration into bytecode instructions."""
        name = node.tag.name
        if node.parent is not None:
            self.tag_parents[name] = node.parent.name
        if node.disjoint is not None:
            other = node.disjoint.name
            self.tag_disjoints.setdefault(name, set()).add(other)
            self.tag_disjoints.setdefault(other, set()).add(name)

    def string_interpolation(
        self,
        parts: tuple[str | tuple[ASTNode, ...], ...],
    ) -> None:
        """Compile interpolation parts and concatenate their runtime strings."""
        template: list[str | None] = []
        for part in parts:
            if isinstance(part, str):
                template.append(part)
                continue
            self.expression(part)
            template.append(None)
        self.emit(OpCode.BUILD_STRING, tuple(template))

    def object_declaration(
        self,
        node: ObjectNode,
        *,
        runtime_name: Symbol | None = None,
    ) -> None:
        """Compile an object-like declaration and publish its runtime values."""
        match node.kind.text:
            case "object":
                type_name = _symbol_runtime_name(node.name)
                binding_name = (
                    type_name
                    if runtime_name is None
                    else _symbol_runtime_name(runtime_name)
                )
                constructors = constructor_definitions(node.name, node.definitions)
                if node.target is None:
                    self.object_runtime_metadata[type_name] = self._runtime_metadata(
                        type_name,
                        _object_runtime_metadata(
                            type_name,
                            node.annotations,
                            node.definitions,
                        ),
                    )
                    self.object_constructor(
                        type_name,
                        node.fields,
                        store_name=binding_name,
                        alias=(
                            type_name
                            if runtime_name is None and not node.name.namespace
                            else None
                        ),
                        initializers=constructors,
                    )
                for definition in node.definitions:
                    if definition not in constructors:
                        self.friendly_definition(type_name, definition)
            case "trait":
                runtime_name = _symbol_runtime_name(node.name)
                for definition in node.definitions:
                    self.friendly_definition(runtime_name, definition)
            case "variant":
                required_names = {requirement.name for requirement in node.requirements}
                for member in node.variants:
                    runtime_name = (
                        f"{_symbol_runtime_name(node.name)}."
                        f"{_symbol_runtime_name(member.name)}"
                    )
                    self.object_runtime_metadata[runtime_name] = self._runtime_metadata(
                        runtime_name,
                        _object_runtime_metadata(
                            runtime_name,
                            node.annotations,
                            member.definitions,
                        ),
                    )
                    self.object_constructor(
                        runtime_name,
                        member.fields,
                        alias=member.name.text,
                    )
                    for definition in member.definitions:
                        self.friendly_definition(
                            runtime_name,
                            definition,
                            variant_dispatch=definition.name in required_names,
                        )
            case "enum":
                enum_name = _symbol_runtime_name(node.name)
                for member in node.enum_members:
                    member_name = _symbol_runtime_name(member.name)
                    runtime_name = f"{enum_name}.{member_name}"
                    value = None
                    if member.value:
                        value = _literal_expression_value(member.value)
                    self.emit(
                        OpCode.MAKE_ENUM_MEMBER,
                        (enum_name, member_name, value),
                    )
                    self.emit(OpCode.STORE_VAR, runtime_name)
                    if value is not None:
                        self.emit(OpCode.PUSH_CONST, value)
                        self.emit(
                            OpCode.STORE_VAR,
                            f"{runtime_name}.value",
                        )

    def object_constructor(
        self,
        name: str,
        fields: object,
        *,
        store_name: str | None = None,
        alias: str | None = None,
        initializers: tuple[DefineNode, ...] = (),
    ) -> None:
        """Build constructor bytecode and lifecycle metadata for an object."""
        field_names: list[str] = []
        default_values: list[tuple[str, object]] = []
        for field in fields:
            field_names.append(field.name.text)
            if field.default:
                default_values.append(
                    (field.name.text, _literal_expression_value(field.default))
                )
        # Synthesized constructors receive every member value after analysis has
        # inserted omitted defaults and ordered named ECS arguments. Explicit
        # constructors retain their declared initializer arity below.
        required = (
            tuple(field_names)
            if not initializers
            else tuple(
                field_name
                for field_name in field_names
                if field_name not in {name for name, _ in default_values}
            )
        )
        initializer_code: FunctionCode | FunctionSetCode | None = None
        if initializers:
            compiled_initializers = tuple(
                _compile_object_initializer(name, definition)
                for definition in initializers
            )
            initializer_code = (
                compiled_initializers[0]
                if len(compiled_initializers) == 1
                else FunctionSetCode(compiled_initializers)
            )
        self.emit(
            OpCode.MAKE_OBJECT_CONSTRUCTOR,
            ObjectConstructorReference(
                type_name=name,
                fields=tuple(field_names),
                required=required,
                defaults=tuple(default_values),
                runtime_metadata=self.object_runtime_metadata.get(
                    name,
                    self._runtime_metadata(
                        name,
                        (None, None, None, None, None, ()),
                    ),
                ),
                initializer=initializer_code,
            ),
        )
        binding_name = name if store_name is None else store_name
        self.emit(OpCode.STORE_VAR, binding_name)
        if alias is not None and alias != binding_name:
            self.emit(OpCode.LOAD_ELEMENT, binding_name)
            self.emit(OpCode.STORE_VAR, alias)

    def friendly_definition(
        self,
        owner: str,
        definition: DefineNode,
        *,
        variant_dispatch: bool = False,
    ) -> None:
        """Compile and store an object-friendly element implementation."""
        body = definition.function.body
        if any(
            isinstance(annotation, AnnotationNode)
            and annotation.name.text == "self"
            for annotation in definition.annotations
        ):
            body = prepare_constructor_body(body)
            body = (*body, GetVariableNode(Symbol("self")))
        function = FunctionNode(
            params=(FunctionParam(Symbol("self")),)
            + tuple(definition.function.params or ()),
            body=body,
            returns=definition.function.returns,
            where_clause=definition.function.where_clause,
            element_tags=definition.function.element_tags,
            annotations=definition.function.annotations,
            location=definition.function.location,
        )
        node = DefineNode(
            definition.name,
            function,
            definition.annotations,
            definition.is_multi,
            definition.visibility,
            location=definition.location,
        )
        runtime_definition_name = _symbol_runtime_name(definition.name)
        self.emit(
            OpCode.MAKE_FUNCTION,
            _compile_function_node(
                node,
                runtime_definition_name,
                multi=variant_dispatch,
                dispatch_types=(
                    (owner, *(None for _ in definition.function.params or ()))
                    if variant_dispatch
                    else ()
                ),
            ),
        )
        self.emit(OpCode.STORE_VAR, runtime_definition_name)
        self.emit(
            OpCode.MAKE_FUNCTION,
            _compile_function_value(node, f"{owner}::{runtime_definition_name}"),
        )
        self.emit(OpCode.STORE_VAR, f"{owner}::{runtime_definition_name}")

    def if_node(self, node: IfNode, typed_node: TypedNode | None = None) -> None:
        """Lower a conditional using analysed child nodes when available."""
        condition: tuple[ASTNode | TypedNode, ...] = node.condition
        then_branch: tuple[ASTNode | TypedNode, ...] = node.then_branch
        else_branch: tuple[ASTNode | TypedNode, ...] = node.else_branch
        if isinstance(typed_node, TypedIfNode):
            condition = typed_node.condition
            then_branch = typed_node.then_branch
            else_branch = typed_node.else_branch
        self.expression(condition)
        jump_to_else = self.emit(OpCode.JUMP_IF_FALSE, None)
        self.expression(then_branch)
        if isinstance(typed_node, TypedIfNode):
            for _ in range(typed_node.then_padding):
                self.emit(OpCode.PUSH_CONST, None)
        jump_to_end = self.emit(OpCode.JUMP, None)
        else_start = len(self.instructions)
        self.patch(jump_to_else, else_start)
        self.expression(else_branch)
        if isinstance(typed_node, TypedIfNode):
            for _ in range(typed_node.else_padding):
                self.emit(OpCode.PUSH_CONST, None)
        self.patch(jump_to_end, len(self.instructions))

    def assert_node(self, node: AssertNode, typed_node: TypedNode | None) -> None:
        """Lower an assertion and its optional failure branch."""
        condition: tuple[ASTNode | TypedNode, ...] = node.condition
        else_branch: tuple[ASTNode | TypedNode, ...] = node.else_branch
        if isinstance(typed_node, TypedAssertNode):
            condition = typed_node.condition
            else_branch = typed_node.else_branch
        self.emit(OpCode.ASSERT_PEEK_BEGIN)
        self.expression(condition)
        self.emit(OpCode.ASSERT_PEEK_END)
        if not else_branch:
            self.emit(OpCode.ASSERT_TRUE)
            return
        jump_to_else = self.emit(OpCode.JUMP_IF_FALSE, None)
        if isinstance(typed_node, TypedAssertNode) and typed_node.top_level_result:
            # None is the canonical raw success value for
            # Result[None, AssertError[E]].
            self.emit(OpCode.PUSH_CONST, None)
        jump_to_end = self.emit(OpCode.JUMP, None)
        else_start = len(self.instructions)
        self.patch(jump_to_else, else_start)
        self.expression(else_branch)
        self.emit(OpCode.WRAP_ASSERT_ERROR)
        self.patch(jump_to_end, len(self.instructions))

    def unfold_node(self, node: UnfoldNode, typed_node: TypedNode | None) -> None:
        """Lower an unfold operation through its analysed callable body."""
        arity = _unfold_state_arity(node, typed_node)
        if isinstance(typed_node, TypedUnfoldNode) and typed_node.function is not None:
            body_code = _compile_function_value(typed_node.function, "unfold.body")
        else:
            body = FunctionNode(
                params=node.params or tuple(FunctionParam(None) for _ in range(arity)),
                body=node.body,
                location=node.location,
            )
            body_code = _compile_function_value(body, "unfold.body")
        condition_code = None
        if node.condition:
            params = node.params
            if params is None:
                params = tuple(FunctionParam(None) for _ in range(arity))
            condition = FunctionNode(
                params=params,
                body=node.condition,
                location=node.location,
            )
            condition_code = _compile_function_value(condition, "unfold.condition")
        self.emit(OpCode.UNFOLD, (condition_code, body_code, arity))

    def at_node(self, node: AtNode, typed_node: TypedNode | None) -> None:
        """Lower an `at` body and its statically resolved stop ranks."""
        if not isinstance(typed_node, TypedAtNode):
            raise CompileError("at expressions require typed vectorisation metadata")
        if typed_node.function is None or typed_node.overload is None:
            raise CompileError("at expression is missing its typed body")

        body = _compile_function_value(typed_node.function, "at.body")
        self.emit(OpCode.MAKE_FUNCTION, body)
        applied = typed_node.overload
        arity = len(node.levels) + 1
        vectorised = applied.vectorised or any(
            rank is not None for rank in applied.vectorised_target_ranks
        )
        self.emit(
            OpCode.CALL_RESOLVED_ELEMENT,
            ResolvedElementReference(
                "call",
                0,
                vectorised=vectorised,
                vectorised_depths=(*applied.vectorised_depths, 0),
                vectorised_target_ranks=(
                    *applied.vectorised_target_ranks,
                    None,
                ),
                return_collection_ranks=tuple(
                    _runtime_collection_rank(ret) for ret in applied.actual_returns
                ),
                static_values=(typed_node.function_overload_index,),
                arity_override=arity,
                consumed_override=arity,
            ),
        )

    def foreach_node(self, node: ForNode, typed_node: TypedNode | None) -> None:
        """Lower a foreach loop and its break-result handling."""
        params = (FunctionParam(node.variable),)
        if node.index_variable is not None:
            params += (FunctionParam(node.index_variable),)
        body = FunctionNode(
            params=params,
            body=node.body,
            location=node.location,
        )
        if isinstance(typed_node, TypedForNode):
            body_code = _Compiler(
                break_as_signal=True,
                return_as_signal=True,
                break_result_count=None,
            ).compile_function(
                typed_node.body,
                params=tuple(
                    f"_{index}" if param.name is None else param.name.text
                    for index, param in enumerate(params)
                ),
                name="foreach.body",
                cycle_params=True,
            )
        else:
            body_code = _compile_function_node(
                body,
                "foreach.body",
                break_as_signal=True,
                return_as_signal=True,
            )
        completion_count = max(1, _max_break_values(node.body))
        self.emit(
            OpCode.FOREACH,
            (body_code, 1 if node.index_variable else 0, completion_count),
        )

    def match_node(self, node: MatchNode, typed_node: TypedNode | None) -> None:
        """Lower typed match cases, guards, bindings, and join targets."""
        typed_bodies = (
            typed_node.case_bodies if isinstance(typed_node, TypedMatchNode) else ()
        )
        typed_guards = (
            typed_node.case_guards if isinstance(typed_node, TypedMatchNode) else ()
        )
        body_by_case = {
            id(case): typed_bodies[index]
            for index, case in enumerate(node.cases)
            if index < len(typed_bodies)
        }
        pattern_arities = (
            typed_node.case_pattern_arities
            if isinstance(typed_node, TypedMatchNode)
            else tuple(tuple(1 for _ in case.patterns) for case in node.cases)
        )
        consumptions = {sum(arities) for arities in pattern_arities}
        match_arity = next(iter(consumptions)) if len(consumptions) == 1 else 0
        if match_arity:
            self.emit(OpCode.SOURCE_ARGS, match_arity)
        # Emit every pattern test in source order. Catch-all patterns are still
        # ordinary matches at runtime: hoisting them to a synthetic fallback
        # changes first-match semantics and can drop earlier guarded patterns.
        case_jumps: list[tuple[int, MatchCaseNode]] = []
        for case_index, case in enumerate(node.cases):
            guard_blocks = (
                iter(typed_guards[case_index])
                if case_index < len(typed_guards)
                else None
            )
            guard_arities = (
                iter(typed_node.case_guard_arities[case_index])
                if isinstance(typed_node, TypedMatchNode)
                and case_index < len(typed_node.case_guard_arities)
                else None
            )
            catch_all = is_catch_all_match_case(case.patterns)
            compiled_patterns = tuple(
                ("catch_all",)
                if catch_all and isinstance(pattern, WildcardPatternNode)
                else _compile_match_pattern(pattern, guard_blocks, guard_arities)
                for pattern in case.patterns
            )
            if guard_blocks is not None:
                try:
                    next(guard_blocks)
                except StopIteration:
                    pass
                else:
                    raise CompileError("typed match guard count exceeds source patterns")
            case_jumps.append(
                (
                    self.emit(
                        OpCode.JUMP_IF_MATCH,
                        (compiled_patterns, None),
                    ),
                    case,
                )
            )
        self.emit(OpCode.MATCH_ERROR)

        end_jumps: list[int] = []
        for jump, case in case_jumps:
            self.patch_match(jump, len(self.instructions))
            # JUMP_IF_MATCH has already installed the retained coordinates as
            # the conceptual input cycle. Execute the selected branch on an
            # empty physical stack, then append every value it produces to the
            # surrounding stack and restore the enclosing cycle scope.
            self.emit(OpCode.MATCH_BRANCH_BEGIN)
            self.expression(body_by_case.get(id(case), case.body))
            self.emit(OpCode.MATCH_BRANCH_END)
            end_jumps.append(self.emit(OpCode.JUMP, None))
        end = len(self.instructions)
        for jump in end_jumps:
            self.patch(jump, end)

    def try_node(self, node: TryNode, typed_node: TypedNode | None) -> None:
        """Lower panic handlers and their protected instruction range."""
        body: tuple[ASTNode | TypedNode, ...] = node.body
        handler_bodies: tuple[tuple[ASTNode | TypedNode, ...], ...] = ()
        if isinstance(typed_node, TypedTryNode):
            body = typed_node.body
            handler_bodies = typed_node.handler_bodies
        begin = self.emit(OpCode.TRY_BEGIN, ())
        self.expression(body)
        self.emit(OpCode.TRY_END)
        success_jump = self.emit(OpCode.JUMP, None)

        handlers: list[tuple[str | None, int]] = []
        end_jumps: list[int] = []
        for index, handler in enumerate(node.handlers):
            if handler.typ is not None and not isinstance(handler.typ, NominalType):
                raise CompileError(
                    f"cannot compile handler for non-nominal type {handler.typ}"
                )
            handlers.append(
                (
                    handler.typ.name.text
                    if isinstance(handler.typ, NominalType)
                    else None,
                    len(self.instructions),
                )
            )
            handler_body: tuple[ASTNode | TypedNode, ...] = handler.body
            if index < len(handler_bodies):
                handler_body = handler_bodies[index]
            self.expression(handler_body)
            end_jumps.append(self.emit(OpCode.JUMP, None))

        end = len(self.instructions)
        self.patch(success_jump, end)
        for jump in end_jumps:
            self.patch(jump, end)
        self.instructions[begin] = Instruction(OpCode.TRY_BEGIN, tuple(handlers))

    def while_node(self, node: WhileNode, typed_node: TypedNode | None) -> None:
        """Lower a while loop with condition and exit jumps."""
        condition: tuple[ASTNode | TypedNode, ...] = node.condition
        body: tuple[ASTNode | TypedNode, ...] = node.body
        if isinstance(typed_node, TypedWhileNode):
            condition = typed_node.condition
            body = typed_node.body
        if (
            node.params is None
            and isinstance(typed_node, TypedWhileNode)
            and typed_node.input_count
        ):
            params = tuple(
                f"_while_input_{index}" for index in range(typed_node.input_count)
            )
            condition_code = _Compiler().compile_function(
                condition,
                params=params,
                name="while.condition",
                cycle_params=True,
            )
            body_code = _Compiler(break_as_signal=True).compile_function(
                body,
                params=params,
                name="while.body",
                cycle_params=True,
            )
            self.emit(
                OpCode.WHILE,
                (condition_code, body_code, typed_node.input_count),
            )
            return
        if node.params is not None:
            params = tuple(
                f"_{index}" if param.name is None else param.name.text
                for index, param in enumerate(node.params)
            )
            if isinstance(typed_node, TypedWhileNode):
                condition_code = _Compiler().compile_function(
                    condition,
                    params=params,
                    name="while.condition",
                    cycle_params=True,
                )
                body_code = _Compiler(break_as_signal=True).compile_function(
                    body,
                    params=params,
                    name="while.body",
                    cycle_params=True,
                )
            else:
                condition_function = FunctionNode(
                    params=node.params,
                    body=node.condition,
                    location=node.location,
                )
                body_function = FunctionNode(
                    params=node.params,
                    body=node.body,
                    location=node.location,
                )
                condition_code = _compile_function_node(
                    condition_function,
                    "while.condition",
                )
                body_code = _compile_function_node(
                    body_function,
                    "while.body",
                    break_as_signal=True,
                )
            self.emit(OpCode.WHILE, (condition_code, body_code, len(node.params)))
            return
        loop_start = len(self.instructions)
        self.loops.append(_LoopPatch([]))
        input_count = (
            typed_node.input_count
            if isinstance(typed_node, TypedWhileNode)
            else 0
        )
        if input_count:
            labels = tuple(f"_while_input_{index}" for index in range(input_count))
            self.emit(OpCode.STACK_SHUFFLE, ("copy", labels, labels))
        self.expression(condition)
        jump_to_end = self.emit(OpCode.JUMP_IF_FALSE, None)
        self.expression(body)
        self.emit(OpCode.JUMP, loop_start)
        loop_end = len(self.instructions)
        self.patch(jump_to_end, loop_end)
        loop = self.loops.pop()
        for jump in loop.break_jumps:
            self.patch(jump, loop_end)

    def break_node(
        self,
        node: BreakNode,
        *,
        values_already_compiled: bool = False,
    ) -> None:
        """Lower one loop break without recompiling typed value expressions."""
        if self.break_as_signal:
            if not values_already_compiled:
                for value in node.values:
                    self.node(value)
            self.emit(OpCode.LOOP_BREAK, self.break_result_count)
            return
        if not self.loops:
            self.unsupported(node, "break outside a loop")
        for value in node.values:
            self.node(value)
        self.loops[-1].break_jumps.append(self.emit(OpCode.JUMP, None))

    def emit(self, op: OpCode, arg: object = None) -> int:
        """Append one bytecode instruction and return its index."""
        self.instructions.append(Instruction(op, arg))
        return len(self.instructions) - 1

    def patch(self, index: int, target: int) -> None:
        """Replace one instruction argument after its target becomes known."""
        instruction = self.instructions[index]
        self.instructions[index] = Instruction(instruction.op, target)

    def patch_match(self, index: int, target: int) -> None:
        """Patch a match instruction while retaining its compiled pattern."""
        instruction = self.instructions[index]
        pattern, _ = instruction.arg
        self.instructions[index] = Instruction(instruction.op, (pattern, target))

    def unsupported(self, node: ASTNode, feature: str) -> NoReturn:
        """Raise a compiler error for an unexpected typed AST node."""
        location = ""
        if node.location is not None:
            location = f" at {node.location.line}:{node.location.column}"
        raise CompileError(f"cannot compile {feature}{location}")


def compile_program(
    nodes: list[TypedNode],
    *,
    optimize: bool = True,
    optimization_pipeline: OptimizationPipeline | None = None,
) -> Program:
    """Compile analysed typed AST nodes, optimising bytecode by default."""
    if not all(isinstance(node, TypedNode) for node in nodes):
        raise CompileError("compile_program expects analysed TypedNode values")
    compiler = _Compiler()
    compiler.prepare_runtime_type_facts(tuple(nodes))
    main = compiler.compile_function(tuple(nodes), name="<main>")
    program = Program(main, tuple(sorted(compiler.tag_parents.items())))
    if not optimize:
        return program

    from valiance.runtime.optimizer import (
        DEFAULT_OPTIMIZATION_PIPELINE,
        optimize_program,
    )

    pipeline = (
        DEFAULT_OPTIMIZATION_PIPELINE
        if optimization_pipeline is None
        else optimization_pipeline
    )
    return optimize_program(program, pipeline=pipeline)


def _compile_object_initializer(name: str, definition: DefineNode) -> FunctionCode:
    """Compile object initializer during typed-AST bytecode lowering."""
    body = prepare_constructor_body(definition.function.body)
    function = FunctionNode(
        params=(FunctionParam(Symbol("self")),)
        + tuple(definition.function.params or ()),
        body=(*body, GetVariableNode(Symbol("self"), location=definition.location)),
        returns=definition.function.returns,
        where_clause=definition.function.where_clause,
        element_tags=definition.function.element_tags,
        annotations=definition.function.annotations,
        location=definition.function.location,
    )
    compiled = _compile_function_value(function, f"{name}.constructor")
    if not isinstance(compiled, FunctionCode):
        raise CompileError(f"constructor '{name}' compiled to an overload set")
    return compiled


def _compile_function_value(
    node: FunctionNode | TypedNode,
    name: str | None = None,
) -> FunctionCode | FunctionSetCode:
    """Compile function value during typed-AST bytecode lowering."""
    typed = node if isinstance(node, TypedFunctionNode) else None
    if typed is not None and typed.overloads:
        ast = _function_ast(node)
        if len(typed.overloads) == 1:
            declared = typed.overloads[0].overload
            if isinstance(declared, Overload) and isinstance(
                declared.call_site_body, tuple
            ):
                return _compile_function_node(
                    node,
                    name,
                    accepts_stack_inputs=True,
                )
        overloads = tuple(
            _compile_function_overload(ast, overload, name)
            for overload in typed.overloads
        )
        if len(overloads) == 1:
            return overloads[0]
        dispatch_plan = (
            () if typed.dispatch_plan is None else typed.dispatch_plan.branches
        )
        return FunctionSetCode(overloads, dispatch_plan)
    return _compile_function_node(node, name)


def _contains_self_read(value: object) -> bool:
    """Return whether a compiled body explicitly reads the object receiver local."""
    if isinstance(value, TypedNode):
        return _contains_self_read(value.node)
    if isinstance(value, GetVariableNode):
        return value.name.text == "self"
    if isinstance(value, tuple):
        return any(_contains_self_read(item) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        return any(_contains_self_read(getattr(value, item.name)) for item in fields(value))
    return False


def _compile_function_node(
    node: FunctionNode | TypedNode,
    name: str | None = None,
    *,
    break_as_signal: bool = False,
    return_as_signal: bool = False,
    multi: bool = False,
    dispatch_types: tuple[str | None, ...] = (),
    accepts_stack_inputs: bool = False,
) -> FunctionCode:
    """Compile function node during typed-AST bytecode lowering."""
    ast = _function_ast(node)
    params = ()
    if ast.params is not None:
        params = tuple(
            f"_{index}" if param.name is None else param.name.text
            for index, param in enumerate(ast.params)
        )
    elif (inferred_arity := _single_element_function_arity(ast)) is not None:
        params = tuple(f"_{index}" for index in range(inferred_arity))
    cycle_params = bool(params)
    params = (*params, *_ast_static_param_names(ast))
    analysed_type = node.typ if isinstance(node, TypedNode) else None
    return_count = (
        len(analysed_type.returns)
        if isinstance(analysed_type, FunctionType)
        and analysed_type.returns is not None
        else None
    )
    return _Compiler(
        break_as_signal=break_as_signal,
        return_as_signal=return_as_signal,
    ).compile_function(
        ast.body,
        params=params,
        name=name,
        cycle_params=cycle_params,
        cycle_param_offset=(
            1 if params[:1] == ("self",) and ast.returns is None else 0
        ),
        accepts_stack_inputs=accepts_stack_inputs or ast.params is None,
        element_tags=_function_element_tag_names(node),
        recursive=_function_is_recursive(ast),
        multi=multi,
        dispatch_types=dispatch_types,
        return_count=return_count,
    )


def _single_element_function_arity(ast: FunctionNode) -> int | None:
    """Infer a bare function's arity when a typed control-flow body was erased."""
    if len(ast.body) != 1 or not isinstance(ast.body[0], ElementNode):
        return None
    element = ast.body[0]
    if element.modifier_args or element.call_args:
        return None
    arities = {
        len(overload.params)
        for item in BUILTIN_ELEMENTS
        if item.name == element.name
        for overload in item.overloads
        if overload.call_site_body is None
    }
    return next(iter(arities)) if len(arities) == 1 else None


def _borrowed_assignment_receivers(
    body: tuple[ASTNode | TypedNode, ...],
) -> set[int]:
    """Find target-variable loads that may borrow a unique container binding."""
    borrowed: set[int] = set()
    for index, candidate in enumerate(body):
        ast = _unwrap(candidate)
        if not isinstance(ast, SetVariableNode):
            continue
        target_name = ast.name
        target_location = ast.location
        saw_container_set = False
        for previous in reversed(body[:index]):
            previous_ast = _unwrap(previous)
            if isinstance(previous_ast, (FieldSetNode, IndexSetNode)):
                if previous_ast.location == target_location:
                    saw_container_set = True
                continue
            if saw_container_set and isinstance(previous_ast, GetVariableNode):
                if (
                    previous_ast.name == target_name
                    and previous_ast.location == target_location
                ):
                    borrowed.add(id(previous))
                    break
            if isinstance(previous_ast, SetVariableNode):
                break
    return borrowed


def _compile_function_overload(
    ast: FunctionNode,
    overload: FunctionOverloadTyping,
    name: str | None,
) -> FunctionCode:
    """Compile function overload during typed-AST bytecode lowering."""
    typ = overload.typ
    if not isinstance(typ, FunctionType):
        raise CompileError(
            f"cannot compile function overload from {type(typ).__name__}"
        )
    source = overload.overload if isinstance(overload.overload, Overload) else None
    static_params = (
        static_where.static_parameter_names(
            params=source.params,
            returns=source.returns,
            param_names=source.param_names,
            clause=source.where_clause,
        )
        if source is not None
        and (
            source.where_clause
            or static_where.rank_variable_names(source.params + source.returns)
        )
        else _ast_static_param_names(ast)
    )
    if source is not None and source.param_names:
        params = tuple(
            f"_{index}" if item is None else item.text
            for index, item in enumerate(source.param_names)
        )
    elif typ.params is None:
        params = ()
    elif ast.params is None:
        params = tuple(f"_{index}" for index in range(len(typ.params)))
    else:
        params = tuple(
            f"_{index}" if item.name is None else item.name.text
            for index, item in enumerate(ast.params)
        )
    returns = source.returns if source is not None else typ.returns
    return_ranks = tuple(_runtime_collection_rank(item) for item in returns or ())
    return _Compiler().compile_function(
        overload.body,
        params=(*params, *static_params),
        name=name,
        cycle_params=bool(typ.params),
        cycle_param_offset=(
            1 if params[:1] == ("self",) and ast.returns is None else 0
        ),
        element_tags=_function_element_tag_names(overload.typ),
        recursive=_function_is_recursive(ast),
        multi=source is not None and source.is_multi,
        dispatch_types=tuple(
            _runtime_dispatch_type(item) for item in source.params
        ) if source is not None else (),
        return_count=len(returns or ()),
        return_tags=tuple(_runtime_tags_for_type(item) for item in returns or ()),
        return_tag_specs=tuple(
            _runtime_tag_contract_spec(item) for item in returns or ()
        ),
        return_collection_ranks=(
            return_ranks if any(rank is not None for rank in return_ranks) else ()
        ),
        param_collection_ranks=(
            *(_runtime_parameter_rank(item) for item in typ.params or ()),
            *(None for _ in static_params),
        ),
    )


def _ast_static_param_names(ast: FunctionNode) -> tuple[str, ...]:
    """Collect hidden static parameters directly from a function AST."""
    params = tuple(param.typ for param in (ast.params or ()) if param.typ is not None)
    param_names = tuple(param.name for param in (ast.params or ()))
    return static_where.static_parameter_names(
        params=params,
        returns=ast.returns or (),
        param_names=param_names,
        clause=ast.where_clause,
    )


def _runtime_parameter_rank(typ: Type) -> int | None:
    """Return the collection rank a dynamic function parameter accepts."""
    typ = normalize(typ)
    if isinstance(typ, (TaggedType, NoVecType, ExactType)):
        return _runtime_parameter_rank(typ.inner)
    if isinstance(typ, CollectionType):
        return typ.rank if isinstance(typ.rank, int) else None
    if isinstance(typ, UnionType):
        optional_payloads = tuple(
            item.args[0]
            for item in typ.items
            if (
                isinstance(item, NominalType)
                and item.name == Symbol("Some")
                and len(item.args) == 1
            )
        )
        if optional_payloads and any(
            isinstance(item, NoneTypeNode) for item in typ.items
        ):
            payload = (
                optional_payloads[0]
                if len(optional_payloads) == 1
                else UnionType(frozenset(optional_payloads))
            )
            return _runtime_parameter_rank(payload)
        ranks = tuple(_runtime_parameter_rank(item) for item in typ.items)
        # A union such as ``T | T~`` accepts both scalar and collection values
        # as atomic parameters. It must not be assigned scalar rank zero, which
        # would make the prepared-call adapter traverse collection alternatives.
        return ranks[0] if ranks and all(rank == ranks[0] for rank in ranks) else None
    if isinstance(typ, IntersectionType):
        ranks = tuple(_runtime_parameter_rank(item) for item in typ.items)
        return ranks[0] if ranks and all(rank == ranks[0] for rank in ranks) else None
    if isinstance(typ, RankVariable):
        return None
    return 0


def _runtime_dispatch_type(typ: Type) -> str | None:
    """Determine the type of runtime dispatch during typed-AST bytecode lowering."""
    typ = normalize(typ)
    if isinstance(typ, (TaggedType, NoVecType, ExactType)):
        return _runtime_dispatch_type(typ.inner)
    if isinstance(typ, NominalType):
        return show(typ)
    return None


def _unfold_state_arity(node: UnfoldNode, typed_node: TypedNode | None) -> int:
    """Determine the required arity for unfold state during typed-AST bytecode lowering."""
    if isinstance(typed_node, TypedUnfoldNode):
        return typed_node.state_arity
    if node.params is not None:
        return len(node.params)
    raise CompileError("unfold without explicit parameters requires typed analysis")


def _function_ast(node: FunctionNode | TypedNode) -> FunctionNode:
    """Compute function AST during typed-AST bytecode lowering."""
    ast = _unwrap(node)
    if isinstance(ast, DefineNode):
        ast = ast.function
    if not isinstance(ast, FunctionNode):
        raise CompileError(f"cannot compile function from {type(ast).__name__}")
    return ast


def _max_break_values(value: ASTNode | tuple[object, ...]) -> int:
    """Return the largest break-result arity nested within an AST value."""
    if isinstance(value, BreakNode):
        best = len(value.values)
    else:
        best = 0
    children = value.__dict__.values() if isinstance(value, ASTNode) else value
    for child in children:
        if isinstance(child, (ASTNode, tuple)):
            best = max(best, _max_break_values(child))
    return best


def _function_element_tag_names(
    node: FunctionNode | TypedNode | Type,
) -> tuple[str, ...]:
    """Collect the names for function element tag during typed-AST bytecode lowering."""
    typ = node.typ if isinstance(node, TypedNode) else node
    if isinstance(typ, FunctionType):
        return tuple(
            sorted(str(tag.name) for tag in typ.element_tags if not tag.absent)
        )
    ast = _unwrap(node) if isinstance(node, (FunctionNode, TypedNode)) else None
    if isinstance(ast, FunctionNode):
        return tuple(
            sorted(str(tag.name) for tag in ast.element_tags if not tag.absent)
        )
    return ()


def _function_is_recursive(ast: FunctionNode) -> bool:
    """Return the Boolean result of function is recursive during typed-AST bytecode lowering."""
    return any(
        isinstance(annotation, AnnotationNode) and annotation.name.text == "recursive"
        for annotation in ast.annotations
    )


def _substitute_runtime_type_template(
    template: str,
    arguments: tuple[str, ...],
) -> str:
    """Compose one generic projection template with another."""
    rendered = template
    for index in range(len(arguments) - 1, -1, -1):
        rendered = rendered.replace(f"${index}", arguments[index])
    return rendered


def _runtime_supertype_template(
    typ: Type,
    generics: tuple[Symbol, ...],
) -> tuple[str, tuple[str, ...]] | None:
    """Encode one generic supertype projection using positional placeholders."""
    typ = normalize(typ)
    if not isinstance(typ, NominalType):
        return None
    indexes = {generic.text: index for index, generic in enumerate(generics)}
    return (
        _symbol_runtime_name(typ.name),
        tuple(_runtime_type_template(arg, indexes) for arg in typ.args),
    )


def _runtime_type_template(typ: Type, indexes: dict[str, int]) -> str:
    """Render a static type with declaration generics as ``$N`` slots."""
    typ = normalize(typ)
    if isinstance(typ, NominalType):
        if not typ.args and typ.name.text in indexes:
            return f"${indexes[typ.name.text]}"
        name = _symbol_runtime_name(typ.name)
        if not typ.args:
            return name
        return f"{name}[{', '.join(_runtime_type_template(arg, indexes) for arg in typ.args)}]"
    if isinstance(typ, VarType) and typ.name in indexes:
        return f"${indexes[typ.name]}"
    if isinstance(typ, UnionType):
        return " | ".join(
            sorted(_runtime_type_template(item, indexes) for item in typ.items)
        )
    return show(typ)


def _runtime_generic_variances(node: ObjectNode) -> tuple[str, ...]:
    """Infer serializable generic variance facts for a declaration."""
    if not node.generics:
        return ()
    usage = {generic.text: [False, False] for generic in node.generics}
    for field in node.fields:
        if field.typ is None:
            continue
        _record_runtime_variance_use(field.typ, +1, usage)
        if field.access.text == "public":
            _record_runtime_variance_use(field.typ, -1, usage)
    for requirement in node.requirements:
        for param in requirement.params or ():
            if param.typ is not None:
                _record_runtime_variance_use(param.typ, -1, usage)
        for ret in requirement.returns or ():
            _record_runtime_variance_use(ret, +1, usage)
    inferred: list[str] = []
    for generic in node.generics:
        positive, negative = usage[generic.text]
        if positive and not negative:
            inferred.append("covariant")
        elif negative and not positive:
            inferred.append("contravariant")
        else:
            inferred.append("invariant")
    if len(node.generic_variances) != len(node.generics):
        return tuple(inferred)
    result: list[str] = []
    for index, marker in enumerate(node.generic_variances):
        if marker is None:
            result.append(inferred[index])
        elif marker.text in {"any", "covariant"}:
            result.append("covariant")
        elif marker.text in {"above", "contravariant"}:
            result.append("contravariant")
        else:
            result.append("invariant")
    return tuple(result)


def _record_runtime_variance_use(
    typ: Type,
    polarity: int,
    usage: dict[str, list[bool]],
) -> None:
    """Record positive and negative generic uses for runtime metadata."""
    typ = normalize(typ)
    if isinstance(typ, VarType):
        if typ.name in usage:
            usage[typ.name][0 if polarity > 0 else 1] = True
        return
    if isinstance(typ, NominalType):
        if not typ.args and typ.name.text in usage:
            usage[typ.name.text][0 if polarity > 0 else 1] = True
            return
        for arg in typ.args:
            _record_runtime_variance_use(arg, polarity, usage)
        return
    if isinstance(typ, (UnionType, IntersectionType)):
        for item in typ.items:
            _record_runtime_variance_use(item, polarity, usage)
        return
    if isinstance(typ, TupleType):
        for item in typ.params:
            _record_runtime_variance_use(item, polarity, usage)
        return
    if isinstance(typ, VariadicTupleType):
        for item in typ.items:
            _record_runtime_variance_use(item.typ, polarity, usage)
        return
    if isinstance(typ, RowType):
        _record_runtime_variance_use(typ.base, polarity, usage)
        for field in typ.fields:
            _record_runtime_variance_use(field.typ, polarity, usage)
        return
    if isinstance(typ, CollectionType):
        _record_runtime_variance_use(typ.base, polarity, usage)
        return
    if isinstance(typ, FunctionType):
        if typ.params is not None:
            for param in typ.params:
                _record_runtime_variance_use(param, -polarity, usage)
        if typ.returns is not None:
            for ret in typ.returns:
                _record_runtime_variance_use(ret, polarity, usage)
        for tag in typ.element_tags:
            for arg in tag.args:
                _record_runtime_variance_use(arg, polarity, usage)
        return
    if isinstance(typ, AnonymousTraitType):
        for requirement in typ.requirements:
            for param in requirement.overload.params:
                _record_runtime_variance_use(param, -polarity, usage)
            for ret in requirement.overload.returns:
                _record_runtime_variance_use(ret, polarity, usage)
        return
    if isinstance(typ, (TaggedType, NoVecType, ExactType)):
        _record_runtime_variance_use(typ.inner, polarity, usage)


def _object_runtime_metadata(
    name: str,
    annotations: tuple[ASTNode, ...],
    definitions: tuple[DefineNode, ...],
) -> tuple[str | None, str | None, str | None, str | None, tuple[str, ...]]:
    """Return all lifecycle metadata required by an object's runtime type."""
    destructor_name = None
    dup_name = None
    dup_error = None
    for definition in definitions:
        definition_name = definition.name.text
        if definition_name == f"~{name.rsplit('.', 1)[-1]}":
            destructor_name = f"{name}::{definition_name}"
        elif definition_name == "dup":
            dup_name = f"{name}::dup"
            for annotation in definition.annotations:
                if (
                    isinstance(annotation, AnnotationNode)
                    and annotation.name.text == "error"
                ):
                    dup_error = next(
                        (
                            arg.value
                            for arg in annotation.args
                            if isinstance(arg, StringLiteralNode)
                        ),
                        None,
                    )
                    break

    mustcall_mode = None
    mustcall_methods: tuple[str, ...] = ()
    for annotation in annotations:
        if (
            not isinstance(annotation, AnnotationNode)
            or annotation.name.text != "mustcall"
        ):
            continue
        kwargs = dict(annotation.kwargs)
        for mode in ("all", "any"):
            value = kwargs.get(Symbol(mode))
            if not isinstance(value, ListLiteralNode):
                continue
            methods: list[str] = []
            for item in value.items:
                if len(item) != 1 or not isinstance(item[0], StringLiteralNode):
                    break
                methods.append(item[0].value)
            else:
                mustcall_mode = mode
                mustcall_methods = tuple(methods)
                break
        break

    return (
        destructor_name,
        dup_name,
        dup_error,
        mustcall_mode,
        mustcall_methods,
    )


def _source_site(node: ASTNode) -> str | None:
    """Render one stable source location for runtime concurrency diagnostics."""
    location = node.location
    if location is None:
        return None
    return f"{location.line}:{location.column}"


def _unwrap(node: ASTNode | TypedNode) -> ASTNode:
    """Compute unwrap during typed-AST bytecode lowering."""
    if isinstance(node, TypedNode):
        return node.node
    return node


def _symbol_runtime_name(symbol: Symbol) -> str:
    """Return the canonical name for symbol runtime during typed-AST bytecode lowering."""
    return symbol.dotted()


def _resolved_element_reference(
    node: TypedNode | None,
) -> ResolvedElementReference | None:
    """Compute resolved element reference during typed-AST bytecode lowering."""
    if not isinstance(node, TypedElementNode):
        return None
    if node.overload_index is None:
        return None
    ast = node.node
    if not isinstance(ast, ElementNode):
        return None
    source_runtime_name = _symbol_runtime_name(ast.name).removeprefix("*::")
    runtime_name = _symbol_runtime_name(
        node.runtime_name if node.runtime_name is not None else ast.name
    ).removeprefix("*::")
    returned = (
        node.overload.actual_returns[0]
        if node.overload is not None and node.overload.actual_returns
        else None
    )
    type_args = (
        tuple(show(arg) for arg in returned.args)
        if isinstance(returned, NominalType) and returned.args
        else ()
    )
    elements = runtime_elements()
    element = elements.get(source_runtime_name)
    if element is not None:
        if not 0 <= node.overload_index < len(element.definitions):
            return None
        definition = element.definitions[node.overload_index]
        if definition.implementation is None:
            raise CompileError(
                f"cannot compile static-only overload {node.overload_index} "
                f"of built-in element '{source_runtime_name}'"
            )
        if (
            node.overload is not None
            and node.overload.overload != definition.signature
            and node.overload.overload.call_site_body is None
            and not type_args
        ):
            return None
    if (
        element is None
        and Symbol(source_runtime_name) in {item.name for item in BUILTIN_ELEMENTS}
        and not type_args
    ):
        return None
    vectorised = bool(node.overload is not None and node.overload.vectorised)
    if (
        ast.name.text == "call"
        and not ast.name.namespace
        and node.call_overload_index is not None
    ):
        static_values = (
            node.call_overload_index,
            *(node.overload.runtime_static_values if node.overload is not None else ()),
        )
    elif node.overload is not None and node.overload.runtime_static_values:
        static_values = node.overload.runtime_static_values
    else:
        static_values = (
            tuple(
                RuntimeNumber(value)
                for _, value in node.overload.rank_values
            )
            if node.overload is not None and node.overload.rank_values
            else ()
        )
    multidispatch = bool(node.overload is not None and node.overload.multidispatch)
    vectorised_depths = (
        tuple(node.overload.vectorised_depths) if node.overload is not None else ()
    )
    vectorised_target_ranks = (
        tuple(node.overload.vectorised_target_ranks)
        if node.overload is not None
        else ()
    )
    return_collection_ranks = (
        tuple(_runtime_collection_rank(ret) for ret in node.overload.actual_returns)
        if node.overload is not None
        else ()
    )
    if not any(rank is not None for rank in return_collection_ranks):
        return_collection_ranks = ()
    return_tags = (
        _call_site_return_tag_contract(node.overload)
        if node.overload is not None
        else ()
    )
    return_tag_specs = (
        _call_site_return_tag_specs(node.overload) if node.overload is not None else ()
    )
    arity_override = None
    consumed_override = None
    if node.overload is not None:
        declared_arity = (
            len(element.definitions[node.overload_index].signature.params)
            if element is not None
            else len(node.overload.overload.params)
        )
        if len(node.overload.params) != declared_arity:
            hidden_count = len(static_values) if element is None else 0
            runtime_consumed = node.overload.runtime_consumed_count
            if runtime_consumed is None:
                runtime_consumed = declared_arity
                arity_override = declared_arity + hidden_count
            else:
                arity_override = len(node.overload.params) + hidden_count
            consumed_override = runtime_consumed + hidden_count
    return ResolvedElementReference(
        runtime_name,
        node.overload_index,
        vectorised=vectorised,
        vectorised_depths=vectorised_depths,
        vectorised_target_ranks=vectorised_target_ranks,
        return_collection_ranks=return_collection_ranks,
        return_tags=return_tags,
        return_tag_specs=return_tag_specs,
        type_args=type_args,
        static_values=static_values,
        arity_override=arity_override,
        consumed_override=consumed_override,
        multidispatch=multidispatch,
        extension=_compiled_element_extension(node.extension),
    )


def _runtime_collection_rank(typ: Type | None) -> int | None:
    """Determine the collection rank for runtime collection during typed-AST bytecode lowering."""
    if typ is None:
        return None
    typ = normalize(typ)
    if isinstance(typ, (TaggedType, NoVecType, ExactType)):
        return _runtime_collection_rank(typ.inner)
    if isinstance(typ, (ListExactType, ArrayExactType)) and isinstance(typ.rank, int):
        return typ.rank
    return None


def _call_site_return_tag_contract(
    applied: AppliedOverload,
) -> tuple[tuple[DataTag, ...], ...]:
    """Return a runtime contract when a call-site overlay changes tag flow."""
    actual = tuple(_runtime_tags_for_type(ret) for ret in applied.actual_returns)
    declared = tuple(_runtime_tags_for_type(ret) for ret in applied.overload.returns)
    return actual if actual != declared else ()


def _call_site_return_tag_specs(applied: AppliedOverload) -> tuple[object, ...]:
    """Return recursive contracts when tagged inputs may affect call results."""
    if not any(_type_contains_data_tags(param) for param in applied.params):
        actual = tuple(
            _runtime_tag_contract_spec(ret) for ret in applied.actual_returns
        )
        declared = tuple(
            _runtime_tag_contract_spec(ret) for ret in applied.overload.returns
        )
        return actual if actual != declared else ()
    return tuple(_runtime_tag_contract_spec(ret) for ret in applied.actual_returns)


def _type_contains_data_tags(typ: Type) -> bool:
    """Return whether any part of a type carries a data-tag fact."""
    typ = normalize(typ)
    if isinstance(typ, TaggedType):
        return bool(typ.tags) or _type_contains_data_tags(typ.inner)
    if isinstance(typ, CollectionType):
        return _type_contains_data_tags(typ.base)
    if isinstance(typ, NominalType):
        return any(_type_contains_data_tags(arg) for arg in typ.args)
    if isinstance(typ, (UnionType, IntersectionType)):
        return any(_type_contains_data_tags(item) for item in typ.items)
    if isinstance(typ, TupleType):
        return any(_type_contains_data_tags(item) for item in typ.params)
    if isinstance(typ, (NoVecType, ExactType)):
        return _type_contains_data_tags(typ.inner)
    return False


def _runtime_tags_for_type(typ: Type) -> tuple[DataTag, ...]:
    """Return all runtime-reified tags attached to one value type."""
    typ = normalize(typ)
    if isinstance(typ, TaggedType):
        return tuple(sorted(typ.tags))
    return ()


def _compiled_element_extension(
    extension: TypedElementExtension | None,
) -> VectorExtensionReference | None:
    """Compute compiled element extension during typed-AST bytecode lowering."""
    if extension is None:
        return None
    return VectorExtensionReference(
        default=(
            _compile_function_value(extension.default)
            if extension.default is not None
            else None
        ),
        rules=tuple(
            ExtensionRuleReference(
                tuple(name is not None for name in rule.pattern),
                _compile_function_value(rule.function),
            )
            for rule in extension.rules
        ),
        selector=(
            _compile_function_value(extension.selector)
            if extension.selector is not None
            else None
        ),
    )


def _index_spec(
    selectors: tuple[IndexSelector, ...],
    spread: bool,
    grouped_update: bool = False,
) -> IndexOperationSpec:
    """Describe the runtime stack shape of an indexed read or write."""
    return IndexOperationSpec(
        selectors=tuple(
            IndexSelectorSpec(
                is_slice=selector.is_slice,
                has_start=bool(selector.start),
                has_stop=bool(selector.stop),
                has_step=bool(selector.step),
            )
            for selector in selectors
        ),
        spread=spread,
        grouped_update=grouped_update,
    )


def _literal_expression_value(nodes: tuple[ASTNode, ...]) -> object:
    """Compute literal expression value during typed-AST bytecode lowering."""
    if len(nodes) != 1:
        raise CompileError("object and enum default values must be literal values")
    node = nodes[0]
    match node:
        case NumberLiteralNode(value):
            return _number(value, node)
        case StringLiteralNode(value):
            return value
        case StringInterpolationNode():
            raise CompileError("object and enum default values must be literal values")
        case _:
            raise CompileError("object and enum default values must be literal values")


def _number(value: str, node: ASTNode) -> RuntimeNumber:
    """Compute number during typed-AST bytecode lowering."""
    try:
        return RuntimeNumber(value)
    except TypeError as exc:
        location = ""
        if node.location is not None:
            location = f" at {node.location.line}:{node.location.column}"
        message = f"cannot compile numeric literal {value!r}{location}"
        raise CompileError(message) from exc


def _compile_match_pattern(
    pattern: MatchPatternNode,
    typed_guards: Iterator[tuple[ASTNode | TypedNode, ...]] | None = None,
    guard_arities: Iterator[int] | None = None,
    *,
    extracting: bool = False,
    root: bool = False,
) -> object:
    """Compile one pattern, consuming analysed guards in traversal order."""
    match pattern:
        case ExtractPatternNode(inner):
            return (
                "extracting",
                _compile_match_pattern(
                    inner,
                    typed_guards,
                    guard_arities,
                    extracting=True,
                    root=True,
                ),
            )
        case LiteralPatternNode(value):
            if extracting and root and isinstance(value, StringLiteralNode):
                source = re.sub(r"\(\?<([A-Za-z_]\w*)>", r"(?P<\1>", value.value)
                compiled = re.compile(source)
                named_by_index = {index: name for name, index in compiled.groupindex.items()}
                group_names = tuple(named_by_index.get(index) for index in range(1, compiled.groups + 1))
                return ("regex", source, group_names)
            return ("literal", _literal_pattern_value(value))
        case ExpressionPatternNode(expression):
            return ("literal", _literal_expression_value(expression))
        case GuardPatternNode(condition):
            guard = _next_typed_guard(typed_guards, condition)
            arity = next(guard_arities) if guard_arities is not None else 1
            return ("guard", _compile_guard(guard, arity), arity)
        case WildcardPatternNode():
            return ("capture",) if extracting and not root else ("wildcard",)
        case RestPatternNode(name):
            if extracting and not root and name is None:
                return ("capture_rest",)
            return ("rest", None if name is None else name.text)
        case BindingPatternNode(name, inner):
            if isinstance(inner, RestPatternNode):
                return ("rest", name.text)
            return (
                "bind",
                name.text,
                _compile_match_pattern(inner, typed_guards, guard_arities, extracting=False, root=False),
            )
        case OrPatternNode(options):
            return (
                "or",
                tuple(
                    _compile_match_pattern(option, typed_guards, guard_arities, extracting=extracting, root=root)
                    for option in options
                ),
            )
        case ListPatternNode(items):
            return (
                "list",
                tuple(
                    _compile_match_pattern(item, typed_guards, guard_arities, extracting=extracting, root=False)
                    for item in items
                ),
            )
        case TypePatternNode(typ, name, fields, guard):
            compiled_guard = None
            if guard:
                compiled_guard = _compile_guard(
                    _next_typed_guard(typed_guards, guard)
                )
            return (
                "type",
                None if typ is None else _cast_type_spec(typ),
                None if name is None else name.text,
                tuple(
                    _compile_match_pattern(field, typed_guards, guard_arities, extracting=extracting, root=False)
                    for field in fields
                ),
                compiled_guard,
            )
        case _:
            raise CompileError(f"cannot compile match pattern {pattern!r}")


def _next_typed_guard(
    typed_guards: Iterator[tuple[ASTNode | TypedNode, ...]] | None,
    source: tuple[ASTNode, ...],
) -> tuple[ASTNode | TypedNode, ...]:
    """Return the analysed guard when available, otherwise preserve raw lowering."""
    if typed_guards is None:
        return source
    try:
        return next(typed_guards)
    except StopIteration as exc:
        raise CompileError("typed match guard count is below source patterns") from exc


def _literal_pattern_value(node: ASTNode) -> object:
    """Compute literal pattern value during typed-AST bytecode lowering."""
    match node:
        case NumberLiteralNode(value):
            return _number(value, node)
        case StringLiteralNode(value):
            return value
        case StringInterpolationNode():
            raise CompileError(f"cannot compile interpolated string pattern {node!r}")
        case _:
            raise CompileError(f"cannot compile literal pattern {node!r}")


def _compile_guard(
    condition: tuple[ASTNode | TypedNode, ...],
    arity: int = 1,
) -> FunctionCode:
    """Compile an analysed guard while preserving selected call metadata."""
    params = tuple(f"_{index}" for index in range(arity))
    return _Compiler().compile_function(condition, params=params, name="<match guard>")


def _runtime_tag_contract_spec(typ: Type) -> object:
    """Compile the structural tag contract needed to canonicalize a value."""
    from valiance.vtypes import VarType

    typ = normalize(typ)
    if isinstance(typ, TaggedType):
        return (
            "tagged",
            _runtime_tag_contract_spec(typ.inner),
            tuple((str(tag.name), tag.depth, tag.absent) for tag in sorted(typ.tags)),
        )
    if isinstance(typ, NoneTypeNode):
        return ("none",)
    if isinstance(typ, VarType):
        return ("any",)
    if isinstance(typ, NominalType):
        return ("nominal", typ.name.text)
    if isinstance(typ, UnionType):
        return ("union", tuple(_runtime_tag_contract_spec(item) for item in typ.items))
    if isinstance(typ, IntersectionType):
        return (
            "intersection",
            tuple(_runtime_tag_contract_spec(item) for item in typ.items),
        )
    if isinstance(typ, TupleType):
        return ("tuple", tuple(_runtime_tag_contract_spec(item) for item in typ.params))
    if isinstance(typ, CollectionType):
        kind = {
            ListExactType: "list_exact",
            ListMinType: "list_min",
            ListRuggedType: "list_rugged",
            ArrayExactType: "array_exact",
            ArrayMinType: "array_min",
        }[type(typ)]
        return (
            "collection",
            kind,
            typ.rank,
            _runtime_tag_contract_spec(typ.base),
        )
    if isinstance(typ, (NoVecType, ExactType)):
        return _runtime_tag_contract_spec(typ.inner)
    return ("any",)


def _cast_type_spec(typ: Type) -> object:
    """Compute cast type spec during typed-AST bytecode lowering."""
    from valiance.vtypes import VarType

    typ = normalize(typ)
    if isinstance(typ, NoneTypeNode):
        return ("none",)
    if isinstance(typ, VarType):
        return ("var", typ.name)
    if isinstance(typ, TaggedType):
        return (
            "tagged",
            _cast_type_spec(typ.inner),
            tuple((str(tag.name), tag.depth, tag.absent) for tag in sorted(typ.tags)),
        )
    if isinstance(typ, NominalType):
        return (
            "nominal",
            typ.name.text,
            tuple(show(arg) for arg in typ.args),
        )
    if isinstance(typ, UnionType):
        return ("union", tuple(_cast_type_spec(item) for item in typ.items))
    if isinstance(typ, IntersectionType):
        return ("intersection", tuple(_cast_type_spec(item) for item in typ.items))
    if isinstance(typ, TupleType):
        return ("tuple", tuple(_cast_type_spec(item) for item in typ.params))
    if isinstance(typ, CollectionType):
        kind = {
            ListExactType: "list_exact",
            ListMinType: "list_min",
            ListRuggedType: "list_rugged",
            ArrayExactType: "array_exact",
            ArrayMinType: "array_min",
        }[type(typ)]
        return ("collection", kind, typ.rank, _cast_type_spec(typ.base))
    if isinstance(typ, (NoVecType, ExactType)):
        return _cast_type_spec(typ.inner)
    raise CompileError(f"cannot compile checked cast to {typ}")
