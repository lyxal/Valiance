"""Deterministic fuzz targets for Valiance's compiler and runtime boundaries.

The fuzzers intentionally use only the Python standard library so they can run in
an offline checkout.  Every case receives an independently derived random seed,
which makes a failure reproducible by target, base seed, and iteration number.
"""

from __future__ import annotations

import gc
import hashlib
import random
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from itertools import permutations
from pathlib import Path
from typing import Any

from valiance.analysis import (
    Analyser,
    AnalysisBranch,
    BranchSet,
    default_environment,
)
from valiance.asts import ASTNode, ElementNode, pretty_ast
from valiance.parsing import LexError, ParseError, lex, parse
from valiance.parsing.lexer import TokenKind
from valiance.runtime import (
    BytecodeFormatError,
    RuntimeError as ValianceRuntimeError,
    compile_program,
    dumps,
    loads,
    run,
)
from valiance.runtime.bytecode import (
    ExtensionRuleReference,
    FunctionCode,
    FunctionSetCode,
    Instruction,
    ObjectConstructorReference,
    OpCode,
    Program,
    ResolvedElementReference,
    VectorExtensionReference,
)
from valiance.symbols import Symbol
from valiance.types import (
    AnonymousTrait,
    AnonymousTraitRequirement,
    AtLeastArray,
    AtLeastList,
    Boolean,
    Context,
    DataTag,
    Exact,
    ExactArray,
    Environment,
    ExactList,
    Field,
    Fn,
    GenericConstraint,
    I,
    Integer,
    N,
    Never,
    NoneType,
    Number,
    OKType,
    Overload,
    Real,
    Result,
    Row,
    RuntimeTypePattern,
    Some,
    String,
    TagKind,
    Tagged,
    Tup,
    Type,
    TypeStack,
    U,
    UnionDispatchBranch,
    V,
    Variance,
    _combine_all,
    _solve,
    _substitute,
    apply_overload,
    assignable,
    compatible,
    merge_stacks,
    merge_types,
    normalize,
    optional,
    resolve_overload_result,
    same,
    show,
    subtype,
)

DEFAULT_SEED = 0x5A17_2026
DEFAULT_ITERATIONS = 1_000


@dataclass(frozen=True, slots=True)
class FuzzConfig:
    """Shared deterministic settings for one fuzz target run."""

    seed: int = DEFAULT_SEED
    iterations: int = DEFAULT_ITERATIONS
    start: int = 0
    max_depth: int = 2
    max_source_length: int = 192


@dataclass(frozen=True, slots=True)
class FuzzStats:
    """Summary returned after a fuzz target completes."""

    target: str
    seed: int
    start: int
    iterations: int


class FuzzFailure(AssertionError):
    """A fuzz failure carrying the exact case needed for reproduction."""

    def __init__(
        self,
        target: str,
        config: FuzzConfig,
        iteration: int,
        case: object,
        cause: BaseException,
    ) -> None:
        self.target = target
        self.config = config
        self.iteration = iteration
        self.case = case
        self.cause = cause
        command = (
            "python -m tools.fuzz "
            f"--target {target} --seed {config.seed} --start {iteration} "
            "--iterations 1 "
            f"--max-depth {config.max_depth} "
            f"--max-source-length {config.max_source_length}"
        )
        super().__init__(
            f"{target} fuzz failure at iteration {iteration} with seed "
            f"{config.seed}\ncase={case!r}\ncause={cause!r}\nreproduce: {command}"
        )


class _GeneratedCaseFailure(Exception):
    """Internal wrapper that preserves the generated input for reporting."""

    def __init__(self, case: object, cause: BaseException) -> None:
        self.case = case
        self.cause = cause
        super().__init__(str(cause))


@dataclass(frozen=True, slots=True)
class _ProgramCase:
    source: str
    expected: list[Any]


Target = Callable[[random.Random, int, FuzzConfig], object]


def _case_rng(seed: int, target: str, iteration: int) -> random.Random:
    """Return a stable per-case RNG independent of execution order."""
    material = f"{seed}:{target}:{iteration}".encode()
    digest = hashlib.blake2b(material, digest_size=16).digest()
    return random.Random(int.from_bytes(digest, "big"))


def run_target(name: str, config: FuzzConfig) -> FuzzStats:
    """Run one named fuzz target and raise :class:`FuzzFailure` on failure."""
    try:
        target = TARGETS[name]
    except KeyError as exc:
        raise ValueError(f"unknown fuzz target {name!r}") from exc

    if config.iterations < 0 or config.start < 0:
        raise ValueError("fuzz start and iteration counts must be non-negative")
    if config.max_depth < 0 or config.max_source_length < 0:
        raise ValueError("fuzz limits must be non-negative")

    for iteration in range(config.start, config.start + config.iterations):
        rng = _case_rng(config.seed, name, iteration)
        case: object = "<case generation did not complete>"
        try:
            case = target(rng, iteration, config)
        except FuzzFailure:
            raise
        except _GeneratedCaseFailure as exc:
            raise FuzzFailure(
                name, config, iteration, exc.case, exc.cause
            ) from exc.cause
        except BaseException as exc:
            raise FuzzFailure(name, config, iteration, case, exc) from exc
        if (iteration - config.start + 1) % 512 == 0:
            gc.collect()

    return FuzzStats(name, config.seed, config.start, config.iterations)


def run_targets(names: list[str], config: FuzzConfig) -> list[FuzzStats]:
    """Run several fuzz targets with the same deterministic configuration."""
    return [run_target(name, config) for name in names]


def _random_source(rng: random.Random, maximum: int) -> str:
    chunks = (
        "a",
        "name",
        "positive?",
        "\\element",
        "0",
        "-1",
        "1e2",
        "3i4",
        " ",
        "\t",
        "\r",
        "\n",
        '"',
        "\\",
        "$",
        "${",
        "}",
        "(",
        ")",
        "[",
        "]",
        "{",
        "}",
        ",",
        ":",
        "::",
        ":=",
        "=",
        "=>",
        "->",
        ".",
        "|",
        "@",
        "'",
        "+",
        "++",
        "?",
        "#tag",
        "#!absent",
        "#? comment",
        "#/",
        "/#",
        "é",
        "λ",
        "🙂",
        "\x00",
    )
    pieces: list[str] = []
    remaining = rng.randint(0, maximum)
    while remaining:
        chunk = rng.choice(chunks)
        chunk = chunk[:remaining]
        pieces.append(chunk)
        remaining -= len(chunk)
    return "".join(pieces)


def _validate_tokens(source: str, tokens: list[Any]) -> None:
    if not tokens or tokens[-1].kind is not TokenKind.EOF:
        raise AssertionError("lexer did not terminate with EOF")
    if sum(token.kind is TokenKind.EOF for token in tokens) != 1:
        raise AssertionError("lexer emitted multiple EOF tokens")
    if tokens[-1].offset != len(source):
        raise AssertionError("EOF offset does not equal source length")

    previous_offset = -1
    for token in tokens:
        if token.offset < previous_offset:
            raise AssertionError("token offsets are not monotonic")
        if not 0 <= token.offset <= len(source):
            raise AssertionError("token offset is outside the source")
        if token.line < 1 or token.column < 1:
            raise AssertionError("token location is not one-based")
        previous_offset = token.offset


def _fuzz_lexer_parser(
    rng: random.Random,
    _iteration: int,
    config: FuzzConfig,
) -> object:
    source = _random_source(rng, config.max_source_length)
    try:
        try:
            tokens = lex(source)
        except LexError:
            return source

        _validate_tokens(source, tokens)
        try:
            program = parse(source)
        except (LexError, ParseError):
            return source

        if not isinstance(program, list) or not all(
            isinstance(node, ASTNode) for node in program
        ):
            raise AssertionError("parser returned a non-AST program")
        pretty_ast(program)
        return source
    except BaseException as exc:
        raise _GeneratedCaseFailure(source, exc) from exc


def _mutate_source(
    rng: random.Random,
    source: str,
    maximum: int,
) -> str:
    if len(source) > maximum:
        start = rng.randrange(len(source) - maximum + 1)
        source = source[start : start + maximum]

    for _ in range(rng.randint(1, 8)):
        operation = rng.randrange(5)
        index = rng.randrange(len(source) + 1)
        if operation == 0 and source:
            width = rng.randint(1, min(12, len(source) - min(index, len(source) - 1)))
            index = min(index, len(source) - 1)
            source = source[:index] + source[index + width :]
        elif operation == 1:
            source = source[:index] + _random_source(rng, 12) + source[index:]
        elif operation == 2 and source:
            index = min(index, len(source) - 1)
            replacement = _random_source(rng, 1)
            source = source[:index] + replacement + source[index + 1 :]
        elif operation == 3 and source:
            start = rng.randrange(len(source))
            end = rng.randint(start + 1, min(len(source), start + 16))
            source = source[:index] + source[start:end] + source[index:]
        elif operation == 4 and len(source) > 1:
            index = min(index, len(source) - 2)
            source = (
                source[:index]
                + source[index + 1]
                + source[index]
                + source[index + 2 :]
            )
        if len(source) > maximum:
            source = source[:maximum]
    return source


def _fuzz_source_mutations(
    rng: random.Random,
    _iteration: int,
    config: FuzzConfig,
) -> object:
    root = Path(__file__).resolve().parents[1]
    corpus = corpus_sources(root)
    if not corpus:
        raise AssertionError("the Valiance sample corpus is empty")
    source = _mutate_source(
        rng,
        rng.choice(corpus),
        config.max_source_length,
    )
    try:
        try:
            tokens = lex(source)
        except LexError:
            return source
        _validate_tokens(source, tokens)

        try:
            program = parse(source)
        except (LexError, ParseError):
            return source
        pretty_ast(program)
        return source
    except BaseException as exc:
        raise _GeneratedCaseFailure(source, exc) from exc


def _fuzz_parser_depth(
    rng: random.Random,
    iteration: int,
    _config: FuzzConfig,
) -> object:
    """Exercise valid and damaged delimiter nesting without leaking recursion errors."""
    opening, closing = rng.choice((("(", ")"), ("[", "]"), ("{", "}")))
    depth = 32 + ((iteration * 37 + rng.randrange(97)) % 1_600)
    source = opening * depth + "0" + closing * depth
    if rng.random() < 0.35:
        source = source[:-rng.randint(1, min(depth, 8))]
    try:
        try:
            parse(source)
        except (LexError, ParseError):
            return source
        return source
    except BaseException as exc:
        raise _GeneratedCaseFailure(source, exc) from exc


def _arith_tree(rng: random.Random, depth: int) -> tuple[str, Decimal]:
    if depth <= 0 or rng.random() < 0.35:
        value = rng.randint(-50, 50)
        return str(value), Decimal(value)

    left_source, left = _arith_tree(rng, depth - 1)
    right_source, right = _arith_tree(rng, depth - 1)
    operator = rng.choice(("+", "-", "*"))
    result = {
        "+": left + right,
        "-": left - right,
        "*": left * right,
    }[operator]
    return f"{operator}({left_source}, {right_source})", result


def _nested_numbers(values: list[int], width: int) -> list[list[int]]:
    return [values[index : index + width] for index in range(0, len(values), width)]


