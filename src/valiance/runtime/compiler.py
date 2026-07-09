"""AST to bytecode compiler."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import NoReturn

from valiance.analysis.builtins import BUILTIN_ELEMENTS, runtime_elements
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
    MatchPatternNode,
    NumberLiteralNode,
    ObjectNode,
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
    TypedElementNode,
    TypedElementExtension,
    TypedFunctionNode,
    TypedLiteralNode,
    TypedNode,
    TypedTagApplicationNode,
    TypedUnfoldNode,
    TypePatternNode,
    UnfoldNode,
    WhileNode,
    WildcardPatternNode,
)
from valiance.runtime.bytecode import (
    ExtensionRuleReference,
    FunctionCode,
    FunctionSetCode,
    Instruction,
    OpCode,
    Program,
    ResolvedElementReference,
    VectorExtensionReference,
)
from valiance.symbols import Symbol
from valiance.types import (
    AtomicType,
    ArrayExactType,
    ArrayMinType,
    CollectionType,
    DataTag,
    ExactType,
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
    normalize,
    show,
)


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
    ) -> None:
        self.instructions: list[Instruction] = []
        self.loops: list[_LoopPatch] = []
        self.break_as_signal = break_as_signal
        self.return_as_signal = return_as_signal
        self.object_runtime_metadata: dict[
            str,
            tuple[
                str | None,
                str | None,
                str | None,
                str | None,
                str | None,
                tuple[str, ...],
            ],
        ] = {}
        self.tag_disjoints: dict[str, set[str]] = {}
        self.tag_parents: dict[str, str] = {}

    def compile_function(
        self,
        body: tuple[ASTNode | TypedNode, ...],
        *,
        params: tuple[str, ...] = (),
        name: str | None = None,
        cycle_params: bool = False,
        element_tags: tuple[str, ...] = (),
        recursive: bool = False,
        multi: bool = False,
        dispatch_types: tuple[str | None, ...] = (),
        return_tags: tuple[tuple[DataTag, ...], ...] = (),
    ) -> FunctionCode:
        for index, node in enumerate(body):
            self.node(node)
            if index + 1 < len(body) and _should_pop_statement_result(node):
                self.emit(OpCode.POP)
        self.emit(OpCode.RETURN)
        return FunctionCode(
            tuple(self.instructions),
            params,
            name,
            cycle_params,
            element_tags,
            recursive,
            multi,
            dispatch_types,
            return_tags,
        )

    def node(self, node: ASTNode | TypedNode) -> None:
        typed_node = node if isinstance(node, TypedNode) else None
        node = _unwrap(node)
        match node:
            case NumberLiteralNode(value):
                self.emit(OpCode.PUSH_CONST, _number(value, node))
            case StringLiteralNode(value):
                self.emit(OpCode.PUSH_CONST, value)
            case StringInterpolationNode(parts):
                self.string_interpolation(parts)
            case GetVariableNode(name):
                self.emit(OpCode.LOAD_VAR, _symbol_runtime_name(name))
            case SetVariableNode(name):
                self.emit(OpCode.STORE_VAR, _symbol_runtime_name(name))
            case SetVariablesNode(targets):
                for target in reversed(targets):
                    self.emit(OpCode.STORE_VAR, _symbol_runtime_name(target.name))
            case ElementNode(name, modifier_args):
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
                        f"_call_arg_{index}" for index in range(len(node.call_args))
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
                    self.emit(OpCode.LOAD_ELEMENT, _symbol_runtime_name(name))
                    self.emit(OpCode.CALL)
                else:
                    self.emit(OpCode.CALL_RESOLVED_ELEMENT, resolved)
                tupled_count = _tupled_element_return_count(node, typed_node)
                if tupled_count is not None:
                    self.emit(OpCode.BUILD_TUPLE, tupled_count)
            case TagApplicationNode():
                if isinstance(typed_node, TypedTagApplicationNode):
                    validator_index = typed_node.validator_index
                    added_tags = typed_node.added_tags
                    removed_tags = typed_node.removed_tags
                elif node.tag.absent:
                    validator_index = None
                    added_tags = ()
                    removed_tags = (DataTag(node.tag.name, node.tag.depth),)
                else:
                    validator_index = None
                    added = [DataTag(node.tag.name, node.tag.depth)]
                    parent = self.tag_parents.get(node.tag.name)
                    if parent is not None:
                        added.append(DataTag(parent, node.tag.depth))
                    added_tags = tuple(added)
                    removed_tags = tuple(
                        DataTag(name, node.tag.depth)
                        for name in sorted(self.tag_disjoints.get(node.tag.name, ()))
                    )
                self.emit(
                    OpCode.VALIDATE_TAG,
                    (
                        f"#{node.tag.name}",
                        validator_index,
                        tuple((tag.name, tag.depth) for tag in added_tags),
                        tuple((tag.name, tag.depth) for tag in removed_tags),
                    ),
                )
            case TagDeclarationNode():
                self._register_runtime_tag_declaration(node)
            case ElementTagDeclarationNode() | TagOverlayNode():
                pass
            case CastNode(typ, checked):
                if checked:
                    self.emit(OpCode.CHECK_CAST, _cast_type_spec(typ))
            case StackShuffleNode():
                self.emit(OpCode.STACK_SHUFFLE, _stack_shuffle_spec(node))
            case FunctionNode():
                self.emit(
                    OpCode.MAKE_FUNCTION,
                    _compile_function_value(typed_node or node),
                )
            case DefineNode(name, function):
                self.emit(
                    OpCode.MAKE_FUNCTION,
                    _compile_function_value(
                        typed_node or function,
                        _symbol_runtime_name(name),
                    ),
                )
                self.emit(OpCode.STORE_VAR, _symbol_runtime_name(name))
            case ImportNode():
                pass
            case ListLiteralNode(items) | ArrayLiteralNode(items):
                compiled_items = (
                    typed_node.items
                    if isinstance(typed_node, TypedLiteralNode)
                    else items
                )
                self.collection(compiled_items, OpCode.BUILD_LIST)
            case TupleLiteralNode(items):
                compiled_items = (
                    typed_node.items
                    if isinstance(typed_node, TypedLiteralNode)
                    else items
                )
                self.collection(compiled_items, OpCode.BUILD_TUPLE)
            case RecordLiteralNode(fields):
                typed_items = (
                    typed_node.items
                    if isinstance(typed_node, TypedLiteralNode)
                    else ()
                )
                keys = []
                for index, (key, expr) in enumerate(fields):
                    self.expression(typed_items[index] if typed_items else expr)
                    keys.append(key.text)
                self.emit(OpCode.BUILD_RECORD, tuple(keys))
            case DictLiteralNode(entries):
                typed_items = (
                    typed_node.items
                    if isinstance(typed_node, TypedLiteralNode)
                    else ()
                )
                expressions = tuple(expr for entry in entries for expr in entry)
                for index, expr in enumerate(expressions):
                    self.expression(typed_items[index] if typed_items else expr)
                self.emit(OpCode.BUILD_DICT, len(entries))
            case ObjectNode():
                self.object_declaration(node)
            case FieldAccessNode(name):
                self.emit(OpCode.GET_FIELD, name.text)
            case FieldSetNode(name):
                self.emit(OpCode.SET_FIELD, name.text)
            case IndexAccessNode(selectors, spread):
                self.emit(OpCode.GET_INDEX, _index_spec(selectors, spread))
            case IndexSetNode(selectors):
                self.emit(OpCode.SET_INDEX, _index_spec(selectors, False))
            case IfNode():
                self.if_node(node)
            case AssertNode():
                self.assert_node(node)
            case MatchNode():
                self.match_node(node)
            case TryNode():
                self.try_node(node)
            case WhileNode():
                self.while_node(node)
            case UnfoldNode():
                self.unfold_node(node, typed_node)
            case AtNode():
                self.at_node(node)
            case ForNode():
                self.foreach_node(node)
            case BreakNode():
                self.break_node(node)
            case ReturnNode(values):
                for value in values:
                    self.node(value)
                self.emit(
                    OpCode.RETURN_SIGNAL if self.return_as_signal else OpCode.RETURN
                )
            case _:
                self.unsupported(node, type(node).__name__)

    def expression(self, nodes: tuple[ASTNode | TypedNode, ...]) -> None:
        for node in nodes:
            self.node(node)

    def collection(
        self,
        items: tuple[tuple[ASTNode | TypedNode, ...], ...],
        op: OpCode,
    ) -> None:
        for item in items:
            self.expression(item)
        self.emit(op, len(items))

    def _register_runtime_tag_declaration(self, node: TagDeclarationNode) -> None:
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
        template: list[str | None] = []
        for part in parts:
            if isinstance(part, str):
                template.append(part)
                continue
            self.expression(part)
            template.append(None)
        self.emit(OpCode.BUILD_STRING, tuple(template))

    def object_declaration(self, node: ObjectNode) -> None:
        match node.kind.text:
            case "object":
                runtime_name = _symbol_runtime_name(node.name)
                if node.target is None:
                    self.object_runtime_metadata[runtime_name] = (
                        _object_runtime_metadata(
                            runtime_name,
                            node.annotations,
                            node.definitions,
                        )
                    )
                    self.object_constructor(
                        runtime_name,
                        node.fields,
                        alias=(
                            runtime_name
                            if not node.name.namespace
                            else None
                        ),
                    )
                for definition in node.definitions:
                    self.friendly_definition(runtime_name, definition)
            case "variant":
                for member in node.variants:
                    runtime_name = (
                        f"{_symbol_runtime_name(node.name)}."
                        f"{_symbol_runtime_name(member.name)}"
                    )
                    self.object_runtime_metadata[runtime_name] = (
                        _object_runtime_metadata(
                            runtime_name,
                            node.annotations,
                            member.definitions,
                        )
                    )
                    self.object_constructor(
                        runtime_name,
                        member.fields,
                        alias=member.name.text,
                    )
                    for definition in member.definitions:
                        self.friendly_definition(runtime_name, definition)
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
        alias: str | None = None,
    ) -> None:
        field_names: list[str] = []
        default_values: list[tuple[str, object]] = []
        for field in fields:
            field_names.append(field.name.text)
            if field.default:
                default_values.append(
                    (field.name.text, _literal_expression_value(field.default))
                )
        required = tuple(
            field_name
            for field_name in field_names
            if field_name not in {name for name, _ in default_values}
        )
        self.emit(
            OpCode.MAKE_OBJECT_CONSTRUCTOR,
            (
                name,
                tuple(field_names),
                required,
                tuple(default_values),
                self.object_runtime_metadata.get(
                    name,
                    (None, None, None, None, None, ()),
                ),
            ),
        )
        self.emit(OpCode.STORE_VAR, name)
        if alias is not None and alias != name:
            self.emit(OpCode.LOAD_ELEMENT, name)
            self.emit(OpCode.STORE_VAR, alias)

    def friendly_definition(self, owner: str, definition: DefineNode) -> None:
        body = definition.function.body
        if _definition_has_annotation(definition, "self"):
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
            _compile_function_value(node, runtime_definition_name),
        )
        self.emit(OpCode.STORE_VAR, runtime_definition_name)
        self.emit(
            OpCode.MAKE_FUNCTION,
            _compile_function_value(node, f"{owner}::{runtime_definition_name}"),
        )
        self.emit(OpCode.STORE_VAR, f"{owner}::{runtime_definition_name}")

    def if_node(self, node: IfNode) -> None:
        for condition_node in node.condition:
            self.node(condition_node)
        jump_to_else = self.emit(OpCode.JUMP_IF_FALSE, None)
        for branch_node in node.then_branch:
            self.node(branch_node)
        jump_to_end = self.emit(OpCode.JUMP, None)
        else_start = len(self.instructions)
        self.patch(jump_to_else, else_start)
        for branch_node in node.else_branch:
            self.node(branch_node)
        self.patch(jump_to_end, len(self.instructions))

    def assert_node(self, node: AssertNode) -> None:
        for condition_node in node.condition:
            self.node(condition_node)
        if not node.else_branch:
            self.emit(OpCode.ASSERT_TRUE)
            return
        jump_to_else = self.emit(OpCode.JUMP_IF_FALSE, None)
        jump_to_end = self.emit(OpCode.JUMP, None)
        else_start = len(self.instructions)
        self.patch(jump_to_else, else_start)
        for branch_node in node.else_branch:
            self.node(branch_node)
        self.patch(jump_to_end, len(self.instructions))

    def unfold_node(self, node: UnfoldNode, typed_node: TypedNode | None) -> None:
        arity = _unfold_state_arity(node, typed_node)
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

    def at_node(self, node: AtNode) -> None:
        self.emit(OpCode.CYCLE_BEGIN, (None, 0))
        self.expression(node.body)
        self.emit(OpCode.CYCLE_END)

    def foreach_node(self, node: ForNode) -> None:
        params = (FunctionParam(node.variable),)
        if node.index_variable is not None:
            params += (FunctionParam(node.index_variable),)
        body = FunctionNode(
            params=params,
            body=node.body,
            location=node.location,
        )
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

    def match_node(self, node: MatchNode) -> None:
        arity = _match_arity(node)
        if arity:
            self.emit(OpCode.SOURCE_ARGS, arity)
        case_jumps: list[tuple[int, MatchCaseNode]] = []
        default_case: MatchCaseNode | None = None
        for case in node.cases:
            if case.is_default or _is_default_case(case.patterns):
                default_case = case
                continue
            case_jumps.append(
                (
                    self.emit(
                        OpCode.JUMP_IF_MATCH,
                        (_compile_case_patterns(case.patterns), None),
                    ),
                    case,
                )
            )

        end_jumps: list[int] = []
        if default_case is None:
            self.emit(OpCode.MATCH_ERROR)
        else:
            default_jump = self.emit(
                OpCode.JUMP_IF_MATCH,
                (_compile_case_patterns(default_case.patterns), None),
            )
            self.emit(OpCode.MATCH_ERROR)
            self.patch_match(default_jump, len(self.instructions))
            for branch_node in default_case.body:
                self.node(branch_node)
            self.emit(OpCode.CYCLE_END)
            end_jumps.append(self.emit(OpCode.JUMP, None))

        for jump, case in case_jumps:
            self.patch_match(jump, len(self.instructions))
            for branch_node in case.body:
                self.node(branch_node)
            self.emit(OpCode.CYCLE_END)
            end_jumps.append(self.emit(OpCode.JUMP, None))
        end = len(self.instructions)
        for jump in end_jumps:
            self.patch(jump, end)

    def try_node(self, node: TryNode) -> None:
        begin = self.emit(OpCode.TRY_BEGIN, ())
        for body_node in node.body:
            self.node(body_node)
        self.emit(OpCode.TRY_END)
        success_jump = self.emit(OpCode.JUMP, None)

        handlers: list[tuple[str | None, int]] = []
        end_jumps: list[int] = []
        for handler in node.handlers:
            handlers.append((_handler_type_name(handler), len(self.instructions)))
            for handler_node in handler.body:
                self.node(handler_node)
            end_jumps.append(self.emit(OpCode.JUMP, None))

        end = len(self.instructions)
        self.patch(success_jump, end)
        for jump in end_jumps:
            self.patch(jump, end)
        self.instructions[begin] = Instruction(OpCode.TRY_BEGIN, tuple(handlers))

    def while_node(self, node: WhileNode) -> None:
        if node.params is not None:
            condition = FunctionNode(
                params=node.params,
                body=node.condition,
                location=node.location,
            )
            body = FunctionNode(
                params=node.params,
                body=node.body,
                location=node.location,
            )
            condition_code = _compile_function_node(condition, "while.condition")
            body_code = _compile_function_node(
                body,
                "while.body",
                break_as_signal=True,
            )
            self.emit(OpCode.WHILE, (condition_code, body_code, len(node.params)))
            return
        loop_start = len(self.instructions)
        self.loops.append(_LoopPatch([]))
        for condition_node in node.condition:
            self.node(condition_node)
        jump_to_end = self.emit(OpCode.JUMP_IF_FALSE, None)
        for body_node in node.body:
            self.node(body_node)
        self.emit(OpCode.JUMP, loop_start)
        loop_end = len(self.instructions)
        self.patch(jump_to_end, loop_end)
        loop = self.loops.pop()
        for jump in loop.break_jumps:
            self.patch(jump, loop_end)

    def break_node(self, node: BreakNode) -> None:
        if self.break_as_signal:
            for value in node.values:
                self.node(value)
            self.emit(OpCode.LOOP_BREAK)
            return
        if not self.loops:
            self.unsupported(node, "break outside a loop")
        for value in node.values:
            self.node(value)
        self.loops[-1].break_jumps.append(self.emit(OpCode.JUMP, None))

    def emit(self, op: OpCode, arg: object = None) -> int:
        self.instructions.append(Instruction(op, arg))
        return len(self.instructions) - 1

    def patch(self, index: int, target: int) -> None:
        instruction = self.instructions[index]
        self.instructions[index] = Instruction(instruction.op, target)

    def patch_match(self, index: int, target: int) -> None:
        instruction = self.instructions[index]
        pattern, _ = instruction.arg
        self.instructions[index] = Instruction(instruction.op, (pattern, target))

    def unsupported(self, node: ASTNode, feature: str) -> NoReturn:
        location = ""
        if node.location is not None:
            location = f" at {node.location.line}:{node.location.column}"
        raise CompileError(f"cannot compile {feature}{location}")


def compile_program(nodes: list[TypedNode]) -> Program:
    """Compile analysed typed AST nodes to bytecode."""
    if not all(isinstance(node, TypedNode) for node in nodes):
        raise CompileError("compile_program expects analysed TypedNode values")
    compiler = _Compiler()
    return Program(compiler.compile_function(tuple(nodes), name="<main>"))


def _compile_function_value(
    node: FunctionNode | TypedNode,
    name: str | None = None,
) -> FunctionCode | FunctionSetCode:
    typed = node if isinstance(node, TypedFunctionNode) else None
    if typed is not None and typed.overloads:
        ast = _function_ast(node)
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


def _compile_function_node(
    node: FunctionNode | TypedNode,
    name: str | None = None,
    *,
    break_as_signal: bool = False,
    return_as_signal: bool = False,
) -> FunctionCode:
    ast = _function_ast(node)
    params = ()
    if ast.params is not None:
        params = tuple(
            f"_{index}" if param.name is None else param.name.text
            for index, param in enumerate(ast.params)
        )
    return _Compiler(
        break_as_signal=break_as_signal,
        return_as_signal=return_as_signal,
    ).compile_function(
        ast.body,
        params=params,
        name=name,
        cycle_params=bool(ast.params),
        element_tags=_function_element_tag_names(node),
        recursive=_function_is_recursive(ast),
    )


def _should_pop_statement_result(node: ASTNode | TypedNode) -> bool:
    ast = _unwrap(node)
    return (
        isinstance(ast, ForNode)
        and isinstance(node, TypedNode)
        and isinstance(node.typ, NoneTypeNode)
    )


def _compile_function_overload(
    ast: FunctionNode,
    overload: FunctionOverloadTyping,
    name: str | None,
) -> FunctionCode:
    typ = overload.typ
    if not isinstance(typ, FunctionType):
        raise CompileError(
            f"cannot compile function overload from {type(typ).__name__}"
        )
    return _Compiler().compile_function(
        overload.body,
        params=(*_overload_param_names(ast, overload), *_static_param_names(overload)),
        name=name,
        cycle_params=bool(ast.params),
        element_tags=_function_element_tag_names(overload.typ),
        recursive=_function_is_recursive(ast),
        multi=_overload_is_multi(overload),
        dispatch_types=_overload_dispatch_types(overload),
        return_tags=_function_return_tags(typ),
    )


def _function_param_names(ast: FunctionNode, arity: int) -> tuple[str, ...]:
    if ast.params is None:
        return tuple(f"_{index}" for index in range(arity))
    return tuple(
        f"_{index}" if param.name is None else param.name.text
        for index, param in enumerate(ast.params)
    )


def _overload_param_names(
    ast: FunctionNode,
    overload: FunctionOverloadTyping,
) -> tuple[str, ...]:
    source = overload.overload
    typ = overload.typ
    if isinstance(source, Overload) and source.param_names:
        return tuple(
            f"_{index}" if name is None else name.text
            for index, name in enumerate(source.param_names)
        )
    if not isinstance(typ, FunctionType) or typ.params is None:
        return ()
    return _function_param_names(ast, len(typ.params))


def _static_param_names(overload: FunctionOverloadTyping) -> tuple[str, ...]:
    source = overload.overload
    if source is None:
        return ()
    names: set[str] = set()
    if isinstance(source, Overload):
        for param in source.params:
            names.update(_rank_var_names_in_type(param))
        for ret in source.returns:
            names.update(_rank_var_names_in_type(ret))
        for node in source.where_clause:
            if isinstance(node, SetVariableNode):
                names.add(node.name.text)
    return tuple(sorted(names))


def _overload_is_multi(overload: FunctionOverloadTyping) -> bool:
    source = overload.overload
    return isinstance(source, Overload) and source.is_multi


def _overload_dispatch_types(
    overload: FunctionOverloadTyping,
) -> tuple[str | None, ...]:
    source = overload.overload
    if not isinstance(source, Overload):
        return ()
    return tuple(_runtime_dispatch_type(param) for param in source.params)


def _function_return_tags(
    typ: FunctionType,
) -> tuple[tuple[DataTag, ...], ...]:
    if typ.returns is None:
        return ()
    return tuple(_top_level_runtime_tags(ret) for ret in typ.returns)


def _top_level_runtime_tags(typ: Type) -> tuple[DataTag, ...]:
    typ = normalize(typ)
    if isinstance(typ, TaggedType):
        return tuple(sorted(tag for tag in typ.tags if tag.depth == 0))
    return ()


def _runtime_dispatch_type(typ: Type) -> str | None:
    typ = normalize(typ)
    if isinstance(typ, (TaggedType, ExactType, AtomicType)):
        return _runtime_dispatch_type(typ.inner)
    if isinstance(typ, NominalType):
        return show(typ)
    return None


def _rank_var_names_in_type(typ: Type) -> set[str]:
    typ = normalize(typ)
    names: set[str] = set()
    if isinstance(typ, CollectionType):
        if isinstance(typ.rank, RankVariable):
            names.add(typ.rank.name)
        names.update(_rank_var_names_in_type(typ.base))
    elif isinstance(typ, NominalType):
        for arg in typ.args:
            names.update(_rank_var_names_in_type(arg))
    elif isinstance(typ, FunctionType):
        if typ.params is None or typ.returns is None:
            return names
        for item in typ.params + typ.returns:
            names.update(_rank_var_names_in_type(item))
    elif isinstance(typ, TupleType):
        for item in typ.params:
            names.update(_rank_var_names_in_type(item))
    elif isinstance(typ, VariadicTupleType):
        for item in typ.items:
            names.update(_rank_var_names_in_type(item.typ))
    elif isinstance(typ, UnionType):
        for item in typ.items:
            names.update(_rank_var_names_in_type(item))
    elif isinstance(typ, IntersectionType):
        for item in typ.items:
            names.update(_rank_var_names_in_type(item))
    elif isinstance(typ, (TaggedType, ExactType, AtomicType)):
        names.update(_rank_var_names_in_type(typ.inner))
    return names


def _compiled_function_arity(code: FunctionCode | FunctionSetCode) -> int:
    if isinstance(code, FunctionCode):
        return len(code.params)
    if not code.overloads:
        return 0
    return len(code.overloads[0].params)


def _unfold_state_arity(node: UnfoldNode, typed_node: TypedNode | None) -> int:
    if isinstance(typed_node, TypedUnfoldNode):
        return typed_node.state_arity
    if node.params is not None:
        return len(node.params)
    raise CompileError("unfold without explicit parameters requires typed analysis")


def _function_ast(node: FunctionNode | TypedNode) -> FunctionNode:
    ast = _unwrap(node)
    if isinstance(ast, DefineNode):
        ast = ast.function
    if not isinstance(ast, FunctionNode):
        raise CompileError(f"cannot compile function from {type(ast).__name__}")
    return ast


def _max_break_values(nodes: tuple[ASTNode, ...]) -> int:
    return max((_node_max_break_values(node) for node in nodes), default=0)


def _node_max_break_values(node: ASTNode) -> int:
    best = 0
    if isinstance(node, BreakNode):
        best = len(node.values)
    for value in node.__dict__.values():
        if isinstance(value, ASTNode):
            best = max(best, _node_max_break_values(value))
        if isinstance(value, tuple):
            best = max(best, _tuple_max_break_values(value))
    return best


def _tuple_max_break_values(values: tuple[object, ...]) -> int:
    best = 0
    for value in values:
        if isinstance(value, ASTNode):
            best = max(best, _node_max_break_values(value))
        if isinstance(value, tuple):
            best = max(best, _tuple_max_break_values(value))
    return best


def _function_element_tag_names(
    node: FunctionNode | TypedNode | Type,
) -> tuple[str, ...]:
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
    return any(
        isinstance(annotation, AnnotationNode)
        and annotation.name.text == "recursive"
        for annotation in ast.annotations
    )


def _definition_has_annotation(definition: DefineNode, name: str) -> bool:
    return any(
        isinstance(annotation, AnnotationNode)
        and annotation.name.text == name
        for annotation in definition.annotations
    )


def _object_runtime_metadata(
    name: str,
    annotations: tuple[ASTNode, ...],
    definitions: tuple[DefineNode, ...],
) -> tuple[str | None, str | None, str | None, str | None, str | None, tuple[str, ...]]:
    destructor_name = None
    pop_name = None
    dup_name = None
    dup_error = None
    for definition in definitions:
        if definition.name.text == f"~{name.rsplit('.', 1)[-1]}":
            destructor_name = f"{name}::{definition.name.text}"
        elif definition.name.text == "pop":
            pop_name = f"{name}::pop"
        elif definition.name.text == "dup":
            dup_name = f"{name}::dup"
            dup_error = _annotation_message(definition.annotations, "error")
    mustcall_mode, mustcall_methods = _mustcall_annotation_metadata(annotations)
    return (
        destructor_name,
        pop_name,
        dup_name,
        dup_error,
        mustcall_mode,
        mustcall_methods,
    )


def _mustcall_annotation_metadata(
    annotations: tuple[ASTNode, ...],
) -> tuple[str | None, tuple[str, ...]]:
    for annotation in annotations:
        if not isinstance(annotation, AnnotationNode):
            continue
        if annotation.name.text != "mustcall":
            continue
        kwargs = dict(annotation.kwargs)
        for key in ("all", "any"):
            value = kwargs.get(Symbol(key))
            methods = _string_list_literal(value)
            if methods is not None:
                return key, methods
    return None, ()


def _string_list_literal(value: ASTNode | None) -> tuple[str, ...] | None:
    if not isinstance(value, ListLiteralNode):
        return None
    methods: list[str] = []
    for item in value.items:
        if len(item) != 1 or not isinstance(item[0], StringLiteralNode):
            return None
        methods.append(item[0].value)
    return tuple(methods)


def _annotation_message(annotations: tuple[ASTNode, ...], name: str) -> str | None:
    for annotation in annotations:
        if not isinstance(annotation, AnnotationNode):
            continue
        if annotation.name.text != name:
            continue
        for arg in annotation.args:
            if isinstance(arg, StringLiteralNode):
                return arg.value
    return None


def _tupled_element_return_count(
    node: ElementNode,
    typed_node: TypedNode | None,
) -> int | None:
    if not any(
        isinstance(annotation, AnnotationNode)
        and annotation.name.text == "@@tupled"
        for annotation in node.annotations
    ):
        return None
    if isinstance(typed_node, TypedElementNode) and typed_node.overload is not None:
        return len(typed_node.overload.actual_returns)
    return None


def _unwrap(node: ASTNode | TypedNode) -> ASTNode:
    if isinstance(node, TypedNode):
        return node.node
    return node


def _symbol_runtime_name(symbol: Symbol) -> str:
    return symbol.dotted()


def _resolved_element_reference(
    node: TypedNode | None,
) -> ResolvedElementReference | None:
    if not isinstance(node, TypedElementNode):
        return None
    if node.overload_index is None:
        return None
    ast = node.node
    if not isinstance(ast, ElementNode):
        return None
    runtime_name = _symbol_runtime_name(ast.name).removeprefix("*::")
    type_args = _resolved_constructor_type_args(ast, node)
    elements = runtime_elements()
    element = elements.get(runtime_name)
    if element is not None:
        if not 0 <= node.overload_index < len(element.definitions):
            return None
        definition = element.definitions[node.overload_index]
        if definition.implementation is None:
            raise CompileError(
                f"cannot compile static-only overload {node.overload_index} "
                f"of built-in element '{runtime_name}'"
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
        and Symbol(runtime_name) in {item.name for item in BUILTIN_ELEMENTS}
        and not type_args
    ):
        return None
    vectorised = bool(node.overload is not None and node.overload.vectorised)
    if (
        ast.name.text == "call"
        and not ast.name.namespace
        and node.call_overload_index is not None
    ):
        static_values = (node.call_overload_index,)
    else:
        static_values = _runtime_rank_values(node)
    multidispatch = bool(node.overload is not None and node.overload.multidispatch)
    vectorised_depths = (
        tuple(node.overload.vectorised_depths) if node.overload is not None else ()
    )
    arity_override = None
    consumed_override = None
    if (
        element is not None
        and node.overload is not None
        and len(node.overload.params)
        != len(element.definitions[node.overload_index].signature.params)
    ):
        arity_override = len(node.overload.params)
        consumed_override = node.overload.runtime_consumed_count
    return ResolvedElementReference(
        runtime_name,
        node.overload_index,
        vectorised=vectorised,
        vectorised_depths=vectorised_depths,
        type_args=type_args,
        static_values=static_values,
        arity_override=arity_override,
        consumed_override=consumed_override,
        multidispatch=multidispatch,
        extension=_compiled_element_extension(node.extension),
    )


def _compiled_element_extension(
    extension: TypedElementExtension | None,
) -> VectorExtensionReference | None:
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


def _runtime_rank_values(node: TypedElementNode) -> tuple[Decimal, ...]:
    if node.overload is None or not node.overload.rank_values:
        return ()
    return tuple(
        Decimal(value)
        for _, value in node.overload.rank_values
    )


def _resolved_constructor_type_args(
    ast: ElementNode,
    node: TypedElementNode,
) -> tuple[str, ...]:
    if node.overload is None or not node.overload.actual_returns:
        return ()
    returned = node.overload.actual_returns[0]
    if not isinstance(returned, NominalType):
        return ()
    if not returned.args:
        return ()
    return tuple(show(arg) for arg in returned.args)


def _index_spec(
    selectors: tuple[IndexSelector, ...],
    spread: bool,
) -> tuple[tuple[int, int, int, int], int]:
    return (
        tuple(
            (
                int(selector.is_slice),
                int(bool(selector.start)),
                int(bool(selector.stop)),
                int(bool(selector.step)),
            )
            for selector in selectors
        ),
        int(spread),
    )


def _stack_shuffle_spec(
    node: StackShuffleNode,
) -> tuple[str, tuple[str | None, ...], tuple[str, ...]]:
    return (
        node.mode.text,
        tuple(None if label is None else label.text for label in node.prestack),
        tuple(label.text for label in node.poststack),
    )


def _literal_expression_value(nodes: tuple[ASTNode, ...]) -> object:
    if len(nodes) != 1:
        raise CompileError("object and enum default values must be literal values")
    node = nodes[0]
    match node:
        case NumberLiteralNode(value):
            return _number(value, node)
        case StringLiteralNode(value):
            return value
        case StringInterpolationNode():
            raise CompileError(
                "object and enum default values must be literal values"
            )
        case _:
            raise CompileError(
                "object and enum default values must be literal values"
            )


def _number(value: str, node: ASTNode) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        location = ""
        if node.location is not None:
            location = f" at {node.location.line}:{node.location.column}"
        message = f"cannot compile numeric literal {value!r}{location}"
        raise CompileError(message) from exc


def _compile_case_patterns(
    patterns: tuple[MatchPatternNode, ...],
) -> tuple[object, ...]:
    return tuple(_compile_match_pattern(pattern) for pattern in patterns)


def _match_arity(node: MatchNode) -> int | None:
    if not node.cases:
        return None
    arities = {len(case.patterns) for case in node.cases}
    if len(arities) != 1:
        return None
    return next(iter(arities))


def _handler_type_name(handler: TryHandlerNode) -> str | None:
    if handler.typ is None:
        return None
    typ = handler.typ
    if isinstance(typ, NominalType):
        return typ.name.text
    raise CompileError(f"cannot compile handler for non-nominal type {typ}")


def _compile_match_pattern(pattern: MatchPatternNode) -> object:
    match pattern:
        case LiteralPatternNode(value):
            return ("literal", _literal_pattern_value(value))
        case ExpressionPatternNode(expression):
            return ("literal", _literal_expression_value(expression))
        case GuardPatternNode(condition):
            return ("guard", _compile_guard(condition))
        case WildcardPatternNode():
            return ("wildcard",)
        case RestPatternNode(name):
            return ("rest", None if name is None else name.text)
        case BindingPatternNode(name, inner):
            if isinstance(inner, RestPatternNode):
                return ("rest", name.text)
            return ("bind", name.text, _compile_match_pattern(inner))
        case OrPatternNode(options):
            return ("or", tuple(_compile_match_pattern(option) for option in options))
        case ListPatternNode(items):
            return ("list", tuple(_compile_match_pattern(item) for item in items))
        case TypePatternNode(typ, name, fields, guard):
            return (
                "type",
                None if typ is None else _cast_type_spec(typ),
                None if name is None else name.text,
                tuple(_compile_match_pattern(field) for field in fields),
                _compile_guard(guard) if guard else None,
            )
        case _:
            raise CompileError(f"cannot compile match pattern {pattern!r}")


def _literal_pattern_value(node: ASTNode) -> object:
    match node:
        case NumberLiteralNode(value):
            return _number(value, node)
        case StringLiteralNode(value):
            return value
        case StringInterpolationNode():
            raise CompileError(f"cannot compile interpolated string pattern {node!r}")
        case _:
            raise CompileError(f"cannot compile literal pattern {node!r}")


def _compile_guard(condition: tuple[ASTNode, ...]) -> FunctionCode:
    return _Compiler().compile_function(condition, params=("_",), name="<match guard>")


def _type_pattern_name(typ: object) -> str:
    from valiance.types import NominalType, NoneTypeNode, normalize

    typ = normalize(typ)
    if isinstance(typ, NoneTypeNode):
        return "None"
    if not isinstance(typ, NominalType):
        raise CompileError(f"cannot compile match type pattern {typ}")
    return typ.name.text


def _cast_type_spec(typ: Type) -> object:
    from valiance.types import VarType

    typ = normalize(typ)
    if isinstance(typ, NoneTypeNode):
        return ("none",)
    if isinstance(typ, VarType):
        return ("var", typ.name)
    if isinstance(typ, NominalType):
        return ("nominal", typ.name.text)
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
    if isinstance(typ, (TaggedType, ExactType, AtomicType)):
        return _cast_type_spec(typ.inner)
    raise CompileError(f"cannot compile checked cast to {typ}")


def _is_default_case(patterns: tuple[MatchPatternNode, ...]) -> bool:
    return bool(patterns) and all(_is_default_pattern(pattern) for pattern in patterns)


def _is_default_pattern(pattern: MatchPatternNode) -> bool:
    return isinstance(pattern, (WildcardPatternNode, RestPatternNode)) or (
        isinstance(pattern, TypePatternNode) and pattern.typ is None
    )
