"""Audit whether compiled call sites retain static resolution metadata."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from valiance.analysis import Analyser
from valiance.modules_system.modules import ModuleLoader
from valiance.parsing import parse
from valiance.runtime import compile_program
from valiance.runtime.bytecode import FunctionCode, FunctionSetCode, OpCode, Program


def audit_program(program: Program) -> dict[str, Any]:
    """Count resolved and dynamic calls recursively across one program."""
    counters: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = {}

    def record(kind: str, code: FunctionCode, index: int, name: str | None = None) -> None:
        counters[kind] += 1
        bucket = examples.setdefault(kind, [])
        if len(bucket) < 8:
            bucket.append({"function": code.name, "instruction": index, "name": name})

    def visit(code: FunctionCode) -> None:
        for index, instruction in enumerate(code.instructions):
            if instruction.op is OpCode.CALL_RESOLVED_ELEMENT:
                record("resolved-element", code, index, instruction.arg.name)
            elif instruction.op is OpCode.CALL:
                prior = code.instructions[index - 1] if index else None
                if prior is not None and prior.op is OpCode.LOAD_ELEMENT:
                    record("dynamic-loaded-element", code, index, str(prior.arg))
                else:
                    record("dynamic-callable", code, index)
            payload = instruction.arg
            if isinstance(payload, FunctionCode):
                visit(payload)
            elif isinstance(payload, FunctionSetCode):
                for overload in payload.overloads:
                    visit(overload)
            elif instruction.op is OpCode.JUMP_IF_MATCH:
                patterns, _target = payload
                for pattern in patterns:
                    visit_pattern(pattern)

    def visit_pattern(pattern: object) -> None:
        if not isinstance(pattern, tuple) or not pattern:
            return
        kind = pattern[0]
        if kind == "guard" and len(pattern) == 2 and isinstance(pattern[1], FunctionCode):
            visit(pattern[1])
        elif kind in {"or", "list"} and len(pattern) == 2:
            for child in pattern[1]:
                visit_pattern(child)
        elif kind == "bind" and len(pattern) == 3:
            visit_pattern(pattern[2])
        elif kind == "type" and len(pattern) == 5:
            for child in pattern[3]:
                visit_pattern(child)
            if isinstance(pattern[4], FunctionCode):
                visit(pattern[4])

    visit(program.main)
    return {"counts": dict(sorted(counters.items())), "examples": examples}


def compile_source(source: str, source_file: Path | None = None) -> Program:
    """Analyse and compile source while retaining its module-resolution context."""
    analyser = Analyser(module_loader=ModuleLoader(), source_file=source_file)
    typed = analyser.analyse(parse(source))
    if analyser.diagnostics:
        raise RuntimeError("; ".join(analyser.diagnostics))
    return compile_program(typed)


def main(argv: list[str] | None = None) -> int:
    """Audit a source file or command-line source expression."""
    parser = argparse.ArgumentParser(description=__doc__)
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--file", type=Path)
    source_group.add_argument("--code")
    args = parser.parse_args(argv)
    if args.file is not None:
        source = args.file.read_text(encoding="utf-8")
        source_file = args.file.resolve()
    else:
        source = args.code
        source_file = None
    report = audit_program(compile_source(source, source_file))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