def _sum_callable_source(arity: int, niladic_value: int = 0) -> str:
    """Build a fixed-arity callable that returns the sum of its inputs."""
    if arity == 0:
        return f"fn => {niladic_value} end"
    names = tuple(f"x{index}" for index in range(arity))
    params = ", ".join(f"{name}: Number" for name in names)
    body = f"${names[0]}"
    for name in names[1:]:
        body += f" ${name} +"
    return f"fn ({params}) -> Number => {body} end"


def _program_case(rng: random.Random, max_depth: int) -> _ProgramCase:
    mode = rng.randrange(9)
    expression, value = _arith_tree(rng, max_depth)

    if mode == 0:
        return _ProgramCase(expression, [value])
    if mode == 1:
        factor_source, factor = _arith_tree(rng, max(0, max_depth - 1))
        return _ProgramCase(
            f"$value = {expression}\n*($value, {factor_source})",
            [value * factor],
        )
    if mode == 2:
        alternate_source, alternate = _arith_tree(rng, max_depth)
        condition = rng.choice((True, False))
        source_condition = "true" if condition else "false"
        return _ProgramCase(
            f"if {source_condition} => {expression} else => "
            f"{alternate_source} end",
            [value if condition else alternate],
        )
    if mode == 3:
        left = rng.randint(-100, 100)
        right = rng.randint(-100, 100)
        operator = rng.choice(("+", "-", "*"))
        expected = {
            "+": Decimal(left + right),
            "-": Decimal(left - right),
            "*": Decimal(left * right),
        }[operator]
        return _ProgramCase(
            "define calculate(x: Number, y: Number) -> Number => "
            f"{operator}($x, $y) end\ncalculate({left}, {right})",
            [expected],
        )
    if mode in {4, 5}:
        values = [rng.randint(-20, 20) for _ in range(rng.randint(1, 6))]
        scalar = rng.randint(-20, 20)
        literal = "[" + ", ".join(map(str, values)) + "]"
        operator = rng.choice(("+", "-", "*"))
        vector_first = mode == 4
        source = (
            f"{literal} {operator} {scalar}"
            if vector_first
            else f"{scalar} {operator} {literal}"
        )
        expected_values = []
        for item in values:
            left, right = (item, scalar) if vector_first else (scalar, item)
            expected_values.append(
                Decimal(
                    {
                        "+": left + right,
                        "-": left - right,
                        "*": left * right,
                    }[operator]
                )
            )
        return _ProgramCase(source, [expected_values])

    if mode == 7:
        arity = rng.randint(0, 3)
        values = [rng.randint(-20, 20) for _ in range(arity * 2)]
        niladic = rng.randint(-20, 20)
        callable_source = _sum_callable_source(arity, niladic)
        prefix = " ".join(map(str, values))
        source = f"{prefix} both: {callable_source}".strip()
        if arity == 0:
            expected = [Decimal(niladic), Decimal(niladic)]
        else:
            expected = [
                Decimal(sum(values[:arity])),
                Decimal(sum(values[arity:])),
            ]
        return _ProgramCase(source, expected)

    if mode == 8:
        lower_arity = rng.randint(0, 3)
        upper_arity = rng.randint(0, 3)
        values = [
            rng.randint(-20, 20)
            for _ in range(lower_arity + upper_arity)
        ]
        lower_niladic = rng.randint(-20, 20)
        upper_niladic = rng.randint(-20, 20)
        lower_source = _sum_callable_source(lower_arity, lower_niladic)
        upper_source = _sum_callable_source(upper_arity, upper_niladic)
        prefix = " ".join(map(str, values))
        source = (
            f"{prefix} correspond: ({lower_source}, {upper_source})".strip()
        )
        lower_values = values[:lower_arity]
        upper_values = values[lower_arity:]
        expected = [
            Decimal(sum(lower_values))
            if lower_arity
            else Decimal(lower_niladic),
            Decimal(sum(upper_values))
            if upper_arity
            else Decimal(upper_niladic),
        ]
        return _ProgramCase(source, expected)

    width = rng.randint(1, 3)
    values = [rng.randint(-10, 10) for _ in range(width * rng.randint(1, 3))]
    scalar = rng.randint(-10, 10)
    rows = _nested_numbers(values, width)
    literal = "[" + ", ".join(
        "[" + ", ".join(map(str, row)) + "]" for row in rows
    ) + "]"
    return _ProgramCase(
        f"{literal} + {scalar}",
        [[[Decimal(item + scalar) for item in row] for row in rows]],
    )


def _fuzz_valid_programs(
    rng: random.Random,
    _iteration: int,
    config: FuzzConfig,
) -> object:
    case = _program_case(rng, config.max_depth)
    try:
        analyser = Analyser()
        typed = analyser.analyse(parse(case.source))
        if analyser.diagnostics:
            raise AssertionError(
                f"generated valid program was rejected: {analyser.diagnostics}"
            )

        program = compile_program(typed)
        direct = run(program)
        round_tripped = run(loads(dumps(program)))
        if direct != round_tripped:
            raise AssertionError(
                f"direct and serialized execution differ: {direct!r} != "
                f"{round_tripped!r}"
            )
        if direct != case.expected:
            raise AssertionError(
                f"runtime result differs from model: {direct!r} != "
                f"{case.expected!r}"
            )
        return case
    except BaseException as exc:
        raise _GeneratedCaseFailure(case, exc) from exc


def _random_string(rng: random.Random, maximum: int = 16) -> str:
    alphabet = "abcXYZ_09 éλ🙂#.+-/"
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, maximum)))


def _random_decimal(rng: random.Random) -> Decimal:
    coefficient = rng.randint(-(10**30), 10**30)
    exponent = rng.randint(-20, 20)
    return Decimal(coefficient).scaleb(exponent)


def _random_tag(rng: random.Random) -> DataTag:
    return DataTag(
        _random_string(rng, 10) or "tag",
        depth=rng.randint(0, 4),
        absent=rng.choice((False, True)),
    )


def _random_pattern(rng: random.Random, depth: int) -> RuntimeTypePattern:
    child_count = rng.randint(0, 3) if depth > 0 else 0
    return RuntimeTypePattern(
        kind=rng.choice(("nominal", "union", "collection", "tagged", "tuple")),
        name=None if rng.random() < 0.3 else (_random_string(rng, 10) or "Type"),
        children=tuple(
            _random_pattern(rng, depth - 1) for _ in range(child_count)
        ),
        accepted_names=tuple(
            _random_string(rng, 8) or "Accepted" for _ in range(rng.randint(0, 3))
        ),
        variances=tuple(
            rng.choice(tuple(Variance)) for _ in range(rng.randint(0, 3))
        ),
        tags=tuple(_random_tag(rng) for _ in range(rng.randint(0, 3))),
        rank=None if rng.random() < 0.4 else rng.randint(0, 5),
        collection_kind=None
        if rng.random() < 0.4
        else rng.choice(("list", "array", "rugged")),
    )


def _simple_value(rng: random.Random, depth: int) -> object:
    choices = [
        "none",
        "int",
        "decimal",
        "string",
        "tuple",
    ] * 3
    if depth > 0:
        choices.extend(("function", "function_set", "resolved", "extension", "object"))
    choice = rng.choice(choices)

    if choice == "none":
        return None
    if choice == "int":
        return rng.randint(-(2**63), 2**63 - 1)
    if choice == "decimal":
        return _random_decimal(rng)
    if choice == "string":
        return _random_string(rng)
    if choice == "tuple":
        return tuple(
            _simple_value(rng, max(0, depth - 1)) for _ in range(rng.randint(0, 3))
        )
    if choice == "function":
        return _random_function(rng, depth - 1)
    if choice == "function_set":
        overloads = tuple(
            _random_function(rng, depth - 1) for _ in range(rng.randint(1, 2))
        )
        branches = tuple(
            UnionDispatchBranch(
                tuple(
                    _random_pattern(rng, max(0, depth - 1))
                    for _ in range(rng.randint(0, 3))
                ),
                rng.randrange(len(overloads)),
            )
            for _ in range(rng.randint(0, 2))
        )
        return FunctionSetCode(overloads, branches)
    if choice == "resolved":
        extension = (
            _random_extension(rng, depth - 1) if rng.random() < 0.35 else None
        )
        return ResolvedElementReference(
            name=_random_string(rng, 12) or "+",
            overload_index=rng.randint(0, 20),
            vectorised=rng.choice((False, True)),
            vectorised_depths=tuple(
                rng.randint(0, 4) for _ in range(rng.randint(0, 4))
            ),
            vectorised_target_ranks=tuple(
                None if rng.random() < 0.3 else rng.randint(0, 4)
                for _ in range(rng.randint(0, 4))
            ),
            return_collection_ranks=tuple(
                None if rng.random() < 0.3 else rng.randint(0, 4)
                for _ in range(rng.randint(0, 4))
            ),
            type_args=tuple(_random_string(rng, 8) for _ in range(rng.randint(0, 3))),
            static_values=tuple(
                _simple_value(rng, max(0, depth - 1))
                for _ in range(rng.randint(0, 3))
            ),
            arity_override=None if rng.random() < 0.5 else rng.randint(0, 8),
            consumed_override=None if rng.random() < 0.5 else rng.randint(0, 8),
            multidispatch=rng.choice((False, True)),
            extension=extension,
        )
    if choice == "extension":
        return _random_extension(rng, depth - 1)

    fields = tuple(_random_string(rng, 8) or "field" for _ in range(rng.randint(0, 4)))
    required = tuple(field for field in fields if rng.random() < 0.5)
    defaults = tuple(
        (field, _simple_value(rng, max(0, depth - 1)))
        for field in fields
        if field not in required and rng.random() < 0.7
    )
    initializer: FunctionCode | FunctionSetCode | None = None
    if depth > 0 and rng.random() < 0.5:
        initializer = _random_function(rng, depth - 1)
    return ObjectConstructorReference(
        type_name=_random_string(rng, 12) or "Object",
        fields=fields,
        required=required,
        defaults=defaults,
        runtime_metadata=_simple_value(rng, max(0, depth - 1)),
        initializer=initializer,
    )


def _random_extension(rng: random.Random, depth: int) -> VectorExtensionReference:
    mode = rng.choice(("default", "rules", "selector"))
    default = (
        _random_function(rng, max(0, depth - 1)) if mode == "default" else None
    )
    selector = (
        _random_function(rng, max(0, depth - 1)) if mode == "selector" else None
    )
    rules = (
        tuple(
            ExtensionRuleReference(
                tuple(
                    rng.choice((False, True))
                    for _ in range(rng.randint(0, 4))
                ),
                _random_function(rng, max(0, depth - 1)),
            )
            for _ in range(rng.randint(1, 3))
        )
        if mode == "rules"
        else ()
    )
    return VectorExtensionReference(default, rules, selector)


