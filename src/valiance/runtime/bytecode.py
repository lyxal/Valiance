"""Small stack-machine bytecode model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class OpCode(Enum):
    """Bytecode operations understood by the Valiance VM."""

    PUSH_CONST = "push_const"
    LOAD_VAR = "load_var"
    STORE_VAR = "store_var"
    LOAD_ELEMENT = "load_element"
    MAKE_FUNCTION = "make_function"
    CALL = "call"
    BUILD_LIST = "build_list"
    BUILD_TUPLE = "build_tuple"
    BUILD_RECORD = "build_record"
    BUILD_DICT = "build_dict"
    GET_FIELD = "get_field"
    JUMP = "jump"
    JUMP_IF_FALSE = "jump_if_false"
    POP = "pop"
    RETURN = "return"


@dataclass(frozen=True, slots=True)
class Instruction:
    """A single bytecode operation."""

    op: OpCode
    arg: Any = None


@dataclass(frozen=True, slots=True)
class FunctionCode:
    """Executable bytecode for a function or top-level program."""

    instructions: tuple[Instruction, ...]
    params: tuple[str, ...] = ()
    name: str | None = None


@dataclass(frozen=True, slots=True)
class Program:
    """A compiled Valiance program."""

    main: FunctionCode
