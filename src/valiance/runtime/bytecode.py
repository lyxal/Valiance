"""Small stack-machine bytecode model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from valiance.types import DataTag, UnionDispatchBranch


class OpCode(Enum):
    """Bytecode operations understood by the Valiance VM."""

    PUSH_CONST = "push_const"
    LOAD_VAR = "load_var"
    STORE_VAR = "store_var"
    LOAD_ELEMENT = "load_element"
    MAKE_FUNCTION = "make_function"
    CALL = "call"
    CALL_RESOLVED_ELEMENT = "call_resolved_element"
    CHECK_CAST = "check_cast"
    BUILD_LIST = "build_list"
    BUILD_STRING = "build_string"
    BUILD_TUPLE = "build_tuple"
    BUILD_RECORD = "build_record"
    BUILD_DICT = "build_dict"
    MAKE_OBJECT_CONSTRUCTOR = "make_object_constructor"
    MAKE_ENUM_MEMBER = "make_enum_member"
    GET_FIELD = "get_field"
    SET_FIELD = "set_field"
    GET_INDEX = "get_index"
    SET_INDEX = "set_index"
    JUMP = "jump"
    JUMP_IF_FALSE = "jump_if_false"
    JUMP_IF_MATCH = "jump_if_match"
    MATCH_ERROR = "match_error"
    ASSERT_TRUE = "assert_true"
    WRAP_ASSERT_ERROR = "wrap_assert_error"
    UNFOLD = "unfold"
    WHILE = "while"
    TRY_BEGIN = "try_begin"
    TRY_END = "try_end"
    PANIC = "panic"
    TRY_UNWRAP = "try_unwrap"
    VALIDATE_TAG = "validate_tag"
    STACK_SHUFFLE = "stack_shuffle"
    CYCLE_BEGIN = "cycle_begin"
    CYCLE_END = "cycle_end"
    SOURCE_ARGS = "source_args"
    FOREACH = "foreach"
    LOOP_BREAK = "loop_break"
    RETURN_SIGNAL = "return_signal"
    POP = "pop"
    RETURN = "return"


@dataclass(frozen=True, slots=True)
class Instruction:
    """A single bytecode operation."""

    op: OpCode
    arg: Any = None


@dataclass(frozen=True, slots=True)
class ResolvedElementReference:
    """Named payload for CALL_RESOLVED_ELEMENT.

    Compiler, serializer, and VM code should use these fields instead of
    positional layouts.
    """

    name: str
    overload_index: int
    vectorised: bool = False
    vectorised_depths: tuple[int, ...] = ()
    vectorised_target_ranks: tuple[int | None, ...] = ()
    return_collection_ranks: tuple[int | None, ...] = ()
    type_args: tuple[str, ...] = ()
    static_values: tuple[Any, ...] = ()
    arity_override: int | None = None
    consumed_override: int | None = None
    multidispatch: bool = False
    extension: VectorExtensionReference | None = None


@dataclass(frozen=True, slots=True)
class FunctionCode:
    """Executable bytecode for a function or top-level program."""

    instructions: tuple[Instruction, ...]
    params: tuple[str, ...] = ()
    name: str | None = None
    cycle_params: bool = False
    element_tags: tuple[str, ...] = ()
    recursive: bool = False
    multi: bool = False
    dispatch_types: tuple[str | None, ...] = ()
    return_tags: tuple[tuple[DataTag, ...], ...] = ()
    return_collection_ranks: tuple[int | None, ...] = ()


@dataclass(frozen=True, slots=True)
class FunctionSetCode:
    """Executable bytecode for every statically analysed overload of a function."""

    overloads: tuple[FunctionCode, ...]
    dispatch_plan: tuple[UnionDispatchBranch, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtensionRuleReference:
    """Compiled substitution rule for one present/missing argument pattern."""

    presence: tuple[bool, ...]
    function: FunctionCode | FunctionSetCode


@dataclass(frozen=True, slots=True)
class VectorExtensionReference:
    """Compiled length-mismatch behavior for a vectorised element call."""

    default: FunctionCode | FunctionSetCode | None = None
    rules: tuple[ExtensionRuleReference, ...] = ()
    selector: FunctionCode | FunctionSetCode | None = None


@dataclass(frozen=True, slots=True)
class Program:
    """A compiled Valiance program."""

    main: FunctionCode