def _random_function(
    rng: random.Random,
    depth: int,
    forced_op: OpCode | None = None,
) -> FunctionCode:
    instruction_count = rng.randint(0, 4 if depth > 0 else 8)
    instructions: list[Instruction] = []
    if forced_op is not None:
        instructions.append(Instruction(forced_op, _simple_value(rng, max(0, depth))))
    while len(instructions) < instruction_count:
        instructions.append(
            Instruction(
                rng.choice(tuple(OpCode)),
                _simple_value(rng, max(0, depth)),
            )
        )

    return FunctionCode(
        instructions=tuple(instructions),
        params=tuple(_random_string(rng, 8) or "p" for _ in range(rng.randint(0, 4))),
        name=None if rng.random() < 0.25 else _random_string(rng, 12),
        cycle_params=rng.choice((False, True)),
        accepts_stack_inputs=rng.choice((False, True)),
        element_tags=tuple(
            _random_string(rng, 8) or "tag" for _ in range(rng.randint(0, 3))
        ),
        recursive=rng.choice((False, True)),
        multi=rng.choice((False, True)),
        dispatch_types=tuple(
            None if rng.random() < 0.3 else _random_string(rng, 8)
            for _ in range(rng.randint(0, 4))
        ),
        return_tags=tuple(
            tuple(_random_tag(rng) for _ in range(rng.randint(0, 3)))
            for _ in range(rng.randint(0, 4))
        ),
        return_collection_ranks=tuple(
            None if rng.random() < 0.3 else rng.randint(0, 5)
            for _ in range(rng.randint(0, 4))
        ),
        param_collection_ranks=tuple(
            None if rng.random() < 0.3 else rng.randint(0, 5)
            for _ in range(rng.randint(0, 4))
        ),
    )



def _fuzz_optimizer(
    rng: random.Random,
    iteration: int,
    _config: FuzzConfig,
) -> object:
    """Differentially exercise every default optimisation family."""
    mode = iteration % 5
    left = rng.randint(-50, 50)
    right = rng.randint(1, 50)
    factor = rng.randint(1, 12)

    if mode == 4:
        condition = Decimal(rng.randrange(2))
        expected = "enabled" if condition else "disabled"
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, Decimal("999")),
                    Instruction(OpCode.POP),
                    Instruction(OpCode.PUSH_CONST, condition),
                    Instruction(OpCode.JUMP_IF_FALSE, 6),
                    Instruction(OpCode.PUSH_CONST, "enabled"),
                    Instruction(OpCode.JUMP, 7),
                    Instruction(OpCode.PUSH_CONST, "disabled"),
                    Instruction(OpCode.RETURN),
                ),
                name="<optimizer-peephole-fuzz>",
            )
        )
        case: object = program
        try:
            optimized = _compile_optimizer_program(program)
            if run(program) != [expected] or run(optimized) != [expected]:
                raise AssertionError("peephole optimisation changed branch behaviour")
            if run(loads(dumps(optimized))) != [expected]:
                raise AssertionError(
                    "optimised peephole bytecode changed after round trip"
                )
            if any(
                instruction == Instruction(OpCode.PUSH_CONST, Decimal("999"))
                for instruction in optimized.main.instructions
            ):
                raise AssertionError("dead scalar push survived peephole optimisation")
            if any(
                instruction.op is OpCode.JUMP_IF_FALSE
                for instruction in optimized.main.instructions
            ):
                raise AssertionError("literal conditional branch was not folded")
            return case
        except BaseException as exc:
            raise _GeneratedCaseFailure(case, exc) from exc

    if mode == 0:
        source = f"{left} {right} + {factor} *"
    elif mode == 1:
        source = (
            "define combine(left: Number, right: Number) -> Number => + end\n"
            f"{left} {right} combine"
        )
    elif mode == 2:
        rate = Decimal(rng.randint(1, 25)) / Decimal(10)
        source = (
            f"define \\rate -> Number => {rate} end\n"
            f"{factor} \\rate *"
        )
    else:
        arity = rng.randint(2, 5)
        values = [rng.randint(-50, 50) for _ in range(arity)]
        labels = [f"value{index}" for index in range(arity)]
        permutation = list(range(arity))
        rng.shuffle(permutation)
        inverse = [permutation.index(index) for index in range(arity)]
        source = " ".join(str(value) for value in values)
        source += " move(" + ", ".join(labels) + " -> "
        source += ", ".join(labels[index] for index in permutation) + ")"
        source += " move(" + ", ".join(labels) + " -> "
        source += ", ".join(labels[index] for index in inverse) + ")"
    case = source
    try:
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        if analyser.diagnostics:
            raise AssertionError(
                "generated optimizer source did not analyse: "
                f"{analyser.diagnostics}"
            )
        unoptimized = compile_program(typed, optimize=False)
        optimized = compile_program(typed)
        expected = run(unoptimized)
        if run(optimized) != expected:
            raise AssertionError("optimized and unoptimized programs diverged")
        if run(loads(dumps(optimized))) != expected:
            raise AssertionError("optimized program diverged after serialization")

        if mode == 0 and len(optimized.main.instructions) >= len(
            unoptimized.main.instructions
        ):
            raise AssertionError("constant folding did not reduce bytecode")
        if mode == 1:
            nested = optimized.main.instructions[0].arg
            if not isinstance(nested, FunctionCode) or not any(
                instruction.op is OpCode.LOAD_VAR for instruction in nested.instructions
            ):
                raise AssertionError("explicit argument materialisation did not run")
        if mode == 2 and any(
            instruction.op is OpCode.CALL_RESOLVED_ELEMENT
            and isinstance(instruction.arg, ResolvedElementReference)
            and instruction.arg.name == "\\rate"
            for instruction in optimized.main.instructions
        ):
            raise AssertionError("small constant function was not inlined")
        if mode == 3 and any(
            instruction.op is OpCode.STACK_SHUFFLE
            for instruction in optimized.main.instructions
        ):
            raise AssertionError("inverse stack shuffles were not eliminated")
        return case
    except BaseException as exc:
        raise _GeneratedCaseFailure(case, exc) from exc


def _compile_optimizer_program(program: Program) -> Program:
    """Run the public default optimiser over a hand-built fuzz program."""
    from valiance.runtime import optimize_program

    return optimize_program(program)


def _fuzz_serialization_roundtrip(
    rng: random.Random,
    iteration: int,
    config: FuzzConfig,
) -> object:
    forced_op = tuple(OpCode)[iteration % len(OpCode)]
    program = Program(_random_function(rng, config.max_depth, forced_op))
    try:
        encoded = dumps(program)
        decoded = loads(encoded)
        if decoded != program:
            raise AssertionError("bytecode serialization is not an exact round trip")
        if dumps(decoded) != encoded:
            raise AssertionError("bytecode serialization is not canonical")
        return program
    except BaseException as exc:
        raise _GeneratedCaseFailure(program, exc) from exc



def _fuzz_numeric_boolean_serialization(
    rng: random.Random,
    iteration: int,
    _config: FuzzConfig,
) -> object:
    """Require host booleans to use Valiance's numeric bytecode representation."""
    value = bool((iteration + rng.randrange(2)) % 2)
    program = Program(
        FunctionCode(
            (
                Instruction(OpCode.PUSH_CONST, value),
                Instruction(OpCode.RETURN),
            ),
            name="<boolean-fuzz>",
        )
    )
    try:
        decoded = loads(dumps(program))
        decoded_value = decoded.main.instructions[0].arg
        if type(decoded_value) is not int or decoded_value != int(value):
            raise AssertionError("boolean bytecode did not canonicalize to 0 or 1")
        if run(decoded) != [int(value)]:
            raise AssertionError("numeric boolean changed at runtime")
        return program
    except BaseException as exc:
        raise _GeneratedCaseFailure(program, exc) from exc

def _mutate_bytes(rng: random.Random, data: bytes) -> bytes:
    mutation = rng.randrange(6)
    if mutation == 0:
        return data[: rng.randrange(len(data) + 1)]
    if mutation == 1 and data:
        index = rng.randrange(len(data))
        flipped = bytes((data[index] ^ (1 << rng.randrange(8)),))
        return data[:index] + flipped + data[index + 1 :]
    if mutation == 2 and data:
        index = rng.randrange(len(data))
        return data[:index] + bytes((rng.randrange(256),)) + data[index + 1 :]
    if mutation == 3:
        return data + bytes(rng.randrange(256) for _ in range(rng.randint(1, 16)))
    if mutation == 4:
        return bytes(rng.randrange(256) for _ in range(rng.randint(0, 128)))
    prefix = data[: rng.randrange(len(data) + 1)] if data else b""
    return prefix + bytes(rng.randrange(256) for _ in range(rng.randint(0, 64)))


def _fuzz_malformed_bytecode(
    rng: random.Random,
    iteration: int,
    config: FuzzConfig,
) -> object:
    valid = dumps(
        Program(
            _random_function(
                rng,
                min(config.max_depth, 2),
                tuple(OpCode)[iteration % len(OpCode)],
            )
        )
    )
    payload = _mutate_bytes(rng, valid)
    try:
        try:
            decoded = loads(payload)
        except BytecodeFormatError:
            return payload

        if loads(dumps(decoded)) != decoded:
            raise AssertionError("accepted bytecode is not stable after re-encoding")
        return payload
    except BaseException as exc:
        raise _GeneratedCaseFailure(payload, exc) from exc


def _nested_tuple_bytecode(depth: int) -> bytes:
    """Build a minimal bytecode file whose sole constant has nested tuples."""
    from valiance.runtime.serialization import MAGIC

    empty_tuple = b"\x04\x00\x00\x00\x00"
    function_header = b"".join(
        (
            b"\x00",  # optional name
            b"\x00",  # cycle params
            b"\x00",  # accepts stack inputs
            b"\x00",  # recursive
            b"\x00\x00\x00\x00",  # params
            b"\x00\x00\x00\x00",  # element tags
            b"\x00",  # multi
            b"\x00\x00\x00\x00",  # dispatch types
            b"\x00\x00\x00\x00",  # return tags
            empty_tuple,  # return collection ranks
            empty_tuple,  # parameter collection ranks
            b"\x00\x00\x00\x01",  # instruction count
            b"\x01",  # PUSH_CONST
        )
    )
    nested_value = (b"\x04\x00\x00\x00\x01" * depth) + b"\x00"
    return MAGIC + function_header + nested_value


def _fuzz_bytecode_depth(
    rng: random.Random,
    iteration: int,
    _config: FuzzConfig,
) -> object:
    """Require recursive bytecode payloads to decode or fail through the format API."""
    depth = 32 + ((iteration * 53 + rng.randrange(101)) % 1_600)
    payload = _nested_tuple_bytecode(depth)
    try:
        try:
            decoded = loads(payload)
        except BytecodeFormatError:
            return payload
        if loads(dumps(decoded)) != decoded:
            raise AssertionError("deep bytecode changed after canonical round trip")
        return payload
    except BaseException as exc:
        raise _GeneratedCaseFailure(payload, exc) from exc


_RUNTIME_BYTECODE_OPS = tuple(
    op
    for op in OpCode
    if op
    not in {
        OpCode.JUMP,
        OpCode.JUMP_IF_FALSE,
        OpCode.JUMP_IF_MATCH,
        OpCode.WHILE,
        OpCode.FOREACH,
        OpCode.UNFOLD,
        OpCode.CYCLE_BEGIN,
        OpCode.CYCLE_END,
        OpCode.LOOP_BREAK,
        OpCode.RETURN_SIGNAL,
        OpCode.TRY_BEGIN,
        OpCode.TRY_END,
    }
)


