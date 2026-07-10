"""Deterministic fuzz targets for Valiance's compiler and runtime boundaries.

The fuzzers intentionally use only the Python standard library so they can run in
an offline checkout.  Every case receives an independently derived random seed,
which makes a failure reproducible by target, base seed, and iteration number.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from valiance.analysis import Analyser
from valiance.asts import ASTNode, pretty_ast
from valiance.parsing import LexError, ParseError, lex, parse
from valiance.parsing.lexer import TokenKind
from valiance.runtime import BytecodeFormatError, compile_program, dumps, loads, run
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
    AtLeastArray,
    AtLeastList,
    Boolean,
    DataTag,
    Exact,
    ExactArray,
    ExactList,
    Integer,
    N,
    NoneType,
    Number,
    Real,
    Row,
    RuntimeTypePattern,
    String,
    Tagged,
    Tup,
    U,
    UnionDispatchBranch,
    Variance,
    assignable,
    merge_types,
    normalize,
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


def _program_case(rng: random.Random, max_depth: int) -> _ProgramCase:
    mode = rng.randrange(7)
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
    )


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
        if same(left, right) != same(right, left):
            raise AssertionError("type equality is not symmetric")
        if not show(normalized):
            raise AssertionError("type display produced an empty string")

        duplicate_union = U(left, left)
        if not same(duplicate_union, normalized):
            raise AssertionError("a duplicate union did not normalize to its member")

        merged = merge_types(left, right)
        if not assignable(left, merged) or not assignable(right, merged):
            raise AssertionError("merged type does not accept both inputs")

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


TARGETS: dict[str, Target] = {
    "lexer-parser": _fuzz_lexer_parser,
    "source-mutations": _fuzz_source_mutations,
    "valid-programs": _fuzz_valid_programs,
    "serialization": _fuzz_serialization_roundtrip,
    "malformed-bytecode": _fuzz_malformed_bytecode,
    "type-relations": _fuzz_type_relations,
}


def corpus_sources(root: Path) -> list[str]:
    """Load checked-in Valiance samples for external mutation fuzzers."""
    return [
        path.read_text(encoding="utf-8")
        for path in sorted((root / "samples").glob("*.vlnc"))
    ]
