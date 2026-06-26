from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from valiance.analysis import Analyser
from valiance.asts import pretty_ast
from valiance.parsing import LexError, ParseError, Parser, lex

HELP = """usage: valiance <file>
       valiance -c <code>

source:
  valiance <file>          lex, parse, and analyse a Valiance source file
  valiance -c <code>       lex, parse, and analyse inline Valiance code
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

    return _run_source(source)


def _parse_args(args: list[str]) -> argparse.Namespace | None:
    parser = argparse.ArgumentParser(
        prog="valiance",
        add_help=False,
    )
    parser.add_argument("-c", "--code")
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


def _run_source(source: str) -> int:
    try:
        tokens = lex(source)
        program = Parser(tokens).parse_program()
        analyser = Analyser()
        typed = analyser.analyse(program)
    except (LexError, ParseError) as exc:
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


if __name__ == "__main__":
    raise SystemExit(main())