def _runtime_fuzz_value(rng: random.Random, depth: int = 1) -> object:
    """Return a bounded primitive payload suitable for malformed VM programs."""
    choices = (None, rng.choice((False, True)), rng.randint(-8, 8), _random_string(rng))
    if depth <= 0 or rng.random() < 0.6:
        return rng.choice(choices)
    return tuple(_runtime_fuzz_value(rng, depth - 1) for _ in range(rng.randint(0, 3)))


def _fuzz_runtime_bytecode(
    rng: random.Random,
    iteration: int,
    _config: FuzzConfig,
) -> object:
    """Run malformed straight-line bytecode and reject leaked Python exceptions."""
    op = _RUNTIME_BYTECODE_OPS[iteration % len(_RUNTIME_BYTECODE_OPS)]
    instructions = [
        Instruction(OpCode.PUSH_CONST, _runtime_fuzz_value(rng, 0))
        for _ in range(rng.randint(0, 4))
    ]
    instructions.extend(
        (
            Instruction(op, _runtime_fuzz_value(rng)),
            Instruction(OpCode.RETURN),
        )
    )
    program = Program(FunctionCode(tuple(instructions), name="<fuzz>"))
    try:
        decoded = loads(dumps(program))
        try:
            run(decoded)
        except ValianceRuntimeError:
            return program
        return program
    except BaseException as exc:
        raise _GeneratedCaseFailure(program, exc) from exc


def _random_concrete_type(rng: random.Random, depth: int):
    atoms = (Integer, Real, Number, String, Boolean, NoneType())
    if depth <= 0:
        return rng.choice(atoms)

    choice = rng.randrange(10)
    if choice < 3:
        return rng.choice(atoms)
    if choice == 3:
        return U(
            _random_concrete_type(rng, depth - 1),
            _random_concrete_type(rng, depth - 1),
        )
    if choice == 4:
        return ExactList(_random_concrete_type(rng, depth - 1), rng.randint(1, 4))
    if choice == 5:
        return AtLeastList(_random_concrete_type(rng, depth - 1), rng.randint(1, 4))
    if choice == 6:
        return ExactArray(_random_concrete_type(rng, depth - 1), rng.randint(1, 4))
    if choice == 7:
        return AtLeastArray(_random_concrete_type(rng, depth - 1), rng.randint(1, 4))
    if choice == 8:
        return Tup(
            *(
                _random_concrete_type(rng, depth - 1)
                for _ in range(rng.randint(0, 4))
            )
        )
    return N(
        Symbol(rng.choice(("Box", "Pair", "ResultLike"))),
        *(
            _random_concrete_type(rng, depth - 1)
            for _ in range(rng.randint(0, 2))
        ),
    )


def _fuzz_type_relations(
    rng: random.Random,
    _iteration: int,
    config: FuzzConfig,
) -> object:
    left = _random_concrete_type(rng, config.max_depth)
    right = _random_concrete_type(rng, config.max_depth)
    rank = rng.randint(1, 4)
    case = (left, right, rank)

    try:
        normalized = normalize(left)

        if normalize(normalized) != normalized:
            raise AssertionError("type normalization is not idempotent")
        if not same(left, left) or not same(normalized, normalized):
            raise AssertionError("type equality is not reflexive")
        if not assignable(left, left):
            raise AssertionError("assignability is not reflexive")
        if not subtype(left, left):
            raise AssertionError("subtyping is not reflexive")
        if not compatible(left, left):
            raise AssertionError("compatibility is not reflexive")
        if same(left, right) != same(right, left):
            raise AssertionError("type equality is not symmetric")
        if not show(normalized):
            raise AssertionError("type display produced an empty string")

        duplicate_union = U(left, left)
        if not same(duplicate_union, normalized):
            raise AssertionError("a duplicate union did not normalize to its member")

        merged = merge_types(left, right)
        reverse_merged = merge_types(right, left)
        if not same(merged, reverse_merged):
            raise AssertionError("type merging is not commutative")
        if not assignable(left, merged) or not assignable(right, merged):
            raise AssertionError("merged type does not accept both inputs")

        if subtype(left, right) and not assignable(left, right):
            raise AssertionError("subtyping did not imply assignability")
        if assignable(left, right) and not compatible(left, right):
            raise AssertionError("assignability did not imply compatibility")

        if assignable(left, right):
            covariance_cases = (
                (ExactList(left, rank), ExactList(right, rank), "exact list"),
                (AtLeastList(left, rank), AtLeastList(right, rank), "minimum list"),
                (ExactArray(left, rank), ExactArray(right, rank), "exact array"),
                (AtLeastArray(left, rank), AtLeastArray(right, rank), "minimum array"),
                (ExactArray(left, rank), ExactList(right, rank), "array-to-list"),
            )
            for wrapped_left, wrapped_right, label in covariance_cases:
                if not assignable(wrapped_left, wrapped_right):
                    raise AssertionError(
                        f"{label} covariance did not preserve assignability"
                    )
            if assignable(ExactList(left, rank), ExactArray(right, rank)):
                raise AssertionError("list values became assignable to arrays")

        tag_name = _random_string(rng, 8) or "tag"
        tagged = Tagged(left, DataTag(tag_name, rng.randint(0, 2)))
        if not same(normalize(tagged), normalize(normalize(tagged))):
            raise AssertionError("tagged normalization is not stable")

        row = Row(left)
        exact = Exact(left)
        show(row)
        show(exact)
        return case
    except BaseException as exc:
        raise _GeneratedCaseFailure(case, exc) from exc



def _fuzz_type_algebra(
    rng: random.Random,
    iteration: int,
    _config: FuzzConfig,
) -> object:
    """Check high-risk relation laws, branch joins, and generic order invariance."""
    unrelated = rng.choice((String, N(Symbol("FuzzUnrelated"))))
    value = rng.choice((Integer, Real, Number))
    case = (value, unrelated, iteration)
    try:
        # Bottom must remain bottom through intersections, otherwise subtype
        # transitivity can depend on which relation path is evaluated first.
        bottom_intersection = I(Never(), value)
        tagged_target = Tagged(unrelated, DataTag(f"required-{iteration % 5}"))
        if not same(bottom_intersection, Never()):
            raise AssertionError("intersection with Never did not normalize to Never")
        if not (
            subtype(bottom_intersection, Never())
            and subtype(Never(), tagged_target)
            and subtype(bottom_intersection, tagged_target)
        ):
            raise AssertionError("subtyping was not transitive through Never")

        if not same(optional(Never()), NoneType()):
            raise AssertionError("Optional[Never] did not normalize to None")

        if not (
            subtype(optional(Integer), optional(Number))
            and assignable(optional(Integer), optional(Number))
            and compatible(optional(Integer), optional(Number))
        ):
            raise AssertionError("optional covariance was rejected")
        if assignable(optional(Number), optional(Integer)):
            raise AssertionError("optional covariance was accepted backwards")

        numeric_intersection = rng.choice(
            ((Integer, Real, Integer), (Integer, Number, Integer), (Real, Number, Real))
        )
        if not same(I(*numeric_intersection[:2]), numeric_intersection[2]):
            raise AssertionError("numeric intersection kept a redundant supertype")

        # Branch joins must not depend on the order in which control-flow
        # branches happen to be analysed.
        branch_types = (value, unrelated, NoneType())
        merged_types = {
            normalize(merge_types(merge_types(first, second), third))
            for first, second, third in permutations(branch_types)
        }
        expected = optional(U(value, unrelated))
        if len(merged_types) != 1 or not same(next(iter(merged_types)), expected):
            raise AssertionError("branch type merge was order-dependent")

        branches = tuple(TypeStack((typ,)) for typ in branch_types[:2]) + (TypeStack(),)
        merged_stacks = {
            merge_stacks(merge_stacks(first, second), third)
            for first, second, third in permutations(branches)
        }
        if merged_stacks != {TypeStack((expected,))}:
            raise AssertionError("branch stack merge was order-dependent")

        # Generic evidence is a set of constraints, not an argument-order fold.
        evidence = (NoneType(), value, optional(value))
        overload = Overload((V("T"), V("T"), V("T")), (V("T"),))
        solutions = []
        for arguments in permutations(evidence):
            applied = apply_overload(overload, arguments)
            if applied is None:
                raise AssertionError("generic solution depended on evidence order")
            solutions.append(applied.substitution["T"])
        if not all(same(solution, optional(value)) for solution in solutions):
            raise AssertionError("generic evidence produced inconsistent solutions")

        # Overload declaration order must not affect the chosen numeric overload.
        overloads = (
            Overload((Integer,), (Integer,)),
            Overload((Real,), (Real,)),
            Overload((Number,), (Number,)),
        )
        expected_result = resolve_overload_result(overloads, (value,))
        if expected_result is None:
            raise AssertionError("numeric overload set had no winner")
        for ordering in permutations(overloads):
            result = resolve_overload_result(ordering, (value,))
            if result is None or not same(
                result.returns[0],
                expected_result.returns[0],
            ):
                raise AssertionError(
                    "overload resolution depended on declaration order"
                )
        return case
    except BaseException as exc:
        raise _GeneratedCaseFailure(case, exc) from exc

_STRUCT_FOO = Symbol("FuzzFoo")
_STRUCT_ENTITY = Symbol("FuzzEntity")
_STRUCT_CAR = Symbol("FuzzCar")
_STRUCT_VEHICLE = Symbol("FuzzVehicle")
_STRUCT_BOX = Symbol("FuzzBox")
_STRUCT_SINK = Symbol("FuzzSink")
_STRUCT_CELL = Symbol("FuzzCell")
_STRUCT_READ = Symbol("fuzz-read")
_STRUCT_WRITE = Symbol("fuzz-write")
_STRUCT_MAP = Symbol("fuzz-map")
_STRUCT_FIELDS = tuple(Symbol(f"field-{index}") for index in range(6))
_STRUCT_FOO_TYPE = N(_STRUCT_FOO)
_STRUCT_ENTITY_TYPE = N(_STRUCT_ENTITY)
_STRUCT_CAR_TYPE = N(_STRUCT_CAR)
_STRUCT_VEHICLE_TYPE = N(_STRUCT_VEHICLE)


@dataclass(frozen=True, slots=True)
class _StructuralTypeCase:
    """Generated structural-type world retained in reproducible failures."""

    row_source: Type
    row_target: Type
    generic_row: Type
    trait: Type
    subject: Type


def _structural_context() -> Context:
    """Create nominal, variance, and subtype facts for structural fuzzing."""
    ctx = Context(
        trait_impls={
            _STRUCT_FOO: {_STRUCT_ENTITY},
            _STRUCT_CAR: {_STRUCT_VEHICLE},
        }
    )
    ctx.set_generic_variance(_STRUCT_BOX, (Variance.COVARIANT,))
    ctx.set_generic_variance(_STRUCT_SINK, (Variance.CONTRAVARIANT,))
    ctx.set_generic_variance(_STRUCT_CELL, (Variance.INVARIANT,))
    return ctx


def _positive_relation_pair(rng: random.Random) -> tuple[Type, Type]:
    """Return a source/target pair known to satisfy directional assignment."""
    return rng.choice(
        (
            (Integer, Integer),
            (Integer, Real),
            (Integer, Number),
            (Real, Number),
            (String, String),
            (_STRUCT_FOO_TYPE, _STRUCT_ENTITY_TYPE),
            (_STRUCT_CAR_TYPE, _STRUCT_VEHICLE_TYPE),
        )
    )


