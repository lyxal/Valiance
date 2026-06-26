from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from valiance.analysis import Analyser
from valiance.asts import pretty_ast
from valiance.parsing import LexError, ParseError, Parser, lex
from valiance.runtime import CompileError, RuntimeError, compile_program, run

HELP = """usage: valiance <file>
       valiance -c <code>

source:
  valiance <file>          lex, parse, and analyse a Valiance source file
  valiance -c <code>       lex, parse, and analyse inline Valiance code
  valiance --run <file>    compile and execute a Valiance source file
  valiance --run -c <code> compile and execute inline Valiance code
  --implicit-output        print the final stack if execution prints nothing
"""


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(HELP)
        return 0

    parsed = _parse_args(args)
    if parsed is None:
        print(HELP)
        return 2

    source = parsed.code
    if source is None:
        source = _read_source_file(parsed.file)
        if source is None:
            return 1

    return _run_source(
        source,
        execute=parsed.run,
        implicit_output=parsed.implicit_output,
    )


def _parse_args(args: list[str]) -> argparse.Namespace | None:
    parser = argparse.ArgumentParser(
        prog="valiance",
        add_help=False,
    )
    parser.add_argument("-c", "--code")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--implicit-output", action="store_true")
    parser.add_argument("file", nargs="?")
    parser.add_argument("-h", "--help", action="store_true")

    try:
        parsed = parser.parse_args(args)
    except SystemExit:
        return None

    if parsed.help:
        return None
    if parsed.code is not None and parsed.file is not None:
        print("error: pass either a file or --code, not both", file=sys.stderr)
        return None
    if parsed.code is None and parsed.file is None:
        return None
    return parsed


def _read_source_file(filename: str) -> str | None:
    try:
        return Path(filename).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: could not read {filename!r}: {exc}", file=sys.stderr)
        return None


def _run_source(
    source: str,
    *,
    execute: bool = False,
    implicit_output: bool = False,
) -> int:
    try:
        tokens = lex(source)
        program = Parser(tokens).parse_program()
        analyser = Analyser()
        typed = analyser.analyse(program)
        if execute:
            if analyser.diagnostics:
                for diagnostic in analyser.diagnostics:
                    print(f"error: {diagnostic}", file=sys.stderr)
                return 1
            bytecode = compile_program(typed)
            output = _OutputTracker()
            stack = run(bytecode, output=output)
            if implicit_output and not output.did_print:
                print(_format_stack(stack))
            return 0
    except (LexError, ParseError, CompileError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for diagnostic in analyser.diagnostics:
        print(f"error: {diagnostic}", file=sys.stderr)

    print("Parsed AST:")
    print(pretty_ast(program))
    print()
    print("Typed AST:")
    print(pretty_ast(typed))
    return 0


class _OutputTracker:
    def __init__(self) -> None:
        self.did_print = False

    def __call__(self, value: str) -> None:
        self.did_print = True
        print(value)


def _format_stack(stack: list[Any]) -> str:
    if not stack:
        return "Stack []"
    lines = ["Stack ["]
    for index, value in enumerate(stack):
        lines.append(f"  {index}: {_format_value(value)}")
    lines.append("]")
    return "\n".join(lines)


def _format_value(value: Any) -> str:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return format(value.quantize(Decimal(1)), "f")
        return format(value.normalize(), "f")
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    if isinstance(value, tuple):
        inner = ", ".join(_format_value(item) for item in value)
        if len(value) == 1:
            inner += ","
        return f"({inner})"
    if isinstance(value, dict):
        items = ", ".join(
            f"{_format_value(key)}: {_format_value(item)}"
            for key, item in value.items()
        )
        return "{" + items + "}"
    return repr(value)


if __name__ == "__main__":
    raise SystemExit(main())
