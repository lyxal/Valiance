"""Command-line entry points and persistent REPL session orchestration."""

from __future__ import annotations

import argparse
import copy
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import valiance.types as T
from valiance.analysis import Analyser, AnalysisBranch, BranchSet, InputMode
from valiance.asts import pretty_ast, typed_source
from valiance.diagnostics import from_exception, from_message, render, should_color
from valiance.packages import (
    PackageError,
    add_dependency,
    init_project,
    install,
    project_entry_path,
    remove_dependency,
    require_manifest,
    upgrade_dependency,
)
from valiance.parsing import LexError, ParseError, Parser, lex
from valiance.repl import ReplCompletion, create_repl_frontend
from valiance.reference_docs import (
    DocumentationError,
    collect_language_references,
    render_language_reference,
)
from valiance.runtime import (
    BytecodeFormatError,
    CompileError,
    RuntimeError,
    VirtualMachine,
    compile_program,
    dumps,
    loads,
    run,
)
from valiance.runtime_values import DIAGNOSTIC_LIST_PREVIEW_LIMIT, format_runtime_value
from valiance.source_tools import (
    DEFAULT_REFERENCE_FILENAME,
    add_missing_docstrings,
    extract_documented_defines,
    format_source,
    project_source_files,
    render_html_reference,
)
from valiance.testing import TestCommandError, run_test_command

DEFAULT_BYTECODE_FILENAME = "out.vbc"
DEFAULT_BYTECODE_SUFFIX = ".vbc"
DEFAULT_PROJECT_BYTECODE_DIR = "bin"
_ANSI_RESET = "\033[0m"
_ANSI_BOLD = "\033[1m"
_ANSI_DIM = "\033[2m"
_ANSI_CYAN = "\033[36m"
_ANSI_GREEN = "\033[32m"

_SOURCE_ACTIONS = {"compile", "run", "parse", "analyse", "analyze"}
_SOURCE_TOOL_ACTIONS = {"tidy", "annotate", "docs"}
_BYTECODE_ACTIONS = {"exec"}
_PACKAGE_ACTIONS = {"init", "install", "add", "remove", "upgrade"}
_TEST_ACTIONS = {"test"}
_LSP_ACTIONS = {"lsp"}
_ACTIONS = (
    _SOURCE_ACTIONS
    | _SOURCE_TOOL_ACTIONS
    | _BYTECODE_ACTIONS
    | _PACKAGE_ACTIONS
    | _TEST_ACTIONS
    | _LSP_ACTIONS
)

HELP = """usage: valiance
       valiance <file> [-o <file>]
       valiance compile [<entry>] [-o <file>] [--no-optimize]
       valiance compile --file <file> [-o <file>]
       valiance compile -c <code> [-o <file>]
       valiance run [<entry>] [--no-optimize]
       valiance run --file <file>
       valiance run -c <code>
       valiance exec [<entry>]
       valiance exec --file <file>
       valiance test [<selector-or-path> ...] [options]
       valiance lsp
       valiance parse <file>
       valiance analyse <file>
       valiance tidy [<file>] [--types] [--docstrings] [--format]
       valiance docs [<file>] [-o <file>]
       valiance docs --language [--format html|markdown|json] [-o <file>]
       valiance install
       valiance init [directory]
       valiance add <package-or-source> <version> [as <name>]
       valiance remove <name>
       valiance upgrade <name> <version>

actions:
  <no action>        start the REPL
  compile             compile the current project's main or named entry
  run                 run the current project's main or named entry
  exec                execute existing project bytecode without compiling
  test                discover and run tests under the project's tests directory
  lsp                 start the Language Server Protocol server over stdio
  parse               print the parsed AST
  analyse             print the typed AST
  tidy                rewrite one file or every project source file
  docs                generate project or language reference documentation
  annotate            legacy alias for `tidy --types --stdout`
  install             install project dependencies and update valiance.lock
  init                create a new project manifest and starter source tree
  add                 add an exact-version dependency
  remove              remove a direct dependency
  upgrade             change a dependency to an exact version

options:
  -c, --code <code>   use inline Valiance code
  --file <file>        use an explicit source or bytecode file
  -o, --output <file> write compiled bytecode to this file
  --no-optimize       disable bytecode optimisation for this compilation
  --implicit-output   print the final stack if execution prints nothing
                      (default for run --code)
  --preview-lists     preview lazy lists instead of forcing full output
  --filter <text>      run tests whose names or descriptions contain text
  --list               list selected tests without running them
  --flat               print copyable dotted names with --list
  --fail-fast          stop after the first failed or errored test
  --show-output        show captured output for passing tests too
  --types              add available inferred function signatures with tidy
  --docstrings         add missing #?? documentation stubs with tidy
  --format             normalize indentation to two spaces with tidy
  --stdout             print tidy output instead of rewriting a file
  --title <title>      set the generated HTML reference title
  --language           document built-ins and standard-library functions
  --format <format>    output html, markdown, or json with docs --language

repl commands:
  :help               show REPL help
  :reset              clear stack, variables, definitions, and imports
  :type <source>      show stack types without executing source
  :clear              clear the terminal
  :quit               exit the REPL
"""


