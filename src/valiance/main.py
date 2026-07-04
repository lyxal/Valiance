from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from valiance.analysis import Analyser
from valiance.asts import pretty_ast, typed_source
from valiance.diagnostics import from_exception, from_message, render
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
from valiance.runtime_values import DIAGNOSTIC_LIST_PREVIEW_LIMIT, format_runtime_value

DEFAULT_BYTECODE_FILENAME = "out.vbc"
DEFAULT_BYTECODE_SUFFIX = ".vbc"

_SOURCE_ACTIONS = {"compile", "run", "parse", "analyse", "analyze", "annotate"}
_BYTECODE_ACTIONS = {"run-bytecode"}
_ACTIONS = _SOURCE_ACTIONS | _BYTECODE_ACTIONS

HELP = """usage: valiance [compile] <file> [-o <file>]
       valiance [compile] -c <code> [-o <file>]
       valiance run <file>
       valiance run -c <code>
       valiance run-bytecode <file>
       valiance parse <file>
       valiance analyse <file>
       valiance annotate <file>

actions:
  compile             compile source to bytecode; default action
  run                 compile and execute source without writing bytecode
  run-bytecode        execute an existing bytecode file
  parse               print the parsed AST
  analyse             print the typed AST
  annotate            print source with inferred type annotations

options:
  -c, --code <code>   use inline Valiance code instead of a source file
  -o, --output <file> write compiled bytecode to this file
  --implicit-output   print the final stack if execution prints nothing
  --preview-lists     preview lazy lists instead of forcing full output
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

    if parsed.action == "run-bytecode":
        return _run_bytecode_file(
            parsed.bytecode_file,
            implicit_output=parsed.implicit_output,
            preview_lists=parsed.preview_lists,
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
        action=parsed.action,
        bytecode_output=parsed.output,
        source_file=source_file,
        implicit_output=parsed.implicit_output,
        preview_lists=parsed.preview_lists,
    )


def _parse_args(args: list[str]) -> argparse.Namespace | None:
    action = "compile"
    if args and args[0] in _ACTIONS:
        action = "analyse" if args[0] == "analyze" else args[0]
        args = args[1:]

    parser = argparse.ArgumentParser(
        prog="valiance",
        add_help=False,
    )
    parser.add_argument("-c", "--code")
    parser.add_argument("-o", "--output")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--emit-bytecode", dest="legacy_output")
    parser.add_argument("--run-bytecode", dest="legacy_bytecode_file")
    parser.add_argument("--implicit-output", action="store_true")
    parser.add_argument("--preview-lists", action="store_true")
    parser.add_argument("file", nargs="?")
    parser.add_argument("-h", "--help", action="store_true")

    try:
        parsed = parser.parse_args(args)
    except SystemExit:
        return None

    if parsed.help:
        return None

    if parsed.run:
        if action != "compile":
            print("error: --run cannot be combined with an action", file=sys.stderr)
            return None
        action = "run"
    if parsed.legacy_bytecode_file is not None:
        if action != "compile":
            print(
                "error: --run-bytecode cannot be combined with an action",
                file=sys.stderr,
            )
            return None
        action = "run-bytecode"
    if parsed.legacy_output is not None:
        if parsed.output is not None:
            print(
                "error: pass either --output or --emit-bytecode, not both",
                file=sys.stderr,
            )
            return None
        parsed.output = parsed.legacy_output

    parsed.action = action
    parsed.bytecode_file = parsed.legacy_bytecode_file

    if parsed.action == "run-bytecode":
        if parsed.code is not None or parsed.output is not None or parsed.run:
            print(
                "error: run-bytecode cannot be combined with source input, "
                "--run, or bytecode output",
                file=sys.stderr,
            )
            return None
        if parsed.bytecode_file is not None and parsed.file is not None:
            print(
                "error: pass either run-bytecode <file> or --run-bytecode <file>, "
                "not both",
                file=sys.stderr,
            )
            return None
        parsed.bytecode_file = parsed.bytecode_file or parsed.file
        if parsed.bytecode_file is None:
            print("error: run-bytecode requires a bytecode file", file=sys.stderr)
            return None
        return parsed

    if parsed.legacy_bytecode_file is not None:
        print(
            "error: --run-bytecode cannot be combined with source actions",
            file=sys.stderr,
        )
        return None
    if parsed.output is not None and parsed.action != "compile":
        print("error: bytecode output is only valid for compile", file=sys.stderr)
        return None
    if parsed.implicit_output and parsed.action not in {"run"}:
        print("error: --implicit-output is only valid for run actions", file=sys.stderr)
        return None
    if parsed.preview_lists and parsed.action not in {"run", "run-bytecode"}:
        print("error: --preview-lists is only valid for run actions", file=sys.stderr)
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
    action: str = "compile",
    bytecode_output: str | None = None,
    source_file: Path | None = None,
    implicit_output: bool = False,
    preview_lists: bool = False,
) -> int:
    try:
        tokens = lex(source)
        program = Parser(tokens).parse_program()
        if action == "parse":
            print("Parsed AST:")
            print(pretty_ast(program))
            return 0

        analyser = Analyser(source_file=source_file)
        typed = analyser.analyse(program)

        if action == "analyse":
            for diagnostic in analyser.diagnostics:
                _print_diagnostic(
                    from_message("Type error", diagnostic),
                    source,
                    source_file,
                )
            print("Typed AST:")
            print(pretty_ast(typed))
            return 0

        if action == "annotate":
            for diagnostic in analyser.diagnostics:
                _print_diagnostic(
                    from_message("Type error", diagnostic),
                    source,
                    source_file,
                )
            print(typed_source(typed))
            return 0

        if analyser.diagnostics:
            for diagnostic in analyser.diagnostics:
                _print_diagnostic(
                    from_message("Type error", diagnostic),
                    source,
                    source_file,
                )
            return 1

        bytecode = compile_program(typed)
        if action == "run":
            _run_bytecode(
                bytecode,
                implicit_output=implicit_output,
                preview_lists=preview_lists,
            )
            return 0

        if action != "compile":
            print(f"error: unknown action {action!r}", file=sys.stderr)
            return 1
        output_path = _resolve_bytecode_output_path(bytecode_output, source_file)
        _write_bytecode_file(output_path, dumps(bytecode))
        print(f"Wrote bytecode: {output_path}")
        return 0
    except (
        BytecodeFormatError,
        LexError,
        OSError,
        ParseError,
        CompileError,
        RuntimeError,
    ) as exc:
        _print_exception_diagnostic(exc, source=source, source_file=source_file)
        return 1


def _run_bytecode_file(
    filename: str,
    *,
    implicit_output: bool = False,
    preview_lists: bool = False,
) -> int:
    try:
        bytecode = loads(Path(filename).read_bytes())
        _run_bytecode(
            bytecode,
            implicit_output=implicit_output,
            preview_lists=preview_lists,
        )
    except (BytecodeFormatError, OSError, RuntimeError) as exc:
        _print_exception_diagnostic(exc)
        return 1
    return 0


def _resolve_bytecode_output_path(
    filename: str | None,
    source_file: Path | None,
) -> Path:
    if filename is None:
        if source_file is not None:
            return source_file.with_suffix(DEFAULT_BYTECODE_SUFFIX)
        return Path(DEFAULT_BYTECODE_FILENAME)
    output_path = Path(filename)
    if source_file is not None and not output_path.is_absolute():
        return source_file.parent / output_path
    return output_path


def _write_bytecode_file(filename: str | Path, data: bytes) -> None:
    Path(filename).write_bytes(data)


def _print_exception_diagnostic(
    exc: BaseException,
    *,
    source: str | None = None,
    source_file: Path | None = None,
) -> None:
    if isinstance(exc, LexError):
        stage = "Lex error"
    elif isinstance(exc, ParseError):
        stage = "Parse error"
    elif isinstance(exc, CompileError):
        stage = "Compile error"
    elif isinstance(exc, RuntimeError):
        stage = "Runtime error"
    else:
        stage = "Error"
    _print_diagnostic(from_exception(stage, exc), source, source_file)


def _print_diagnostic(
    diagnostic,
    source: str | None = None,
    source_file: Path | None = None,
) -> None:
    print(render(diagnostic, source, source_file=source_file), file=sys.stderr)


def _run_bytecode(
    bytecode,
    *,
    implicit_output: bool = False,
    preview_lists: bool = False,
) -> None:
    output = _OutputTracker()
    preview_limit = DIAGNOSTIC_LIST_PREVIEW_LIMIT if preview_lists else None
    stack = run(bytecode, output=output, list_preview_limit=preview_limit)
    if implicit_output and not output.did_print:
        print(_format_stack(stack, preview_limit=preview_limit))


class _OutputTracker:
    def __init__(self) -> None:
        self.did_print = False

    def __call__(self, value: str) -> None:
        self.did_print = True
        print(value, end="")


def _format_stack(stack: list[Any], *, preview_limit: int | None = None) -> str:
    if not stack:
        return "Stack []"
    lines = ["Stack ["]
    for index, value in enumerate(stack):
        lines.append(f"  {index}: {_format_value(value, preview_limit=preview_limit)}")
    lines.append("]")
    return "\n".join(lines)


def _format_value(value: Any, *, preview_limit: int | None = None) -> str:
    return format_runtime_value(
        value,
        quote_strings=True,
        tuple_single_comma=True,
        lazy_preview_limit=preview_limit,
    )


if __name__ == "__main__":
    raise SystemExit(main())