def _incompatible_with(typ: Type, ctx: Context) -> Type:
    """Return a simple type unrelated to ``typ`` in either direction."""
    for candidate in (String, _STRUCT_FOO_TYPE, Integer):
        if not assignable(typ, candidate, ctx) and not assignable(
            candidate,
            typ,
            ctx,
        ):
            return candidate
    raise AssertionError(f"no incompatible structural fuzz type for {typ}")


def _combined_solution(
    constraints: dict[str, list[Type]],
    ctx: Context | None = None,
) -> dict[str, Type]:
    """Combine a solver result and reject incoherent generic evidence."""
    result: dict[str, Type] = {}
    for name, values in constraints.items():
        combined = _combine_all(values, ctx)
        if combined is None:
            raise AssertionError(f"incoherent solution for {name}: {values!r}")
        result[name] = combined
    return result


def _structural_requirement(
    name: Symbol,
    params: tuple[Type, ...],
    returns: tuple[Type, ...],
) -> AnonymousTraitRequirement:
    """Build one concise anonymous structural-trait requirement."""
    return AnonymousTraitRequirement(name, Overload(params, returns))


def _fuzz_structural_types(
    rng: random.Random,
    iteration: int,
    _config: FuzzConfig,
) -> object:
    """Fuzz rows, scoped generics, traits, variance, and relation laws."""
    ctx = _structural_context()
    field_count = rng.randint(1, len(_STRUCT_FIELDS) - 1)
    fields = list(_STRUCT_FIELDS[:field_count])
    rng.shuffle(fields)
    base_source, base_target = rng.choice(
        (
            (_STRUCT_FOO_TYPE, _STRUCT_ENTITY_TYPE),
            (_STRUCT_CAR_TYPE, _STRUCT_VEHICLE_TYPE),
            (_STRUCT_FOO_TYPE, _STRUCT_FOO_TYPE),
        )
    )
    field_pairs = [_positive_relation_pair(rng) for _ in fields]
    source_fields = tuple(
        Field(name, source)
        for name, (source, _target) in zip(fields, field_pairs, strict=True)
    )
    target_width = rng.randint(1, field_count)
    target_fields = tuple(
        Field(name, target)
        for name, (_source, target) in zip(
            fields[:target_width],
            field_pairs[:target_width],
            strict=True,
        )
    )
    extra = Field(Symbol(f"extra-{iteration % 7}"), String)
    row_source = Row(base_source, *source_fields, extra)
    row_target = Row(base_target, *target_fields)
    names = tuple(f"@{iteration % 101 + index + 1}" for index in range(field_count + 1))
    generic_row = Row(
        V(names[0]),
        *(Field(name, V(names[index + 1])) for index, name in enumerate(fields)),
    )

    subject, subject_parameter = _positive_relation_pair(rng)
    return_value, required_return = _positive_relation_pair(rng)
    trait = AnonymousTrait(
        (Symbol("T"),),
        (
            _structural_requirement(
                _STRUCT_MAP,
                (V("T"),),
                (required_return,),
            ),
        ),
    )
    case = _StructuralTypeCase(row_source, row_target, generic_row, trait, subject)

    try:
        # Width/depth row laws and relation implications.
        if not subtype(row_source, row_target, ctx):
            raise AssertionError("constructed row subtype was rejected")
        if not assignable(row_source, row_target, ctx):
            raise AssertionError("row subtype did not imply assignability")
        if not compatible(row_source, row_target, ctx):
            raise AssertionError("row assignment did not imply compatibility")
        if assignable(row_target, row_source, ctx):
            raise AssertionError("row width subtyping became symmetric")
        if not same(row_source, Row(base_source, extra, *reversed(source_fields))):
            raise AssertionError("row field ordering changed canonical equality")

        missing = Row(base_source, *source_fields[target_width:])
        if assignable(missing, row_target, ctx):
            raise AssertionError("row missing required fields was assignable")
        first = target_fields[0]
        source_by_name = {field.name: field.typ for field in source_fields}
        wrong = Row(
            base_target,
            Field(first.name, _incompatible_with(source_by_name[first.name], ctx)),
            *target_fields[1:],
        )
        if assignable(row_source, wrong, ctx):
            raise AssertionError("row accepted an incompatible field")

        # Named/anonymous row variables, reconstruction, and alpha renaming.
        constraints = _solve(generic_row, row_source, ctx)
        if constraints is None:
            raise AssertionError("generic row did not solve")
        substitution = _combined_solution(constraints, ctx)
        solved_row = _substitute(generic_row, substitution)
        if not assignable(row_source, solved_row, ctx):
            raise AssertionError("row substitution did not reconstruct its pattern")
        if assignable(row_source, generic_row, ctx):
            raise AssertionError("free generic row allowed storage assignment")
        if not compatible(row_source, generic_row, ctx):
            raise AssertionError("generic row did not support call compatibility")

        renamed_names = tuple(
            f"@{500 + iteration % 101 + i}" for i in range(field_count + 1)
        )
        renamed_row = Row(
            V(renamed_names[0]),
            *(Field(name, V(renamed_names[i + 1])) for i, name in enumerate(fields)),
        )
        renamed = _solve(renamed_row, row_source, ctx)
        if renamed is None or not assignable(
            row_source,
            _substitute(renamed_row, _combined_solution(renamed, ctx)),
            ctx,
        ):
            raise AssertionError("alpha-renamed row solved differently")

        # Shared generic evidence must combine or reject coherently.
        shared = Row(
            V("@base"),
            Field(_STRUCT_FIELDS[0], V("@item")),
            Field(_STRUCT_FIELDS[1], V("@item")),
        )
        shared_actual = Row(
            _STRUCT_FOO_TYPE,
            Field(_STRUCT_FIELDS[0], Integer),
            Field(_STRUCT_FIELDS[1], Number),
        )
        shared_solution = _solve(shared, shared_actual, ctx)
        if shared_solution is None or not same(
            _combined_solution(shared_solution, ctx)["@item"],
            Number,
        ):
            raise AssertionError("shared row generic did not widen to Number")
        conflict_actual = Row(
            _STRUCT_FOO_TYPE,
            Field(_STRUCT_FIELDS[0], String),
            Field(_STRUCT_FIELDS[1], Number),
        )
        if apply_overload(Overload((shared,), (V("@item"),)), (conflict_actual,), ctx):
            raise AssertionError("conflicting row generic escaped overload solving")

        # Structural types must respect declaration-site variance.
        if not assignable(N(_STRUCT_BOX, row_source), N(_STRUCT_BOX, row_target), ctx):
            raise AssertionError("row covariance failed in a nominal generic")
        if not assignable(
            N(_STRUCT_SINK, row_target),
            N(_STRUCT_SINK, row_source),
            ctx,
        ):
            raise AssertionError("row contravariance failed in a nominal generic")
        if assignable(N(_STRUCT_SINK, row_source), N(_STRUCT_SINK, row_target), ctx):
            raise AssertionError("row contravariance was accepted backwards")
        if assignable(N(_STRUCT_CELL, row_source), N(_STRUCT_CELL, row_target), ctx):
            raise AssertionError("row escaped an invariant generic")
        if not assignable(ExactList(row_source), ExactList(row_target), ctx):
            raise AssertionError("row covariance failed in a list")

        # Function-shape solving substitutes row variables before callability.
        function_return, _ = _positive_relation_pair(rng)
        function_row = Row(base_source, *source_fields)
        function_pattern = Fn((generic_row,), (V("@return"),))
        function_actual = Fn((function_row,), (function_return,))
        function_constraints = _solve(function_pattern, function_actual, ctx)
        if function_constraints is None or not same(
            _combined_solution(function_constraints, ctx)["@return"],
            function_return,
        ):
            raise AssertionError("generic function shape solved incorrectly")

        # Trait parameter contravariance and return covariance.
        ctx.define_structural_overload(
            _STRUCT_MAP,
            Overload((subject_parameter,), (return_value,)),
        )
        relations = (
            subtype(subject, trait, ctx),
            assignable(subject, trait, ctx),
            compatible(subject, trait, ctx),
        )
        if relations != (True, True, True):
            raise AssertionError(f"trait relation disagreement: {relations!r}")

        wrong_parameter_ctx = _structural_context()
        wrong_parameter_ctx.define_structural_overload(
            _STRUCT_MAP,
            Overload((_incompatible_with(subject, ctx),), (return_value,)),
        )
        if assignable(subject, trait, wrong_parameter_ctx):
            raise AssertionError("trait accepted an unusable candidate parameter")

        # Shared variables require complete backtracking, not first-match choice.
        coherent = rng.choice((Number, String))
        incoherent = String if same(coherent, Number) else Number
        shared_trait = AnonymousTrait(
            (Symbol("T"), Symbol("U")),
            (
                _structural_requirement(_STRUCT_READ, (V("T"),), (V("U"),)),
                _structural_requirement(_STRUCT_WRITE, (V("T"),), (V("U"),)),
            ),
        )
        swapped_trait = AnonymousTrait(
            (Symbol("T"), Symbol("U")),
            tuple(reversed(shared_trait.requirements)),
        )
        for order in ((incoherent, coherent), (coherent, incoherent)):
            shared_ctx = _structural_context()
            for result in order:
                shared_ctx.define_structural_overload(
                    _STRUCT_READ,
                    Overload((_STRUCT_FOO_TYPE,), (result,)),
                )
            shared_ctx.define_structural_overload(
                _STRUCT_WRITE,
                Overload((_STRUCT_FOO_TYPE,), (coherent,)),
            )
            if not assignable(_STRUCT_FOO_TYPE, shared_trait, shared_ctx):
                raise AssertionError("trait solver failed to backtrack")
            if not assignable(_STRUCT_FOO_TYPE, swapped_trait, shared_ctx):
                raise AssertionError("requirement order changed trait satisfaction")

        # Multiple complete paths must aggregate deterministically.
        result_trait = AnonymousTrait(
            (Symbol("T"), Symbol("U")),
            (_structural_requirement(_STRUCT_READ, (V("T"),), (V("U"),)),),
        )
        result_overload = Overload((result_trait,), (V("U"),))
        ambiguous_ctx = _structural_context()
        ambiguous_ctx.define_structural_overload(
            _STRUCT_READ,
            Overload((_STRUCT_FOO_TYPE,), (String,)),
        )
        ambiguous_ctx.define_structural_overload(
            _STRUCT_READ,
            Overload((_STRUCT_FOO_TYPE,), (Number,)),
        )
        if assignable(_STRUCT_FOO_TYPE, result_trait, ambiguous_ctx):
            raise AssertionError("incompatible trait solutions were accepted")
        if apply_overload(result_overload, (_STRUCT_FOO_TYPE,), ambiguous_ctx):
            raise AssertionError("ambiguous trait generic escaped application")

        widening_ctx = _structural_context()
        for result in (Integer, Number):
            widening_ctx.define_structural_overload(
                _STRUCT_READ,
                Overload((_STRUCT_FOO_TYPE,), (result,)),
            )
        widened = apply_overload(result_overload, (_STRUCT_FOO_TYPE,), widening_ctx)
        if widened is None or not same(widened.substitution["U"], Number):
            raise AssertionError("compatible trait solutions did not widen")

        contextual_ctx = _structural_context()
        for result in (_STRUCT_CAR_TYPE, _STRUCT_VEHICLE_TYPE):
            contextual_ctx.define_structural_overload(
                _STRUCT_READ,
                Overload((_STRUCT_FOO_TYPE,), (result,)),
            )
        contextual = apply_overload(
            result_overload,
            (_STRUCT_FOO_TYPE,),
            contextual_ctx,
        )
        if contextual is None or not same(
            contextual.substitution["U"],
            _STRUCT_VEHICLE_TYPE,
        ):
            raise AssertionError("trait solution ignored nominal subtype context")

        # Alpha-renaming and capture avoidance preserve meaning.
        renamed_trait = AnonymousTrait(
            (Symbol("Subject"),),
            (
                _structural_requirement(
                    _STRUCT_MAP,
                    (V("Subject"),),
                    (required_return,),
                ),
            ),
        )
        if not same(trait, renamed_trait):
            raise AssertionError("alpha-renamed traits were not equal")
        scoped_trait = AnonymousTrait(
            (Symbol("Local"),),
            (_structural_requirement(_STRUCT_READ, (V("Local"),), (V("Outer"),)),),
        )
        substituted = _substitute(
            scoped_trait,
            {"Local": String, "Outer": required_return},
        )
        expected = AnonymousTrait(
            (Symbol("Local"),),
            (_structural_requirement(_STRUCT_READ, (V("Local"),), (required_return,)),),
        )
        if not same(substituted, expected):
            raise AssertionError("outer substitution captured a trait generic")

        local_name = f"Local{iteration % 97}"
        renamed_local = f"Renamed{iteration % 89}"
        local_trait = AnonymousTrait(
            (),
            (
                AnonymousTraitRequirement(
                    _STRUCT_READ,
                    Overload(
                        (V(local_name),),
                        (V("Outer"),),
                        (GenericConstraint(local_name, V("Outer")),),
                    ),
                ),
            ),
        )
        alpha_local_trait = AnonymousTrait(
            (),
            (
                AnonymousTraitRequirement(
                    _STRUCT_READ,
                    Overload(
                        (V(renamed_local),),
                        (required_return,),
                        (GenericConstraint(renamed_local, required_return),),
                    ),
                ),
            ),
        )
        substituted_local = _substitute(
            local_trait,
            {local_name: String, "Outer": required_return},
        )
        if not same(substituted_local, alpha_local_trait):
            raise AssertionError("requirement-local generic capture or alpha failure")

        # Candidate-local generic bounds and nested structural composition.
        bounded_trait = AnonymousTrait(
            (Symbol("T"),),
            (_structural_requirement(_STRUCT_MAP, (V("T"),), (V("T"),)),),
        )
        bounded_ctx = _structural_context()
        bounded_ctx.define_structural_overload(
            _STRUCT_MAP,
            Overload(
                (V("X"),),
                (V("X"),),
                (GenericConstraint("X", _STRUCT_VEHICLE_TYPE),),
            ),
        )
        if not assignable(_STRUCT_CAR_TYPE, bounded_trait, bounded_ctx):
            raise AssertionError("bounded generic candidate rejected subtype")
        if assignable(String, bounded_trait, bounded_ctx):
            raise AssertionError("bounded generic candidate accepted invalid type")

        if not assignable(N(_STRUCT_BOX, subject), N(_STRUCT_BOX, trait), ctx):
            raise AssertionError("trait failed in a covariant generic")
        if not assignable(N(_STRUCT_SINK, trait), N(_STRUCT_SINK, subject), ctx):
            raise AssertionError("trait failed in a contravariant generic")
        if assignable(N(_STRUCT_CELL, subject), N(_STRUCT_CELL, trait), ctx):
            raise AssertionError("trait escaped an invariant generic")
        if not assignable(ExactList(subject), ExactList(trait), ctx):
            raise AssertionError("trait failed in a list")
        if not assignable(subject, U(trait, _incompatible_with(subject, ctx)), ctx):
            raise AssertionError("trait failed in a union")
        if not assignable(subject, optional(trait), ctx):
            raise AssertionError("trait failed in an optional")
        if not assignable(
            Row(_STRUCT_FOO_TYPE, Field(_STRUCT_FIELDS[0], subject)),
            Row(_STRUCT_ENTITY_TYPE, Field(_STRUCT_FIELDS[0], trait)),
            ctx,
        ):
            raise AssertionError("trait failed as a row field")

        return case
    except BaseException as exc:
        raise _GeneratedCaseFailure(case, exc) from exc