def main(argv: Sequence[str] | None = None) -> int:
    """Parse command-line arguments and dispatch the requested Valiance action."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return _run_repl()

    parsed = _parse_args(args)
    if parsed is None:
        print(HELP)
        return 2

    if parsed.action == "lsp":
        from valiance.lsp import run_language_server

        return run_language_server()
    if parsed.action == "exec":
        bytecode_file = parsed.bytecode_file
        if bytecode_file is None:
            try:
                manifest = require_manifest()
                bytecode_file = _project_bytecode_path(
                    manifest.root,
                    parsed.project_entry,
                )
            except PackageError as exc:
                print(f"Package error: {exc}", file=sys.stderr)
                return 1
        return _run_bytecode_file(
            bytecode_file,
            implicit_output=parsed.implicit_output,
            preview_lists=parsed.preview_lists,
        )
    if parsed.action in {"tidy", "annotate"}:
        return _run_tidy_command(parsed)
    if parsed.action == "docs":
        return _run_docs_command(parsed)
    if parsed.action in _PACKAGE_ACTIONS:
        return _run_package_command(parsed)
    if parsed.action == "test":
        try:
            manifest = require_manifest()
            return run_test_command(
                manifest.root,
                parsed.test_arguments,
                filter_text=parsed.test_filter,
                list_only=parsed.test_list,
                flat=parsed.test_flat,
                fail_fast=parsed.test_fail_fast,
                show_output=parsed.test_show_output,
            )
        except (PackageError, TestCommandError) as exc:
            print(f"Test error: {exc}", file=sys.stderr)
            return 1

    source = parsed.code
    source_file: Path | None = None
    project_root: Path | None = None
    project_entry: str | None = None
    if source is None:
        if parsed.project_entry is not None:
            try:
                manifest = require_manifest()
                source_file = project_entry_path(manifest, parsed.project_entry)
                project_root = manifest.root
                project_entry = parsed.project_entry
            except PackageError as exc:
                print(f"Package error: {exc}", file=sys.stderr)
                return 1
        else:
            source_file = Path(parsed.source_file)
        source = _read_source_file(str(source_file))
        if source is None:
            return 1

    return _run_source(
        source,
        action=parsed.action,
        bytecode_output=parsed.output,
        source_file=source_file,
        project_root=project_root,
        project_entry=project_entry,
        implicit_output=parsed.implicit_output,
        preview_lists=parsed.preview_lists,
        optimize=not parsed.no_optimize,
    )


def _parse_args(args: list[str]) -> argparse.Namespace | None:
    """Parse args for CLI and REPL orchestration."""
    action = "compile"
    explicit_action: str | None = None
    if args and args[0] in _ACTIONS:
        explicit_action = args[0]
        action = "analyse" if args[0] == "analyze" else args[0]
        args = args[1:]

    if explicit_action == "lsp":
        if args:
            print("error: lsp does not accept arguments", file=sys.stderr)
            return None
        return argparse.Namespace(action="lsp")
    if explicit_action == "test":
        return _parse_test_args(args)
    if explicit_action in {"tidy", "annotate"}:
        return _parse_tidy_args(explicit_action, args)
    if explicit_action == "docs":
        return _parse_docs_args(args)

    parser = argparse.ArgumentParser(
        prog="valiance",
        add_help=False,
    )
    parser.add_argument("-c", "--code")
    parser.add_argument("--file", dest="explicit_source_file")
    parser.add_argument("-o", "--output")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--emit-bytecode", dest="legacy_output")
    parser.add_argument("--implicit-output", action="store_true")
    parser.add_argument("--preview-lists", action="store_true")
    parser.add_argument(
        "--no-optimize",
        "--no-optimise",
        dest="no_optimize",
        action="store_true",
    )
    parser.add_argument("file", nargs="?")
    parser.add_argument("extra", nargs="*")
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

    if parsed.legacy_output is not None:
        if parsed.output is not None:
            print(
                "error: pass either --output or --emit-bytecode, not both",
                file=sys.stderr,
            )
            return None
        parsed.output = parsed.legacy_output

    parsed.action = action
    parsed.bytecode_file = None

    if parsed.action == "exec":
        if (
            parsed.code is not None
            or parsed.output is not None
            or parsed.run
            or parsed.no_optimize
        ):
            print(
                "error: exec cannot be combined with source input, --run, "
                "or bytecode output",
                file=sys.stderr,
            )
            return None
        if parsed.extra:
            print("error: exec takes at most one entry name", file=sys.stderr)
            return None
        if parsed.file is not None and parsed.explicit_source_file is not None:
            print("error: pass either an entry or --file, not both", file=sys.stderr)
            return None

        parsed.project_entry = parsed.file or "main"
        parsed.bytecode_file = parsed.explicit_source_file
        return parsed

    if parsed.action in _PACKAGE_ACTIONS:
        if (
            parsed.code is not None
            or parsed.explicit_source_file is not None
            or parsed.output is not None
            or parsed.run
            or parsed.implicit_output
            or parsed.preview_lists
            or parsed.no_optimize
        ):
            print(
                "error: package commands cannot be combined with source options",
                file=sys.stderr,
            )
            return None
        return _validate_package_args(parsed)

    if parsed.output is not None and parsed.action != "compile":
        print("error: bytecode output is only valid for compile", file=sys.stderr)
        return None
    if parsed.implicit_output and parsed.action != "run":
        print("error: --implicit-output is only valid for run actions", file=sys.stderr)
        return None
    if parsed.preview_lists and parsed.action not in {"run", "exec"}:
        print("error: --preview-lists is only valid for run actions", file=sys.stderr)
        return None
    if parsed.no_optimize and parsed.action not in {"compile", "run"}:
        print(
            "error: --no-optimize is only valid for compile or run actions",
            file=sys.stderr,
        )
        return None
    if parsed.extra:
        print("error: too many positional arguments", file=sys.stderr)
        return None

    project_mode = explicit_action in {"compile", "run"} and not parsed.run

    if project_mode:
        selected_inputs = sum(
            value is not None
            for value in (parsed.code, parsed.explicit_source_file, parsed.file)
        )
        if selected_inputs > 1:
            print(
                "error: pass an entry, --file, or --code; not more than one",
                file=sys.stderr,
            )
            return None

        parsed.project_entry = None
        parsed.source_file = None
        if parsed.code is None:
            if parsed.explicit_source_file is not None:
                parsed.source_file = parsed.explicit_source_file
            else:
                parsed.project_entry = parsed.file or "main"
    else:
        if parsed.explicit_source_file is not None:
            print(
                "error: --file is only valid with explicit compile or run actions",
                file=sys.stderr,
            )
            return None
        if parsed.code is not None and parsed.file is not None:
            print("error: pass either a file or --code, not both", file=sys.stderr)
            return None
        if parsed.code is None and parsed.file is None:
            return None

        parsed.project_entry = None
        parsed.source_file = parsed.file

    if parsed.action == "run" and parsed.code is not None:
        parsed.implicit_output = True
    return parsed


def _parse_tidy_args(
    action: str,
    args: list[str],
) -> argparse.Namespace | None:
    """Parse tidy args for CLI and REPL orchestration."""
    parser = argparse.ArgumentParser(prog=f"valiance {action}", add_help=False)
    parser.add_argument("-c", "--code")
    parser.add_argument("--file", dest="explicit_source_file")
    parser.add_argument("--types", dest="tidy_types", action="store_true")
    parser.add_argument("--docstrings", dest="tidy_docstrings", action="store_true")
    parser.add_argument("--format", dest="tidy_format", action="store_true")
    parser.add_argument("--stdout", dest="tidy_stdout", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("file", nargs="?")
    try:
        parsed = parser.parse_args(args)
    except SystemExit:
        return None
    if parsed.help:
        return None

    selected_inputs = sum(
        value is not None
        for value in (parsed.code, parsed.explicit_source_file, parsed.file)
    )
    if selected_inputs > 1:
        print(
            "error: pass a file, --file, or --code; not more than one",
            file=sys.stderr,
        )
        return None

    parsed.action = action
    parsed.source_file = parsed.explicit_source_file or parsed.file
    parsed.project_mode = parsed.code is None and parsed.source_file is None

    if action == "annotate":
        if parsed.tidy_docstrings or parsed.tidy_format:
            print(
                "error: use `vln tidy` to combine annotations with other rewrites",
                file=sys.stderr,
            )
            return None
        if parsed.project_mode:
            print(
                "error: legacy annotate requires a file or --code; use `vln tidy` "
                "for a whole project",
                file=sys.stderr,
            )
            return None
        parsed.tidy_types = True
        parsed.tidy_stdout = True
    elif not (
        parsed.tidy_types or parsed.tidy_docstrings or parsed.tidy_format
    ):
        parsed.tidy_types = True
        parsed.tidy_format = True

    if parsed.code is not None:
        parsed.tidy_stdout = True
    if parsed.project_mode and parsed.tidy_stdout:
        print("error: --stdout requires one file or --code", file=sys.stderr)
        return None
    return parsed


def _parse_docs_args(args: list[str]) -> argparse.Namespace | None:
    """Parse docs args for CLI and REPL orchestration."""
    parser = argparse.ArgumentParser(prog="valiance docs", add_help=False)
    parser.add_argument("-c", "--code")
    parser.add_argument("--file", dest="explicit_source_file")
    parser.add_argument("-o", "--output")
    parser.add_argument("--title")
    parser.add_argument("--language", dest="language_reference", action="store_true")
    parser.add_argument(
        "--format",
        dest="docs_format",
        choices=("html", "markdown", "json"),
        default="html",
    )
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("file", nargs="?")
    try:
        parsed = parser.parse_args(args)
    except SystemExit:
        return None
    if parsed.help:
        return None

    selected_inputs = sum(
        value is not None
        for value in (parsed.code, parsed.explicit_source_file, parsed.file)
    )
    if selected_inputs > 1:
        print(
            "error: pass a file, --file, or --code; not more than one",
            file=sys.stderr,
        )
        return None
    if parsed.language_reference and selected_inputs:
        print(
            "error: --language cannot be combined with a source file or --code",
            file=sys.stderr,
        )
        return None
    if not parsed.language_reference and parsed.docs_format != "html":
        print(
            "error: --format is currently supported only with --language",
            file=sys.stderr,
        )
        return None
    parsed.action = "docs"
    parsed.source_file = parsed.explicit_source_file or parsed.file
    parsed.project_mode = (
        not parsed.language_reference
        and parsed.code is None
        and parsed.source_file is None
    )
    return parsed


def _parse_test_args(args: list[str]) -> argparse.Namespace | None:
    """Parse test args for CLI and REPL orchestration."""
    parser = argparse.ArgumentParser(prog="valiance test", add_help=False)
    parser.add_argument("--filter", dest="test_filter")
    parser.add_argument("--list", dest="test_list", action="store_true")
    parser.add_argument("--flat", dest="test_flat", action="store_true")
    parser.add_argument("--fail-fast", dest="test_fail_fast", action="store_true")
    parser.add_argument("--show-output", dest="test_show_output", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("test_arguments", nargs="*")
    try:
        parsed = parser.parse_args(args)
    except SystemExit:
        return None
    if parsed.help:
        return None
    if parsed.test_flat and not parsed.test_list:
        print("error: --flat is only valid with --list", file=sys.stderr)
        return None
    parsed.action = "test"
    return parsed


def _validate_package_args(parsed: argparse.Namespace) -> argparse.Namespace | None:
    """Validate package args for CLI and REPL orchestration."""
    args = [item for item in (parsed.file, *parsed.extra) if item is not None]
    if parsed.action == "install":
        if args:
            print("error: install takes no arguments", file=sys.stderr)
            return None
        return parsed
    if parsed.action == "init":
        if len(args) > 1:
            print("error: init takes at most one directory", file=sys.stderr)
            return None
        parsed.package_args = args
        return parsed
    if parsed.action == "remove":
        if len(args) != 1:
            print("error: remove requires a dependency name", file=sys.stderr)
            return None
        parsed.package_args = args
        return parsed
    if parsed.action == "upgrade":
        if len(args) != 2:
            print(
                "error: upgrade requires a dependency name and version",
                file=sys.stderr,
            )
            return None
        parsed.package_args = args
        return parsed
    if parsed.action == "add":
        if len(args) not in {2, 4} or (len(args) == 4 and args[2] != "as"):
            print(
                "error: add requires <package-or-source> <version> [as <name>]",
                file=sys.stderr,
            )
            return None
        parsed.package_args = args
        return parsed
    return None


def _run_package_command(parsed: argparse.Namespace) -> int:
    """Run package command for CLI and REPL orchestration."""
    try:
        if parsed.action == "install":
            manifest, lock_path = install()
            print(
                f"Installed {len(manifest.dependencies)} dependencies; "
                f"updated {lock_path}"
            )
            return 0
        if parsed.action == "init":
            args = getattr(parsed, "package_args", [])
            root = init_project(Path(args[0]) if args else None)
            print(f"Initialized Valiance project: {root}")
            return 0
        args = parsed.package_args
        if parsed.action == "add":
            manifest = add_dependency(
                args[0],
                args[1],
                alias=args[3] if len(args) == 4 else None,
            )
            print(f"Added dependency; updated {manifest.path}")
            return 0
        if parsed.action == "remove":
            manifest = remove_dependency(args[0])
            print(f"Removed dependency; updated {manifest.path}")
            return 0
        if parsed.action == "upgrade":
            manifest = upgrade_dependency(args[0], args[1])
            print(f"Upgraded dependency; updated {manifest.path}")
            return 0
    except PackageError as exc:
        print(f"Package error: {exc}", file=sys.stderr)
        return 1
    print(f"error: unknown package action {parsed.action!r}", file=sys.stderr)
    return 1


def _run_repl() -> int:
    """Run REPL for CLI and REPL orchestration."""
    session = _ReplSession()
    color = should_color(sys.stdout)
    frontend = create_repl_frontend(
        prompt=lambda line_number: _repl_prompt(line_number, color=color),
        completion_provider=session.completion_items,
        type_hint_provider=session.type_hint,
    )
    line_number = 1
    _print_repl_banner(color=color, fancy=frontend.fancy)
    while True:
        try:
            source = frontend.read(line_number)
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            return 130
        source = source.strip()
        if not source:
            continue
        if source in {":quit", ":q", "quit", "exit"}:
            return 0
        if source in {":reset", ":r", "reset"}:
            session.reset()
            print(
                "Reset REPL state. Stack, variables, definitions, and imports cleared."
            )
            line_number = 1
            continue
        if source in {":help", ":h", "help"}:
            _print_repl_help(color=color, fancy=frontend.fancy)
            continue
        if source in {":clear", ":c", "clear"}:
            print("\033[2J\033[H", end="")
            continue
        if source == ":type" or source.startswith(":type "):
            expression = source.removeprefix(":type").strip()
            if not expression:
                print("usage: :type <Valiance source>")
            else:
                print(session.type_hint(expression) or "No type information available.")
            continue
        session.run(source)
        line_number += 1


def _print_repl_banner(*, color: bool, fancy: bool = False) -> None:
    """Print REPL banner for CLI and REPL orchestration."""
    print(_repl_style("Valiance REPL", _ANSI_BOLD + _ANSI_CYAN, color))
    print(_repl_style("-------------", _ANSI_DIM, color))
    if fancy:
        print(
            "Enhanced editing enabled: highlighting, completion, and live type hints."
        )
    print("State persists between lines. Type :help, :reset, or :quit.")


def _print_repl_help(*, color: bool, fancy: bool = False) -> None:
    """Print REPL help for CLI and REPL orchestration."""
    print(_repl_style("REPL commands", _ANSI_BOLD, color))
    print("  :help   show this message")
    print("  :reset  clear stack, variables, definitions, and imports")
    print("  :type   show stack types without executing source: :type <source>")
    print("  :clear  clear the terminal")
    print("  :quit   exit the REPL")
    if fancy:
        print()
        print("Enhanced editing")
        print("  Tab / Ctrl-Space  show completions")
        print("  F2                toggle live type hints")
        print("  Right arrow        accept an inline history suggestion")
    print()
    print("Enter one Valiance expression or statement per line.")


def _repl_prompt(line_number: int, *, color: bool) -> str:
    """Compute REPL prompt for CLI and REPL orchestration."""
    prompt = f"vln:{line_number}> "
    return _repl_style(prompt, _ANSI_GREEN, color)


def _repl_style(text: str, code: str, enabled: bool) -> str:
    """Compute REPL style for CLI and REPL orchestration."""
    if not enabled:
        return text
    return f"{code}{text}{_ANSI_RESET}"


@dataclass
class _ReplSession:
    analyser: Analyser | None = None
    branch: AnalysisBranch | None = None
    output: _OutputTracker | None = None
    vm: VirtualMachine | None = None
    runtime_stack: list[Any] | None = None
    _state_version: int = 0
    _hint_cache: tuple[int, str, str | None] | None = None

    def __post_init__(self) -> None:
        """Validate invariants after constructing this REPL session."""
        self.reset()

    def reset(self) -> None:
        """Reset persistent analyser, globals, stack, and output state."""
        self.analyser = Analyser()
        self.branch = AnalysisBranch(input_mode=InputMode.TOP_LEVEL)
        self.output = _OutputTracker()
        self.vm = VirtualMachine(output=self.output)
        self.runtime_stack = []
        self._state_version += 1
        self._hint_cache = None

    def completion_items(self) -> tuple[ReplCompletion, ...]:
        """Return completion metadata derived from the current REPL session."""
        if self.analyser is None or self.branch is None:
            return ()
        items: dict[str, ReplCompletion] = {}
        env = self.analyser.env
        depth = 0
        while env is not None:
            scope = "element" if depth == 0 else "built-in element"
            for name in env.overloads:
                text = name.text
                items.setdefault(text, ReplCompletion(text, scope))
            for collection, meta in (
                (env.objects, "object"),
                (env.traits, "trait"),
                (env.variants, "variant"),
                (env.enums, "enum"),
            ):
                for name in collection:
                    text = name.text
                    items.setdefault(text, ReplCompletion(text, meta))
            for name in env.data_tags:
                text = f"#{name.text}"
                items.setdefault(text, ReplCompletion(text, "data tag"))
            env = env.parent
            depth += 1
        for name, typ in self.branch.variables.visible_items():
            text = f"${name.text}"
            items[text] = ReplCompletion(text, f"variable: {T.show(typ)}")
        return tuple(items.values())

    def type_hint(self, source: str) -> str | None:
        """Preview the resulting type stack without mutating REPL state."""
        source = source.strip()
        if not source:
            return None
        cached = self._hint_cache
        if cached is not None and cached[:2] == (self._state_version, source):
            return cached[2]
        result = self._type_hint_uncached(source)
        self._hint_cache = (self._state_version, source, result)
        return result

    def _type_hint_uncached(self, source: str) -> str | None:
        """Compute type hint uncached for CLI and REPL orchestration."""
        if self.analyser is None or self.branch is None:
            return None
        try:
            program = Parser(lex(source)).parse_program()
        except LexError as exc:
            return f"Lex error: {exc}"
        except ParseError as exc:
            return f"Parse error: {exc}"
        analyser = copy.deepcopy(self.analyser)
        analyser.diagnostics.clear()
        analyser.warnings.clear()
        analyser.clear_lints()
        initial = replace(copy.deepcopy(self.branch), typed_body=())
        try:
            final = analyser.analyse_block(BranchSet((initial,)), tuple(program))
        except (OSError, RuntimeError) as exc:
            return f"Type error: {exc}"
        if analyser.diagnostics:
            return f"Type error: {analyser.diagnostics[0]}"
        if len(final) != 1:
            return "Type error: source has no single valid stack effect"
        next_branch = next(iter(final))
        if next_branch.errors:
            return f"Type error: {next_branch.errors[0].message}"
        return (
            f"Types: {_format_type_stack(self.branch.stack)} -> "
            f"{_format_type_stack(next_branch.stack)}"
        )

    def run(self, source: str) -> bool:
        """Compile and execute one source entry in the persistent REPL session."""
        if (
            self.analyser is None
            or self.branch is None
            or self.output is None
            or self.vm is None
            or self.runtime_stack is None
        ):
            self.reset()
        assert self.analyser is not None
        assert self.branch is not None
        assert self.output is not None
        assert self.vm is not None
        assert self.runtime_stack is not None
        self.analyser.diagnostics.clear()
        self.analyser.warnings.clear()
        self.analyser.clear_lints()
        self.output.did_print = False
        try:
            program = Parser(lex(source)).parse_program()
            final = self.analyser.analyse_block(
                BranchSet((replace(self.branch, typed_body=()),)),
                tuple(program),
            )
            if len(final) != 1:
                for node in program:
                    _print_diagnostic(
                        from_message("Type error", f"could not analyse {node!r}"),
                        source,
                    )
                return False
            next_branch = next(iter(final))
            for lint in self.analyser.lints:
                _print_diagnostic(from_message("Lint warning", lint), source)
            for warning in self.analyser.warnings:
                _print_diagnostic(from_message("Type warning", warning), source)
            if self.analyser.diagnostics:
                for diagnostic in self.analyser.diagnostics:
                    _print_diagnostic(from_message("Type error", diagnostic), source)
                return False
            bytecode = compile_program(list(next_branch.typed_body))
            stack = self.vm.execute(
                bytecode.main,
                {},
                self.vm.globals,
                initial_stack=list(self.runtime_stack),
            )
            self.runtime_stack = stack
            self.branch = replace(next_branch, typed_body=())
            self._state_version += 1
            self._hint_cache = None
            if not self.output.did_print:
                print(_format_stack(stack))
            return True
        except (
            BytecodeFormatError,
            LexError,
            OSError,
            ParseError,
            CompileError,
            RuntimeError,
        ) as exc:
            _print_exception_diagnostic(exc, source=source)
            return False


def _format_type_stack(stack: T.TypeStack) -> str:
    """Format type stack for CLI and REPL orchestration."""
    return "[" + ", ".join(T.show(item) for item in stack) + "]"


def _read_source_file(filename: str) -> str | None:
    """Read source file for CLI and REPL orchestration."""
    try:
        return Path(filename).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: could not read {filename!r}: {exc}", file=sys.stderr)
        return None


def _run_tidy_command(parsed: argparse.Namespace) -> int:
    """Run tidy command for CLI and REPL orchestration."""
    project_root: Path | None = None
    if parsed.project_mode:
        try:
            manifest = require_manifest()
        except PackageError as exc:
            print(f"Package error: {exc}", file=sys.stderr)
            return 1
        project_root = manifest.root
        source_files = project_source_files(project_root)
        if not source_files:
            print("error: project contains no .vlnc source files", file=sys.stderr)
            return 1
    elif parsed.source_file is not None:
        source_files = (Path(parsed.source_file),)
    else:
        source_files = ()

    if parsed.code is not None:
        try:
            rendered = _tidy_source(
                parsed.code,
                source_file=None,
                add_types=parsed.tidy_types,
                add_docstrings=parsed.tidy_docstrings,
                apply_format=parsed.tidy_format,
                add_inferred_overloads=parsed.action == "tidy",
            )
        except (LexError, ParseError, OSError) as exc:
            _print_exception_diagnostic(exc, source=parsed.code)
            return 1
        print(rendered, end="" if rendered.endswith("\n") else "\n")
        return 0

    changed = 0
    failed = 0
    for source_file in source_files:
        source = _read_source_file(str(source_file))
        if source is None:
            failed += 1
            continue
        try:
            rendered = _tidy_source(
                source,
                source_file=source_file,
                add_types=parsed.tidy_types,
                add_docstrings=parsed.tidy_docstrings,
                apply_format=parsed.tidy_format,
                add_inferred_overloads=parsed.action == "tidy",
            )
        except (LexError, ParseError, OSError) as exc:
            _print_exception_diagnostic(exc, source=source, source_file=source_file)
            failed += 1
            continue

        if parsed.tidy_stdout:
            print(rendered, end="" if rendered.endswith("\n") else "\n")
            continue
        if rendered == source:
            continue
        try:
            source_file.write_text(rendered, encoding="utf-8")
        except OSError as exc:
            print(f"error: could not write {source_file}: {exc}", file=sys.stderr)
            failed += 1
            continue
        changed += 1

    if not parsed.tidy_stdout:
        if parsed.project_mode:
            relative_label = (
                f" in {project_root}" if project_root is not None else ""
            )
            print(
                f"Tidied {len(source_files) - failed} file(s){relative_label}; "
                f"{changed} changed."
            )
        elif source_files:
            status = "Updated" if changed else "Unchanged"
            print(f"{status}: {source_files[0]}")
    return 1 if failed else 0


def _tidy_source(
    source: str,
    *,
    source_file: Path | None,
    add_types: bool,
    add_docstrings: bool,
    apply_format: bool,
    add_inferred_overloads: bool,
) -> str:
    """Compute tidy source for CLI and REPL orchestration."""
    program = Parser(lex(source)).parse_program()
    rendered = source
    if add_types:
        analyser = Analyser(source_file=source_file)
        typed = analyser.analyse(program)
        _print_analyser_messages(analyser, source, source_file)
        rendered = _safe_typed_source(
            typed, source, add_inferred_overloads=add_inferred_overloads
        )
    if add_docstrings:
        rendered = add_missing_docstrings(rendered)
    if apply_format:
        rendered = format_source(rendered, indent_width=2)
    return rendered


def _run_docs_command(parsed: argparse.Namespace) -> int:
    """Run docs command for CLI and REPL orchestration."""
    if parsed.language_reference:
        return _run_language_docs_command(parsed)

    project_root: Path | None = None
    project_title: str | None = None
    if parsed.project_mode:
        try:
            manifest = require_manifest()
        except PackageError as exc:
            print(f"Package error: {exc}", file=sys.stderr)
            return 1
        project_root = manifest.root
        project_title = str(manifest.project.get("name") or project_root.name)
        source_files = project_source_files(project_root)
        if not source_files:
            print("error: project contains no .vlnc source files", file=sys.stderr)
            return 1
    elif parsed.source_file is not None:
        source_files = (Path(parsed.source_file),)
    else:
        source_files = ()

    references = []
    failed = 0
    if parsed.code is not None:
        source_items = ((None, "<code>", parsed.code),)
    else:
        source_items_list = []
        for source_file in source_files:
            source = _read_source_file(str(source_file))
            if source is None:
                failed += 1
                continue
            if project_root is None:
                label = source_file.name
            else:
                label = (
                    source_file.resolve()
                    .relative_to(project_root.resolve())
                    .as_posix()
                )
            source_items_list.append((source_file, label, source))
        source_items = tuple(source_items_list)

    for source_file, label, source in source_items:
        try:
            program = Parser(lex(source)).parse_program()
            analyser = Analyser(source_file=source_file)
            typed = analyser.analyse(program)
            _print_analyser_messages(analyser, source, source_file)
            annotated = _safe_typed_source(typed, source)
            references.extend(
                extract_documented_defines(annotated, source_path=label)
            )
        except (LexError, ParseError, OSError) as exc:
            _print_exception_diagnostic(exc, source=source, source_file=source_file)
            failed += 1

    if parsed.title is not None:
        title = parsed.title
    elif project_title is not None:
        title = f"{project_title} Reference"
    elif source_files:
        title = f"{source_files[0].stem} Reference"
    else:
        title = "Valiance Reference"
    rendered = render_html_reference(references, title=title)

    output_path: Path | None
    if parsed.output is not None:
        output_path = Path(parsed.output)
    elif parsed.code is not None:
        output_path = None
    elif project_root is not None:
        output_path = project_root / "docs" / DEFAULT_REFERENCE_FILENAME
    else:
        output_path = source_files[0].with_suffix(".html")

    if output_path is None:
        print(rendered, end="")
    else:
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
        except OSError as exc:
            print(f"error: could not write {output_path}: {exc}", file=sys.stderr)
            return 1
        print(f"Wrote documentation: {output_path}")
    return 1 if failed else 0


def _run_language_docs_command(parsed: argparse.Namespace) -> int:
    """Generate built-in and standard-library reference documentation."""
    title = parsed.title or "Valiance Built-ins and Standard Library Reference"
    try:
        references = collect_language_references()
        rendered = render_language_reference(
            references,
            output_format=parsed.docs_format,
            title=title,
        )
    except (DocumentationError, ValueError) as exc:
        print(f"documentation error: {exc}", file=sys.stderr)
        return 1

    suffixes = {"html": ".html", "markdown": ".md", "json": ".json"}
    if parsed.output == "-":
        print(rendered, end="")
        return 0
    output_path = (
        Path(parsed.output)
        if parsed.output is not None
        else Path.cwd() / "docs" / f"language-reference{suffixes[parsed.docs_format]}"
    )
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        print(f"error: could not write {output_path}: {exc}", file=sys.stderr)
        return 1
    print(
        f"Wrote {len(references)} built-in and standard-library entries: "
        f"{output_path}"
    )
    return 0


def _safe_typed_source(
    typed, source: str, *, add_inferred_overloads: bool = True
) -> str:
    """Compute safe typed source for CLI and REPL orchestration."""
    rendered = typed_source(
        typed, source, add_inferred_overloads=add_inferred_overloads
    )
    try:
        Parser(lex(rendered)).parse_program()
    except (LexError, ParseError):
        return source
    return rendered


def _run_source(
    source: str,
    *,
    action: str = "compile",
    bytecode_output: str | None = None,
    source_file: Path | None = None,
    project_root: Path | None = None,
    project_entry: str | None = None,
    implicit_output: bool = False,
    preview_lists: bool = False,
    optimize: bool = True,
) -> int:
    """Run source for CLI and REPL orchestration."""
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
            _print_analyser_messages(analyser, source, source_file)
            print("Typed AST:")
            print(pretty_ast(typed))
            return 0

        if action == "annotate":
            _print_analyser_messages(analyser, source, source_file)
            print(typed_source(typed, source))
            return 0

        for lint in analyser.lints:
            _print_diagnostic(
                from_message("Lint warning", lint),
                source,
                source_file,
            )
        for warning in analyser.warnings:
            _print_diagnostic(
                from_message("Type warning", warning),
                source,
                source_file,
            )
        if analyser.diagnostics:
            for diagnostic in analyser.diagnostics:
                _print_diagnostic(
                    from_message("Type error", diagnostic),
                    source,
                    source_file,
                )
            return 1

        bytecode = compile_program(typed, optimize=optimize)
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
        output_path = _resolve_bytecode_output_path(
            bytecode_output,
            source_file,
            project_root=project_root,
            project_entry=project_entry,
        )
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


def _project_bytecode_path(project_root: Path, entry: str) -> Path:
    """Resolve the path for project bytecode for CLI and REPL orchestration."""
    path = (
        project_root
        / DEFAULT_PROJECT_BYTECODE_DIR
        / f"{entry}{DEFAULT_BYTECODE_SUFFIX}"
    )
    if not path.is_file():
        raise PackageError(
            f"compiled entry {entry!r} does not exist: {path}; "
            f"run `vln compile {entry}` first"
        )
    return path


def _run_bytecode_file(
    filename: str,
    *,
    implicit_output: bool = False,
    preview_lists: bool = False,
) -> int:
    """Run bytecode file for CLI and REPL orchestration."""
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
    *,
    project_root: Path | None = None,
    project_entry: str | None = None,
) -> Path:
    """Resolve bytecode output path for CLI and REPL orchestration."""
    if filename is None:
        if project_root is not None and project_entry is not None:
            return (
                project_root
                / DEFAULT_PROJECT_BYTECODE_DIR
                / f"{project_entry}{DEFAULT_BYTECODE_SUFFIX}"
            )
        if source_file is not None:
            return source_file.with_suffix(DEFAULT_BYTECODE_SUFFIX)
        return Path(DEFAULT_BYTECODE_FILENAME)
    output_path = Path(filename)
    if source_file is not None and not output_path.is_absolute():
        return source_file.parent / output_path
    return output_path


def _write_bytecode_file(filename: str | Path, data: bytes) -> None:
    """Write bytecode file for CLI and REPL orchestration."""
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _print_exception_diagnostic(
    exc: BaseException,
    *,
    source: str | None = None,
    source_file: Path | None = None,
) -> None:
    """Print exception diagnostic for CLI and REPL orchestration."""
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
    """Print diagnostic for CLI and REPL orchestration."""
    print(
        render(
            diagnostic,
            source,
            source_file=source_file,
            color=should_color(sys.stderr),
        ),
        file=sys.stderr,
    )


def _print_analyser_messages(
    analyser: Analyser,
    source: str,
    source_file: Path | None,
) -> None:
    """Print analyser messages for CLI and REPL orchestration."""
    for lint in analyser.lints:
        _print_diagnostic(
            from_message("Lint warning", lint),
            source,
            source_file,
        )
    for warning in analyser.warnings:
        _print_diagnostic(
            from_message("Type warning", warning),
            source,
            source_file,
        )
    for diagnostic in analyser.diagnostics:
        _print_diagnostic(
            from_message("Type error", diagnostic),
            source,
            source_file,
        )


def _run_bytecode(
    bytecode,
    *,
    implicit_output: bool = False,
    preview_lists: bool = False,
) -> None:
    """Run bytecode for CLI and REPL orchestration."""
    output = _OutputTracker()
    preview_limit = DIAGNOSTIC_LIST_PREVIEW_LIMIT if preview_lists else None
    stack = run(bytecode, output=output, list_preview_limit=preview_limit)
    if implicit_output and not output.did_print:
        print(_format_stack(stack, preview_limit=preview_limit))


class _OutputTracker:
    def __init__(self) -> None:
        """Initialize this output tracker."""
        self.did_print = False

    def __call__(self, value: str) -> None:
        """Invoke this output tracker with the supplied arguments."""
        self.did_print = True
        print(value, end="")


def _format_stack(stack: list[Any], *, preview_limit: int | None = None) -> str:
    """Format stack for CLI and REPL orchestration."""
    if not stack:
        return "Stack []"
    lines = ["Stack ["]
    for index, value in enumerate(stack):
        lines.append(f"  {index}: {_format_value(value, preview_limit=preview_limit)}")
    lines.append("]")
    return "\n".join(lines)


def _format_value(value: Any, *, preview_limit: int | None = None) -> str:
    """Format value for CLI and REPL orchestration."""
    return format_runtime_value(
        value,
        quote_strings=True,
        tuple_single_comma=True,
        lazy_preview_limit=preview_limit,
    )


if __name__ == "__main__":
    raise SystemExit(main())
