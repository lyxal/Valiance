"""Clean-versus-incremental validation helpers."""
from __future__ import annotations
import hashlib, subprocess, sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from valiance.analysis import Analyser
from valiance.incremental import CompilationCoordinator
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run

@dataclass(frozen=True, slots=True)
class ValidationResult:
    diagnostics: tuple[str, ...]
    bytecode: bytes
    runtime: object

def clean_result(source_file: Path) -> ValidationResult:
    analyser = Analyser(source_file=source_file)
    typed = analyser.analyse(parse(source_file.read_text(encoding="utf-8")))
    diagnostics = tuple(str(item) for item in analyser.diagnostics)
    if diagnostics:
        return ValidationResult(diagnostics, b"", None)
    bytecode = dumps(compile_program(typed))
    return ValidationResult((), bytecode, run(loads(bytecode)))

def incremental_result(source_file: Path, root: Path) -> ValidationResult:
    output = root / "bin/main.vbc"
    try:
        CompilationCoordinator(root).build_executable(source_file, output, target_identity="validation:main")
    except RuntimeError as exc:
        return ValidationResult((str(exc),), b"", None)
    bytecode = output.read_bytes()
    return ValidationResult((), bytecode, run(loads(bytecode)))

def assert_equivalent(source_file: Path, root: Path) -> None:
    clean = clean_result(source_file)
    incremental = incremental_result(source_file, root)
    if bool(clean.diagnostics) != bool(incremental.diagnostics):
        raise AssertionError((clean.diagnostics, incremental.diagnostics))
    if not clean.diagnostics and (clean.bytecode != incremental.bytecode or clean.runtime != incremental.runtime):
        raise AssertionError("incremental result differs from clean build")

def history_independent(source_file: Path, root: Path, history: Iterable[str]) -> None:
    for source in history:
        source_file.write_text(source, encoding="utf-8")
        incremental_result(source_file, root)
    assert_equivalent(source_file, root)

def process_independent(source_file: Path, root: Path) -> None:
    first = incremental_result(source_file, root)
    code = "from pathlib import Path; from tools.incremental_validation import incremental_result; import hashlib,sys; print(hashlib.sha256(incremental_result(Path(sys.argv[1]),Path(sys.argv[2])).bytecode).hexdigest())"
    completed = subprocess.run([sys.executable, "-c", code, str(source_file), str(root)], cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, check=True)
    if completed.stdout.strip() != hashlib.sha256(first.bytecode).hexdigest():
        raise AssertionError("process-restored result differs")
