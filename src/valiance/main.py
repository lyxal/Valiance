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
from valiance.runtime import (
    BytecodeFormatError,
    CompileError,
    RuntimeError,
    compile_program,
    dumps,
    loads,
    run,
)
from valiance.runtime_values import is_list_like

HELP = """usage: valiance <file>
       valiance -c <code>

source:
  valiance <file>          lex, parse, and analyse a Valiance source file
  valiance -c <code>       lex, parse, and analyse inline Valiance code
  valiance --run <file>    compile and execute a Valiance source file
  valiance --run -c <code> compile and execute inline Valiance code
  --emit-bytecode <file>   save compiled bytecode to a binary file
  --run-bytecode <file>    execute a saved bytecode file
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

    if parsed.run_bytecode is not None:
        return _run_bytecode_file(
            parsed.run_bytecode,
            implicit_output=parsed.implicit_output,
        )

    source = parsed.code
    source_file: Path | None = None
    if source is None:
        source_file = Path(parsed.file)
        source = _read_source_file(parsed.file)
        if source is None:
            return 1

    return _run_source(
        source,
        execute=parsed.run,
        bytecode_output=parsed.emit_bytecode,
        source_file=source_file,
        implicit_output=parsed.implicit_output,
    )


def _parse_args(args: list[str]) -> argparse.Namespace | None:
    parser = argparse.ArgumentParser(
        prog="valiance",
        add_help=False,
    )
    parser.add_argument("-c", "--code")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--emit-bytecode")
    parser.add_argument("--run-bytecode")
    parser.add_argument("--implicit-output", action="store_true")
    parser.add_argument("file", nargs="?")
    parser.add_argument("-h", "--help", action="store_true")

    try:
        parsed = parser.parse_args(args)
    except SystemExit:
        return None

    if parsed.help:
        return None
    if parsed.run_bytecode is not None and (
        parsed.code is not None
        or parsed.file is not None
        or parsed.emit_bytecode is not None
        or parsed.run
    ):
        print(
            "error: --run-bytecode cannot be combined with source input, "
            "--run, or --emit-bytecode",
            file=sys.stderr,
        )
        return None
    if parsed.code is not None and parsed.file is not None:
        print("error: pass either a file or --code, not both", file=sys.stderr)
        return None
    if parsed.code is None and parsed.file is None and parsed.run_bytecode is None:
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
    bytecode_output: str | None = None,
    source_file: Path | None = None,
    implicit_output: bool = False,
) -> int:
    try:
        tokens = lex(source)
        program = Parser(tokens).parse_program()
        analyser = Analyser(source_file=source_file)
        typed = analyser.analyse(program)
        if execute or bytecode_output is not None:
            if analyser.diagnostics:
                for diagnostic in analyser.diagnostics:
                    print(f"error: {diagnostic}", file=sys.stderr)
                return 1
            bytecode = compile_program(typed)
            if bytecode_output is not None:
                output_path = _resolve_bytecode_output_path(
                    bytecode_output,
                    source_file,
                )
                _write_bytecode_file(output_path, dumps(bytecode))
            if execute:
                _run_bytecode(bytecode, implicit_output=implicit_output)
            return 0
    except (
        BytecodeFormatError,
        LexError,
        OSError,
        ParseError,
        CompileError,
        RuntimeError,
    ) as exc:
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


def _run_bytecode_file(filename: str, *, implicit_output: bool = False) -> int:
    try:
        bytecode = loads(Path(filename).read_bytes())
        _run_bytecode(bytecode, implicit_output=implicit_output)
    except (BytecodeFormatError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _resolve_bytecode_output_path(filename: str, source_file: Path | None) -> Path:
    output_path = Path(filename)
    if source_file is not None and not output_path.is_absolute():
        return source_file.parent / output_path
    return output_path


def _write_bytecode_file(filename: str | Path, data: bytes) -> None:
    Path(filename).write_bytes(data)


def _run_bytecode(bytecode, *, implicit_output: bool = False) -> None:
    output = _OutputTracker()
    stack = run(bytecode, output=output)
    if implicit_output and not output.did_print:
        print(_format_stack(stack))


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
    if is_list_like(value):
        return "<lazy list>"
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
