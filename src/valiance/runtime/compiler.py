"""AST to bytecode compiler."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import NoReturn

from valiance.asts import (
    ArrayLiteralNode,
    ASTNode,
    BreakNode,
    DefineNode,
    DictLiteralNode,
    ElementNode,
    FieldAccessNode,
    ForNode,
    FunctionNode,
    GetVariableNode,
    IfNode,
    ListLiteralNode,
    NumberLiteralNode,
    RecordLiteralNode,
    ReturnNode,
    SetVariableNode,
    StringLiteralNode,
    TagApplicationNode,
    TupleLiteralNode,
    TypedFunctionNode,
    TypedNode,
    WhileNode,
)
from valiance.runtime.bytecode import FunctionCode, Instruction, OpCode, Program


class CompileError(Exception):
    """Raised when AST nodes cannot yet be lowered to bytecode."""


@dataclass(slots=True)
class _LoopPatch:
    break_jumps: list[int]


class _Compiler:
    def __init__(self) -> None:
        self.instructions: list[Instruction] = []
        self.loops: list[_LoopPatch] = []

    def compile_function(
        self,
        body: tuple[ASTNode | TypedNode, ...],
        *,
        params: tuple[str, ...] = (),
        name: str | None = None,
        cycle_params: bool = False,
    ) -> FunctionCode:
        for node in body:
            self.node(node)
        self.emit(OpCode.RETURN)
        return FunctionCode(tuple(self.instructions), params, name, cycle_params)

    def node(self, node: ASTNode | TypedNode) -> None:
        typed_node = node if isinstance(node, TypedNode) else None
        node = _unwrap(node)
        match node:
            case NumberLiteralNode(value):
                self.emit(OpCode.PUSH_CONST, _number(value, node))
            case StringLiteralNode(value):
                self.emit(OpCode.PUSH_CONST, value)
            case GetVariableNode(name):
                self.emit(OpCode.LOAD_VAR, name.text)
            case SetVariableNode(name):
                self.emit(OpCode.STORE_VAR, name.text)
            case ElementNode(name):
                self.emit(OpCode.LOAD_ELEMENT, name.text)
                self.emit(OpCode.CALL)
            case TagApplicationNode():
                pass
            case FunctionNode():
                self.emit(
                    OpCode.MAKE_FUNCTION,
                    _compile_function_node(typed_node or node),
                )
            case DefineNode(name, function):
                self.emit(
                    OpCode.MAKE_FUNCTION,
                    _compile_function_node(typed_node or function, name.text),
                )
                self.emit(OpCode.STORE_VAR, name.text)
            case ListLiteralNode(items) | ArrayLiteralNode(items):
                self.collection(items, OpCode.BUILD_LIST)
            case TupleLiteralNode(items):
                self.collection(items, OpCode.BUILD_TUPLE)
            case RecordLiteralNode(fields):
                keys = []
                for key, expr in fields:
                    self.expression(expr)
                    keys.append(key.text)
                self.emit(OpCode.BUILD_RECORD, tuple(keys))
            case DictLiteralNode(entries):
                for key_expr, value_expr in entries:
                    self.expression(key_expr)
                    self.expression(value_expr)
                self.emit(OpCode.BUILD_DICT, len(entries))
            case FieldAccessNode(name):
                self.emit(OpCode.GET_FIELD, name.text)
            case IfNode():
                self.if_node(node)
            case WhileNode():
                self.while_node(node)
            case ForNode():
                self.unsupported(node, "foreach loops")
            case BreakNode():
                self.break_node(node)
            case ReturnNode(values):
                for value in values:
                    self.node(value)
                self.emit(OpCode.RETURN)
            case _:
                self.unsupported(node, type(node).__name__)

    def expression(self, nodes: tuple[ASTNode, ...]) -> None:
        for node in nodes:
            self.node(node)

    def collection(
        self,
        items: tuple[tuple[ASTNode, ...], ...],
        op: OpCode,
    ) -> None:
        for item in items:
            self.expression(item)
        self.emit(op, len(items))

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

    def while_node(self, node: WhileNode) -> None:
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


def _compile_function_node(
    node: FunctionNode | TypedNode,
    name: str | None = None,
) -> FunctionCode:
    typed = node if isinstance(node, TypedFunctionNode) else None
    ast = _unwrap(node)
    if isinstance(ast, DefineNode):
        ast = ast.function
    if not isinstance(ast, FunctionNode):
        raise CompileError(f"cannot compile function from {type(ast).__name__}")
    params = ()
    if ast.params is not None:
        params = tuple(
            f"_{index}" if param.name is None else param.name.text
            for index, param in enumerate(ast.params)
        )
    body: tuple[ASTNode | TypedNode, ...] = ast.body
    if typed is not None and typed.overloads:
        body = typed.overloads[0].body
    return _Compiler().compile_function(
        body,
        params=params,
        name=name,
        cycle_params=bool(ast.params),
    )


def _unwrap(node: ASTNode | TypedNode) -> ASTNode:
    if isinstance(node, TypedNode):
        return node.node
    return node


def _number(value: str, node: ASTNode) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        location = ""
        if node.location is not None:
            location = f" at {node.location.line}:{node.location.column}"
        message = f"cannot compile numeric literal {value!r}{location}"
        raise CompileError(message) from exc
