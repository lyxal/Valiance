"""Small stack-machine bytecode model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Any

from valiance.vtypes import DataTag, UnionDispatchBranch


class OpCode(Enum):
    """Bytecode operations understood by the Valiance VM."""

    PUSH_CONST = "push_const"
    LOAD_VAR = "load_var"
    LOAD_VAR_BORROW = "load_var_borrow"
    STORE_VAR = "store_var"
    LOAD_ELEMENT = "load_element"
    MAKE_FUNCTION = "make_function"
    APPLY_DISPATCH_PLAN = "apply_dispatch_plan"
    CALL = "call"
    CALL_RESOLVED_ELEMENT = "call_resolved_element"
    CHECK_CAST = "check_cast"
    TRY_CAST = "try_cast"
    CANONICALIZE_TAGS = "canonicalize_tags"
    BUILD_LIST = "build_list"
    BUILD_STRING = "build_string"
    BUILD_TUPLE = "build_tuple"
    BUILD_RECORD = "build_record"
    BUILD_DICT = "build_dict"
    ISOLATE_STACK_BEGIN = "isolate_stack_begin"
    ISOLATE_STACK_END = "isolate_stack_end"
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
    POP_N = "pop_n"
    RETURN = "return"


@dataclass(frozen=True, slots=True)
class Instruction:
    """A single bytecode operation."""

    op: OpCode
    arg: Any = None


@dataclass(frozen=True, slots=True)
class IndexSelectorSpec:
    """Static stack shape for one index or slice selector."""

    is_slice: bool
    has_start: bool
    has_stop: bool
    has_step: bool

    @property
    def value_count(self) -> int:
        """Return how many runtime stack values encode this selector."""
        return int(self.has_start) + int(self.has_stop) + int(self.has_step)


@dataclass(frozen=True, slots=True)
class IndexOperationSpec:
    """Named bytecode payload for an indexed read or write."""

    selectors: tuple[IndexSelectorSpec, ...]
    spread: bool = False
    grouped_update: bool = False

    @property
    def value_count(self) -> int:
        """Return the total number of selector values consumed from the stack."""
        return sum(selector.value_count for selector in self.selectors)


@lru_cache(maxsize=None)
def decode_stack_shuffle_spec(
    value: object,
    error_type: type[Exception] = ValueError,
) -> tuple[
    str,
    tuple[str | None, ...],
    tuple[str, ...],
    tuple[int, ...] | None,
]:
    """Validate and decode one stack-shuffle instruction payload."""
    if not isinstance(value, tuple) or len(value) != 3:
        raise error_type(f"invalid stack shuffle spec {value!r}")
    mode, prestack, poststack = value
    if mode not in {"copy", "move"}:
        raise error_type(f"invalid stack shuffle mode {mode!r}")
    if not isinstance(prestack, tuple) or not all(
        label is None or isinstance(label, str) for label in prestack
    ):
        raise error_type(f"invalid stack shuffle prestack {prestack!r}")
    if not isinstance(poststack, tuple) or not all(
        isinstance(label, str) for label in poststack
    ):
        raise error_type(f"invalid stack shuffle poststack {poststack!r}")
    labels = {label for label in prestack if label is not None}
    if any(label not in labels for label in poststack):
        raise error_type(
            f"stack shuffle poststack contains a label absent from {prestack!r}"
        )
    permutation = None
    if (
        mode == "move"
        and len(prestack) == len(poststack)
        and all(label is not None for label in prestack)
        and len(set(prestack)) == len(prestack)
        and set(prestack) == set(poststack)
    ):
        positions = {label: index for index, label in enumerate(prestack)}
        permutation = tuple(positions[label] for label in poststack)
    return mode, prestack, poststack, permutation


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
    return_tags: tuple[tuple[DataTag, ...], ...] = ()
    return_tag_specs: tuple[object, ...] = ()
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
    accepts_stack_inputs: bool = False
    element_tags: tuple[str, ...] = ()
    recursive: bool = False
    multi: bool = False
    dispatch_types: tuple[str | None, ...] = ()
    return_count: int | None = None
    return_tags: tuple[tuple[DataTag, ...], ...] = ()
    return_tag_specs: tuple[object, ...] = ()
    return_collection_ranks: tuple[int | None, ...] = ()
    param_collection_ranks: tuple[int | None, ...] = ()


@dataclass(frozen=True, slots=True)
class FunctionSetCode:
    """Executable bytecode for every statically analysed overload of a function."""

    overloads: tuple[FunctionCode, ...]
    dispatch_plan: tuple[UnionDispatchBranch, ...] = ()


@dataclass(frozen=True, slots=True)
class ObjectConstructorReference:
    """Named payload for ``MAKE_OBJECT_CONSTRUCTOR`` bytecode."""

    type_name: str
    fields: tuple[str, ...]
    required: tuple[str, ...]
    defaults: tuple[tuple[str, Any], ...]
    runtime_metadata: Any = None
    initializer: FunctionCode | FunctionSetCode | None = None


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
    tag_parents: tuple[tuple[str, str], ...] = ()