def _fuzz_analyser_never_recovery(
    rng: random.Random,
    _iteration: int,
    _config: FuzzConfig,
) -> object:
    """Exercise primary-diagnostic suppression and terminal ``Never`` paths."""
    primary_cases = (
        ("1 +(missing)", "unknown element 'missing'"),
        ("if missing => 1 else => 2 end", "unknown element 'missing'"),
        ("while missing => 1 end", "unknown element 'missing'"),
        ("[missing]", "unknown element 'missing'"),
    )
    never_cases = (
        "missing halt",
        "if halt => 1 else => 2 end missing",
        "1 +(halt) missing",
        "[halt] missing",
    )

    if rng.randrange(2) == 0:
        source, expected = rng.choice(primary_cases)
        case = ("primary", source)
        try:
            analyser = Analyser()
            analyser.analyse(parse(source))
            if len(analyser.diagnostics) != 1:
                raise AssertionError(
                    f"expected one primary diagnostic, got {analyser.diagnostics!r}"
                )
            if expected not in analyser.diagnostics[0]:
                raise AssertionError(
                    f"unexpected primary diagnostic {analyser.diagnostics[0]!r}"
                )
            return case
        except BaseException as exc:
            raise _GeneratedCaseFailure(case, exc) from exc

    source = rng.choice(never_cases)
    case = ("never", source)
    try:
        env = default_environment().child_scope()
        env.define_overload(Symbol("halt"), Overload((), (Never(),)))
        analyser = Analyser(env)
        typed = analyser.analyse(parse(source))
        if analyser.diagnostics:
            raise AssertionError(
                f"terminal Never produced diagnostics {analyser.diagnostics!r}"
            )
        if not typed:
            raise AssertionError("terminal Never discarded the typed prefix")
        return case
    except BaseException as exc:
        raise _GeneratedCaseFailure(case, exc) from exc


def _fuzz_smart_diagnostics(
    rng: random.Random,
    _iteration: int,
    _config: FuzzConfig,
) -> object:
    """Exercise typo suggestions, readable overload lists, and lint recovery."""
    mode = rng.randrange(10)
    if mode == 0:
        base = rng.choice(("increment", "decrement", "normalize", "flatten"))
        cut = rng.randrange(1, len(base) - 1)
        typo = base[:cut] + base[cut + 1 :]
        input_type, other_type = (
            (Integer, String) if rng.randrange(2) == 0 else (String, Integer)
        )
        env = Environment()
        env.define_overload(
            Symbol(base),
            Overload(
                (input_type,),
                (input_type,),
                param_names=(Symbol("value"),),
            ),
        )
        incompatible = base + "Text"
        env.define_overload(
            Symbol(incompatible),
            Overload(
                (other_type,),
                (other_type,),
                param_names=(Symbol("value"),),
            ),
        )
        case = ("suggestion", typo, base, incompatible, show(input_type))
        try:
            analyser = Analyser(env)
            analyser.analyse_node(
                BranchSet((AnalysisBranch(stack=TypeStack((input_type,))),)),
                ElementNode(Symbol(typo)),
            )
            if len(analyser.diagnostics) != 1:
                raise AssertionError(analyser.diagnostics)
            message = analyser.diagnostics[0]
            if base not in message or incompatible in message:
                raise AssertionError(f"bad suggestions: {message!r}")
            if "Function[" in message:
                raise AssertionError(f"legacy signature rendering: {message!r}")
            return case
        except BaseException as exc:
            raise _GeneratedCaseFailure(case, exc) from exc

    if mode == 1:
        env = Environment()
        name = Symbol("convert")
        env.define_overload(
            name,
            Overload((Integer,), (String,), param_names=(Symbol("value"),)),
        )
        env.define_overload(
            name,
            Overload((String,), (Integer,), param_names=(Symbol("text"),)),
        )
        case = ("overloads",)
        try:
            analyser = Analyser(env)
            analyser.analyse_node(
                BranchSet((AnalysisBranch(stack=TypeStack((NoneType(),))),)),
                ElementNode(name),
            )
            [message] = analyser.diagnostics
            if "available overloads:\n  - " not in message:
                raise AssertionError(f"overloads are not multiline: {message!r}")
            if "Function[" in message:
                raise AssertionError(f"legacy signature rendering: {message!r}")
            return case
        except BaseException as exc:
            raise _GeneratedCaseFailure(case, exc) from exc

    if mode == 2:
        env = Environment()
        name = Symbol("format")
        env.define_overload(
            name,
            Overload((Integer,), (String,), param_names=(Symbol("value"),)),
        )
        env.define_overload(
            name,
            Overload((String,), (String,), param_names=(Symbol("text"),)),
        )
        case = ("explicit-suggestion",)
        try:
            analyser = Analyser(env)
            analyser.analyse(parse("formt(1)"))
            [message] = analyser.diagnostics
            if "format(value: Integer) -> String" not in message:
                raise AssertionError(message)
            if "format(text: String)" in message:
                raise AssertionError(f"incompatible signature suggested: {message!r}")
            return case
        except BaseException as exc:
            raise _GeneratedCaseFailure(case, exc) from exc

    if mode == 3:
        env = Environment()
        env.define_overload(
            Symbol("convert"),
            Overload((Integer,), (String,), param_names=(Symbol("value"),)),
        )
        case = ("named-argument",)
        try:
            analyser = Analyser(env)
            analyser.analyse(parse("convert(vaule = 1)"))
            [message] = analyser.diagnostics
            if "unknown named argument 'vaule'" not in message:
                raise AssertionError(message)
            if "did you mean 'value'?" not in message:
                raise AssertionError(message)
            return case
        except BaseException as exc:
            raise _GeneratedCaseFailure(case, exc) from exc

    if mode == 4:
        source = rng.choice(
            (
                "1 as Integer",
                "1 as! Number",
                "1 move(value -> value)",
                "1 copy(value ->)",
            )
        )
        case = ("lint", source)
        try:
            analyser = Analyser()
            analyser.analyse(parse(source))
            if analyser.diagnostics:
                raise AssertionError(
                    f"lint became an error: {analyser.diagnostics!r}"
                )
            if len(analyser.lints) != 1:
                raise AssertionError(f"expected one lint: {analyser.lints!r}")
            if len(analyser.lint_findings) != 1:
                raise AssertionError(
                    f"missing structured lint: {analyser.lint_findings!r}"
                )
            finding = analyser.lint_findings[0]
            if finding.render() != analyser.lints[0]:
                raise AssertionError((finding, analyser.lints[0]))
            if finding.rewrite is None or not finding.rewrite.semantics_preserving:
                raise AssertionError(f"lint lacks a safe rewrite: {finding!r}")
            if (
                "remove" not in analyser.lints[0]
                and "instead" not in analyser.lints[0]
            ):
                raise AssertionError(
                    f"lint is not actionable: {analyser.lints[0]!r}"
                )
            return case
        except BaseException as exc:
            raise _GeneratedCaseFailure(case, exc) from exc

    if mode == 5:
        source = "fn -> Number =>\n  return 1\n  2\nend"
        expected = "unreachable-code"
    elif mode == 6:
        source = '1\nmatch =>\n  _ => "first"\n  1 => "second"\nend'
        expected = "unreachable-match-case"
    elif mode == 7:
        source = (
            '1\nmatch =>\n  1 => "first"\n  1 => "second"\n'
            '  _ => "other"\nend'
        )
        expected = "duplicate-match-case"
    elif mode == 8:
        source = '1\nmatch =>\n  1 || 1 => "one"\n  _ => "other"\nend'
        expected = "duplicate-pattern-alternative"
    else:
        source = rng.choice(
            (
                '1\nmatch =>\n  as x if > 0 => "positive"\n'
                '  _ => "other"\nend',
                '1\nmatch =>\n  as x(field) => "structured"\n'
                '  _ => "other"\nend',
            )
        )
        case = ("guarded-or-destructured-catchall", source)
        try:
            analyser = Analyser()
            analyser.analyse(parse(source))
            if analyser.diagnostics or analyser.lints or analyser.lint_findings:
                raise AssertionError(
                    (analyser.diagnostics, analyser.lints, analyser.lint_findings)
                )
            return case
        except BaseException as exc:
            raise _GeneratedCaseFailure(case, exc) from exc

    case = ("structured-lint", expected)
    try:
        analyser = Analyser()
        analyser.analyse(parse(source))
        if analyser.diagnostics:
            raise AssertionError(analyser.diagnostics)
        codes = tuple(finding.code for finding in analyser.lint_findings)
        if codes != (expected,):
            raise AssertionError((expected, codes, analyser.lints))
        finding = analyser.lint_findings[0]
        if finding.rewrite is None or not finding.rewrite.semantics_preserving:
            raise AssertionError(finding)
        return case
    except BaseException as exc:
        raise _GeneratedCaseFailure(case, exc) from exc


