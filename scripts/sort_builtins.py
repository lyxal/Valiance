#!/usr/bin/env python3
"""Sort contiguous runs of @builtin-decorated functions by builtin name.

The sorter uses Python's AST to find top-level functions and literal builtin
names. It preserves each complete function block, including stacked @builtin
and @alias decorators. It deliberately does not move functions across other
Python statements, which avoids moving a declaration above a helper used by a
decorator.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path("src/valiance/elements/builtins.py")


@dataclass(frozen=True)
class BuiltinBlock:
    start: int  # zero-based, inclusive
    end: int  # zero-based, exclusive
    name: str
    source: tuple[str, ...]


def decorator_name(decorator: ast.expr) -> str | None:
    """Return a literal name from @builtin("name", ...), if present."""
    if not isinstance(decorator, ast.Call):
        return None
    function = decorator.func
    is_builtin = (
        isinstance(function, ast.Name) and function.id == "builtin"
    ) or (
        isinstance(function, ast.Attribute) and function.attr == "builtin"
    )
    if not is_builtin or not decorator.args:
        return None
    value = decorator.args[0]
    return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None


def block_for(node: ast.FunctionDef | ast.AsyncFunctionDef, lines: list[str]) -> BuiltinBlock | None:
    names = [name for decorator in node.decorator_list if (name := decorator_name(decorator)) is not None]
    if not names:
        return None
    if len(set(names)) != 1:
        joined = ", ".join(repr(name) for name in names)
        raise ValueError(f"line {node.lineno}: stacked @builtin decorators use different names: {joined}")
    start_line = min((decorator.lineno for decorator in node.decorator_list), default=node.lineno)
    start = start_line - 1
    end = node.end_lineno
    assert end is not None
    return BuiltinBlock(start, end, names[0], tuple(lines[start:end]))


def only_trivia(lines: list[str], start: int, end: int) -> bool:
    """Whether lines contain only blanks or comments."""
    return all(not line.strip() or line.lstrip().startswith("#") for line in lines[start:end])


def sorted_source(source: str, filename: str) -> str:
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source, filename=filename)
    blocks = [
        block
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and (block := block_for(node, lines)) is not None
    ]

    # Sort only adjacent runs. Comments and blank lines are retained in place;
    # real statements form barriers so helper/decorator dependencies stay safe.
    runs: list[list[BuiltinBlock]] = []
    for block in blocks:
        if runs and only_trivia(lines, runs[-1][-1].end, block.start):
            runs[-1].append(block)
        else:
            runs.append([block])

    result = list(lines)
    for run in reversed(runs):
        if len(run) < 2:
            continue
        start, end = run[0].start, run[-1].end
        gaps = [lines[left.end:right.start] for left, right in zip(run, run[1:])]
        ordered = sorted(run, key=lambda block: (block.name.casefold(), block.name))
        replacement: list[str] = []
        for index, block in enumerate(ordered):
            replacement.extend(block.source)
            if index < len(gaps):
                replacement.extend(gaps[index])
        result[start:end] = replacement
    return "".join(result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--check", action="store_true", help="report disorder without rewriting")
    args = parser.parse_args()

    original = args.path.read_text(encoding="utf-8")
    try:
        updated = sorted_source(original, str(args.path))
    except (SyntaxError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if updated == original:
        print(f"Already sorted: {args.path}")
        return 0

    if args.check:
        print(f"Builtins are not alphabetically sorted: {args.path}", file=sys.stderr)
        sys.stderr.writelines(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=str(args.path),
                tofile=str(args.path) + " (sorted)",
            )
        )
        return 1

    args.path.write_text(updated, encoding="utf-8")
    print(f"Sorted: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