_VARIANT_MATCH_PREFIX = """variant Maybe =>
  Some => $value: Number end
  None => end
end
"""


def _fuzz_match_safety(
    rng: random.Random,
    _iteration: int,
    _config: FuzzConfig,
) -> object:
    """Check that accepted match programs cannot fail from static pattern mistakes."""
    mode = rng.randrange(19)
    if mode == 0:
        source = "1\nmatch =>\n  $x = _ => $x\nend"
        expected = [Decimal("1")]
        case = ("binding-catchall",)
    elif mode == 1:
        source = '1\nmatch =>\n  1 || _ => "first"\nend'
        expected = ["first"]
        case = ("or-catchall",)
    elif mode == 2:
        source = (
            _VARIANT_MATCH_PREFIX
            + 'Some(2)\nmatch =>\n  as :Some if 0 1 == => "never"\n'
            '  as :None => "none"\nend'
        )
        expected = None
        case = ("guarded-coverage",)
    elif mode == 3:
        source = (
            _VARIANT_MATCH_PREFIX
            + 'Some(2)\nmatch =>\n  as :Some(1) => "one"\n'
            '  as :None => "none"\nend'
        )
        expected = None
        case = ("restrictive-destructure",)
    elif mode == 4:
        source = (
            _VARIANT_MATCH_PREFIX
            + 'Some(2)\nmatch =>\n  as :Some(_, _) => "two"\n'
            '  as :None => "none"\nend'
        )
        expected = None
        case = ("destructure-arity",)
    elif mode == 5:
        source = "1\nmatch =>\n  1 || $x = _ => $x\n  _ => 0\nend"
        expected = None
        case = ("partial-or-binding",)
    elif mode == 6:
        source = (
            '$x = 1\n"abc"\nmatch =>\n'
            '  as x: String => $x length\n  _ => 0\nend'
        )
        expected = [3]
        case = ("binding-shadow",)
    elif mode == 7:
        source = (
            '"s"\nmatch =>\n'
            '  as x: Number if 1 1 == || as x: String => $x + 1\n'
            '  _ => 0\nend'
        )
        expected = None
        case = ("or-binding-types",)
    elif mode == 8:
        source = (
            '$x = (if 1 1 == => 1 else => "s" end)\n'
            '$x\nmatch =>\n  as :Number if 0 1 == => 0\n'
            '  _ => $x length\nend'
        )
        expected = None
        case = ("guarded-default-narrowing",)
    elif mode == 9:
        source = (
            '$x = (if 1 1 == => 2 else => "s" end)\n'
            '$x\nmatch =>\n  1 => 0\n  _ => $x length\nend'
        )
        expected = None
        case = ("literal-default-narrowing",)
    elif mode == 10:
        source = (
            '$x = (if 0 1 == => 1 else => "s" end)\n'
            '$x\nmatch =>\n  as :Number => 0\n'
            '  _ => $x length\nend'
        )
        expected = [1]
        case = ("irrefutable-default-narrowing",)
    elif mode == 11:
        source = (
            '$x = (if 0 1 == => 1 else => "s" end)\n'
            '$x\nmatch =>\n  1 || _ => $x + 1\nend'
        )
        expected = None
        case = ("or-catchall-narrowing",)
    elif mode == 12:
        source = (
            '$x = [1, "s"]\n$x\nmatch =>\n'
            '  [1, _] => $x sum\n  _ => 0\nend'
        )
        expected = None
        case = ("list-pattern-narrowing",)
    elif mode == 13:
        source = (
            '$x = (if 1 1 == => 1 else => "x" end)\n'
            '$y = (if 0 1 == => 1 else => "y" end)\n'
            '$x $y\nmatch =>\n'
            '  as :Number, as :Number => 0\n'
            '  _, _ => $x length\nend'
        )
        expected = None
        case = ("correlated-subject-narrowing",)
    elif mode == 14:
        source = (
            '$x = (if 0 1 == => 1 else => "x" end)\n'
            '$y = (if 1 1 == => 1 else => "y" end)\n'
            '$x $y\nmatch =>\n'
            '  _, as :Number => 0\n'
            '  _, _ => $x length\nend'
        )
        expected = [1]
        case = ("independent-subject-narrowing",)
    elif mode == 15:
        source = '1\nmatch =>\n  _ => "first"\n  1 => "second"\nend'
        expected = ["first"]
        case = ("wildcard-source-order",)
    elif mode == 16:
        source = (
            '1\nmatch =>\n  as x if > 0 => "positive"\n'
            '  1 => "one"\n  _ => "other"\nend'
        )
        expected = ["positive"]
        case = ("guarded-source-order",)
    elif mode == 17:
        source = '1 2\nmatch =>\n  $x = _, $x = _ => "same"\nend'
        expected = None
        case = ("repeated-binding-not-exhaustive",)
    else:
        source = (
            '1 2\nmatch =>\n  $x = _, $x = _ => "same"\n'
            '  _, _ => "different"\nend'
        )
        expected = ["different"]
        case = ("repeated-binding-fallback",)

    try:
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        if expected is None:
            if not analyser.diagnostics:
                raise AssertionError(
                    "unsafe match program was accepted without a diagnostic"
                )
            return case
        if analyser.diagnostics:
            raise AssertionError(analyser.diagnostics)
        actual = run(compile_program(typed))
        if actual != expected:
            raise AssertionError((expected, actual))
        return case
    except BaseException as exc:
        raise _GeneratedCaseFailure((case, source), exc) from exc


def _fuzz_soundness_boundaries(
    rng: random.Random,
    _iteration: int,
    _config: FuzzConfig,
) -> object:
    """Exercise static/runtime agreement at safety-sensitive boundaries."""
    mode = rng.randrange(10)
    case: object = ("mode", mode)
    try:
        if mode == 0:
            some_integer = N(Symbol("Some"), Integer)
            case = ("optional-subtyping", some_integer)
            if not subtype(some_integer, optional(Number)):
                raise AssertionError("Some covariance broke optional transitivity")
        elif mode == 1:
            some_integer = N(Symbol("Some"), Integer)
            case = ("optional-join", some_integer)
            if not same(merge_types(NoneType(), some_integer), optional(Integer)):
                raise AssertionError("None/Some join double-wrapped its payload")
        elif mode == 2:
            tagged_integer = Tagged(Integer, "x")
            case = ("tagged-decomposition", tagged_integer)
            if not subtype(
                U(tagged_integer, Tagged(Real, "x")),
                Tagged(Number, "x"),
            ):
                raise AssertionError("tagged union was not checked branchwise")
            if not subtype(
                I(tagged_integer, Tagged(Number, "y")),
                tagged_integer,
            ):
                raise AssertionError("intersection did not project a tagged member")
        elif mode == 3:
            ctx = Context()
            ctx.define_tag("km", TagKind.UNIT)
            ctx.define_tag("sec", TagKind.UNIT)
            seconds = Tagged(Integer, "sec")
            not_kilometres = Tagged(Integer, DataTag("km", absent=True))
            case = ("unit-laundering", seconds, not_kilometres)
            if assignable(seconds, not_kilometres, ctx):
                raise AssertionError("unit tag was laundered through an absent tag")
            merged = merge_types(Integer, Tagged(Integer, "km"), ctx)
            if not (
                assignable(Integer, merged, ctx)
                and assignable(Tagged(Integer, "km"), merged, ctx)
            ):
                raise AssertionError("contextual join erased a unit-tagged branch")
        elif mode == 4:
            source = """
tag #km as unit
1
match =>
  as :#km Number => "tagged"
  _ => "plain"
end
"""
            case = ("runtime-tag-pattern", source)
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            if analyser.diagnostics or run(compile_program(typed)) != ["plain"]:
                raise AssertionError(analyser.diagnostics)
        elif mode == 5:
            source = """
None
match =>
  as :None => "none"
  _ => "other"
end
"""
            case = ("runtime-none-pattern", source)
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            if analyser.diagnostics or run(compile_program(typed)) != ["none"]:
                raise AssertionError(analyser.diagnostics)
        elif mode == 6:
            source = """
object[T] Box =>
  public $value: T
end
Box("s")
match =>
  as :Box[Number] => "number"
  _ => "other"
end
"""
            case = ("runtime-generic-pattern", source)
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            if analyser.diagnostics or run(compile_program(typed)) != ["other"]:
                raise AssertionError(analyser.diagnostics)
        elif mode == 7:
            source = 'ValueError("x") as! Err'
            case = ("safe-checked-upcast", source)
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            if analyser.diagnostics:
                raise AssertionError(analyser.diagnostics)
            [value] = run(compile_program(typed))
            if getattr(value, "type_name", None) != "ValueError":
                raise AssertionError(value)
        elif mode == 8:
            source = """
try =>
  ValueFault("x") panic
handle Fault =>
  "caught"
end
"""
            case = ("trait-panic-handler", source)
            analyser = Analyser()
            analyser.analyse(parse(source))
            if not any("not a concrete runtime fault type" in item for item in analyser.diagnostics):
                raise AssertionError(analyser.diagnostics)
        else:
            target = rng.choice((-1, 2))
            case = ("invalid-jump", target)
            program = Program(
                FunctionCode((Instruction(OpCode.JUMP, target),), name="<main>")
            )
            try:
                run(program)
            except ValianceRuntimeError as exc:
                if "invalid jump target" not in str(exc):
                    raise
            else:
                raise AssertionError("invalid jump target was accepted")
        return case
    except BaseException as exc:
        raise _GeneratedCaseFailure(case, exc) from exc


def _fuzz_correctness_workloads(
    rng: random.Random,
    iteration: int,
    _config: FuzzConfig,
) -> object:
    """Exercise type algebra and realistic runtime representation boundaries."""
    mode = iteration % 20
    case: object = ("uninitialized", mode)
    try:
        if mode == 0:
            wrapped = rng.choice((False, True))
            argument = (
                f"Some({rng.randint(-20, 20)})"
                if wrapped
                else str(rng.randint(-20, 20))
            )
            source = f"""
define retryStatus(value: Integer?) -> String =>
  $value |
  match =>
    as :Some[Integer] => \"scheduled\"
    as :None => \"disabled\"
    _ => \"invalid\"
  end
end |
retryStatus({argument})
"""
            case = ("optional-workload", source)
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            if analyser.diagnostics:
                raise AssertionError(analyser.diagnostics)
            program = compile_program(typed)
            if run(program) != ["scheduled"] or run(loads(dumps(program))) != ["scheduled"]:
                raise AssertionError("present optional missed its Some branch")
        elif mode == 1:
            value = rng.randint(-20, 20)
            argument = rng.choice((str(value), f"OK({value})"))
            source = f"""
define status(value: Result[Number, ValueError]) -> String =>
  $value |
  match =>
    as :OK[Number] => \"accepted\"
    as :ValueError => \"rejected\"
    _ => \"invalid\"
  end
end |
status({argument})
"""
            case = ("result-workload", source)
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            if analyser.diagnostics:
                raise AssertionError(analyser.diagnostics)
            program = compile_program(typed)
            if run(program) != ["accepted"] or run(loads(dumps(program))) != ["accepted"]:
                raise AssertionError("successful Result missed its OK branch")
        elif mode == 2:
            source = """
define kind(value: Dict[String, Integer] | String) -> String =>
  $value |
  match =>
    as :Dict[String, Integer] => \"mapping\"
    _ => \"preset\"
  end
end |
kind(dict{\"retries\": 3})
"""
            case = ("dict-workload", source)
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            if analyser.diagnostics or run(compile_program(typed)) != ["mapping"]:
                raise AssertionError(analyser.diagnostics)
        elif mode == 3:
            source = """
define requireMatrix(values: Number+ | Number+2) -> Number+2 =>
  $values as! Number+2
end |
requireMatrix([] as Number+)
"""
            case = ("empty-rank-cast", source)
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            if analyser.diagnostics:
                raise AssertionError(analyser.diagnostics)
            try:
                run(compile_program(typed))
            except ValianceRuntimeError:
                pass
            else:
                raise AssertionError("flat empty list passed a matrix cast")
        elif mode == 4:
            left = U(Integer, Some(String))
            right = Some(U(Integer, String))
            case = ("some-normalization", left, right)
            if not same(left, right):
                raise AssertionError("raw and explicit Some branches did not normalize")
        elif mode == 5:
            value_error = N(Symbol("ValueError"))
            err = N(Symbol("Err"))
            narrow = Result(Integer, value_error)
            broad = Result(Number, err)
            case = ("result-covariance", narrow, broad)
            if not subtype(narrow, broad) or not assignable(narrow, broad):
                raise AssertionError("Result covariance failed")
        elif mode == 6:
            value_error = N(Symbol("ValueError"))
            left = rng.choice((Integer, OKType(Integer)))
            right = Result(String, value_error)
            merged = merge_types(left, right)
            case = ("result-join", left, right, merged)
            if not assignable(left, merged) or not assignable(right, merged):
                raise AssertionError("Result join was not an upper bound")
        elif mode == 7:
            source = """
define kind(value: Function[Number -> Number] | String) -> String =>
  $value |
  match =>
    as :Function[Number -> Number] => \"function\"
    _ => \"other\"
  end
end
"""
            case = ("non-reified-function-pattern", source)
            analyser = Analyser()
            analyser.analyse(parse(source))
            if not any(
                "cannot be checked at runtime" in item
                for item in analyser.diagnostics
            ):
                raise AssertionError(analyser.diagnostics)
        elif mode == 8:
            value = rng.randint(-20, 20)
            source = f"""
define delay(value: Integer?) -> Integer =>
  $value |
  match =>
    as :Some[Integer](seconds) => +($seconds, 1)
    _ => 0
  end
end |
delay({value})
"""
            case = ("raw-some-destructure", source)
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            if analyser.diagnostics:
                raise AssertionError(analyser.diagnostics)
            if run(compile_program(typed)) != [Decimal(value + 1)]:
                raise AssertionError("raw Some payload was not destructured")
        elif mode == 9:
            argument = rng.choice(
                (str(rng.randint(-20, 20)), 'ValueError(\"bad\")')
            )
            expected = "rejected" if argument.startswith("ValueError") else "accepted"
            source = f"""
define requireResult(value: Number | String | ValueError) -> Result[Number, ValueError] =>
  $value as! Result[Number, ValueError]
end |
requireResult({argument}) |
match =>
  as :OK[Number] => \"accepted\"
  as :Err => \"rejected\"
  _ => \"invalid\"
end
"""
            case = ("checked-result-workload", source)
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            if analyser.diagnostics:
                raise AssertionError(analyser.diagnostics)
            if run(compile_program(typed)) != [expected]:
                raise AssertionError("Result runtime discrimination disagreed")
        elif mode == 10:
            source = """
define force(value: Function[Number -> Number] | String) -> Function[Number -> Number] =>
  $value as! Function[Number -> Number]
end
"""
            case = ("non-reified-function-cast", source)
            analyser = Analyser()
            analyser.analyse(parse(source))
            if not any(
                "cannot be checked at runtime" in item
                for item in analyser.diagnostics
            ):
                raise AssertionError(analyser.diagnostics)
        elif mode == 11:
            source = """
trait Vehicle => end |
object Car => $model: String end |
object Car as Vehicle => end |
Car("sedan") |
match =>
  as :Vehicle => "vehicle"
  _ => "other"
end
"""
            case = ("user-trait-pattern", source)
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            if analyser.diagnostics or run(compile_program(typed)) != ["vehicle"]:
                raise AssertionError(analyser.diagnostics)
        elif mode == 12:
            source = """
trait Vehicle => end |
object Car => $model: String end |
object Car as Vehicle => end |
object[T] Box => $value: T end |
Box(Car("sedan")) |
match =>
  as :Box[Vehicle] => "vehicle box"
  _ => "other"
end
"""
            case = ("covariant-generic-pattern", source)
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            if analyser.diagnostics or run(compile_program(typed)) != ["vehicle box"]:
                raise AssertionError(analyser.diagnostics)
        elif mode == 13:
            source = """
define state(value: Integer?) -> String =>
  $value |
  match =>
    as :Some[Integer] => "some"
    as :None => "none"
  end
end |
state(1)
"""
            case = ("exhaustive-optional", source)
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            if analyser.diagnostics or run(compile_program(typed)) != ["some"]:
                raise AssertionError(analyser.diagnostics)
        elif mode == 14:
            source = """
object Problem => $message: String end |
object Problem as Err => end |
Problem("bad") |
match =>
  as :Err => "error"
  _ => "success"
end
"""
            case = ("user-error-trait", source)
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            if analyser.diagnostics or run(compile_program(typed)) != ["error"]:
                raise AssertionError(analyser.diagnostics)
        elif mode == 15:
            source = """
object Abort => $message: String end |
object Abort as Fault => end |
Abort("stop") |
match =>
  as :Fault => "fault"
  _ => "ordinary"
end
"""
            case = ("user-fault-trait", source)
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            if analyser.diagnostics or run(compile_program(typed)) != ["fault"]:
                raise AssertionError(analyser.diagnostics)
        elif mode == 16:
            source = """
trait Vehicle => end |
trait Electric as Vehicle => end |
object Car => $model: String end |
object Car as Electric => end |
Car("sedan") |
match =>
  as :Vehicle => "vehicle"
  _ => "other"
end
"""
            case = ("transitive-trait-runtime", source)
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            if analyser.diagnostics or run(compile_program(typed)) != ["vehicle"]:
                raise AssertionError(analyser.diagnostics)
        elif mode == 17:
            source = """
object Problem => $message: String end |
object Problem as Err => end |
define state(value: Result[Integer, Problem]) -> String =>
  $value |
  match =>
    as :OK[Integer] => "success"
    as :Problem => "error"
  end
end |
state(1)
"""
            case = ("exhaustive-result", source)
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            if analyser.diagnostics or run(compile_program(typed)) != ["success"]:
                raise AssertionError(analyser.diagnostics)
        elif mode == 18:
            source = """
trait[T] Producer => end |
object[T] Box => $value: T end |
object[T] Box as Producer[T] => end |
Box(1) |
match =>
  as :Producer[String] => "wrong"
  as :Producer[Integer] => "right"
  _ => "other"
end
"""
            case = ("generic-trait-projection", source)
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            if analyser.diagnostics or run(compile_program(typed)) != ["right"]:
                raise AssertionError(analyser.diagnostics)
        else:
            source = """
trait[T] Source => end |
trait[T] Producer as Source[T] => end |
object[T] Box => $value: T end |
object[T] Box as Producer[T] => end |
Box(1) |
match =>
  as :Source[String] => "wrong"
  as :Source[Integer] => "right"
  _ => "other"
end
"""
            case = ("transitive-generic-trait-projection", source)
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            if analyser.diagnostics or run(compile_program(typed)) != ["right"]:
                raise AssertionError(analyser.diagnostics)
        return case
    except BaseException as exc:
        raise _GeneratedCaseFailure(case, exc) from exc


TARGETS: dict[str, Target] = {
    "lexer-parser": _fuzz_lexer_parser,
    "source-mutations": _fuzz_source_mutations,
    "parser-depth": _fuzz_parser_depth,
    "valid-programs": _fuzz_valid_programs,
    "serialization": _fuzz_serialization_roundtrip,
    "optimizer": _fuzz_optimizer,
    "numeric-booleans": _fuzz_numeric_boolean_serialization,
    "malformed-bytecode": _fuzz_malformed_bytecode,
    "bytecode-depth": _fuzz_bytecode_depth,
    "runtime-bytecode": _fuzz_runtime_bytecode,
    "type-relations": _fuzz_type_relations,
    "type-algebra": _fuzz_type_algebra,
    "structural-types": _fuzz_structural_types,
    "analyser-never": _fuzz_analyser_never_recovery,
    "smart-diagnostics": _fuzz_smart_diagnostics,
    "match-safety": _fuzz_match_safety,
    "soundness-boundaries": _fuzz_soundness_boundaries,
    "correctness-workloads": _fuzz_correctness_workloads,
}


def corpus_sources(root: Path) -> list[str]:
    """Load checked-in Valiance samples for external mutation fuzzers."""
    return [
        path.read_text(encoding="utf-8")
        for path in sorted((root / "samples").glob("*.vlnc"))
    ]
