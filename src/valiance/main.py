"""Command-line entry points and persistent REPL session orchestration."""

from __future__ import annotations

import argparse
import copy
import os
import sys
from difflib import get_close_matches
from importlib.metadata import PackageNotFoundError, version
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import valiance.vtypes as T
from valiance.analysis import Analyser, AnalysisBranch, BranchSet, InputMode
from valiance.asts import pretty_ast, typed_source
from valiance.analysis.diagnostics import from_exception, from_message, render, should_color
from valiance.modules_system.packages import (
    PackageError,
    PackageProgress,
    PROJECT_TEMPLATES,
    DEFAULT_PROJECT_TEMPLATE,
    add_dependency,
    init_project,
    install,
    normalize_project_template,
    project_entry_path,
    remove_dependency,
    require_manifest,
    find_project_root,
    load_manifest,
    upgrade_dependency,
)
from valiance.parsing import LexError, ParseError, ParseErrors, Parser, lex, parse
from valiance.repl import ReplCompletion, create_repl_frontend
from valiance.elements.reference_docs import (
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
    build_module,
    dumps_module,
)
from valiance.runtime.runtime_values import DIAGNOSTIC_LIST_PREVIEW_LIMIT, format_runtime_value
from valiance.source_tools import (
    DEFAULT_REFERENCE_FILENAME,
    add_missing_docstrings,
    extract_documented_defines,
    format_source,
    project_source_files,
    render_html_reference,
)
from valiance.testing.runner import TestCommandError, run_test_command

DEFAULT_BYTECODE_FILENAME = "out.vbc"
DEFAULT_BYTECODE_SUFFIX = ".vbc"
DEFAULT_PROJECT_BYTECODE_DIR = "bin"
_ANSI_RESET = "\033[0m"
_ANSI_BOLD = "\033[1m"
_ANSI_DIM = "\033[2m"
_ANSI_CYAN = "\033[36m"
_ANSI_GREEN = "\033[32m"
_ANSI_YELLOW = "\033[33m"
_ANSI_RED = "\033[31m"

_SOURCE_ACTIONS = {"compile", "run", "parse", "analyse", "analyze", "compile-module", "build"}
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

DEFAULT_PROG = "valiance"
DOCS_URL = "https://github.com/lyxal/Valiance-Lang#readme"
ISSUES_URL = "https://github.com/lyxal/Valiance-Lang/issues"


def _version_text() -> str:
    """Return the installed package version without making startup depend on metadata."""
    try:
        return version("valiance")
    except PackageNotFoundError:
        return "0.1.0"



def _help_text(prog: str) -> str:
    """Render the top-level help text for the given program name."""
    return f"""Valiance compiler, runtime, project tools, and interactive REPL.

USAGE
  {prog}                         Start the interactive REPL
  {prog} <command> [options]
  {prog} <file> [-o <file>]      Compile a source file (legacy shorthand)

EXAMPLES
  {prog} run --code '1 2 +'
  {prog} compile --file src/main.vlnc --output bin/main.vbc
  {prog} test --filter arithmetic

COMMON COMMANDS
  run       Run a project entry, source file, or inline code
  compile   Compile a project entry, source file, or inline code
  compile-module  Compile a reusable .vbcm module
  build     Build one or all [build.<name>] manifest targets
  exec      Execute existing bytecode without recompiling
  test      Discover and run project tests
  parse     Print the parsed AST
  analyse   Print the typed AST
  tidy      Add types/docs or format source
  docs      Generate project or language reference documentation
  init      Create a Valiance project
  add       Add an exact-version dependency
  install   Install project dependencies
  lsp       Start the stdio language server

GLOBAL OPTIONS
  -h, --help       Show help (also works after a command)
  --version        Show the installed Valiance version

Run `{prog} <command> --help` for command-specific help.
Documentation: {DOCS_URL}
Report issues:  {ISSUES_URL}
"""


def _command_help_text(prog: str, action: str) -> str:
    """Render focused help for one command."""
    usage = {
        "compile": "[<entry> | --file <file> | --code <code>] [-o <file>] [--no-optimize]",
        "compile-module": "--file <file> [-o <file>] [--no-optimize]",
        "build": "[<target>] [--no-optimize]",
        "run": "[<entry> | --file <file> | --code <code>] [--implicit-output] [--preview-lists] [--no-optimize]",
        "exec": "[<entry> | --file <file>] [--implicit-output] [--preview-lists]",
        "parse": "<file> | --code <code>",
        "analyse": "<file> | --code <code>",
        "tidy": "[<file> | --file <file> | --code <code>] [--types] [--docstrings] [--format] [--stdout]",
        "annotate": "<file> | --code <code>",
        "docs": "[<file>] [-o <file>] [--title <title>] | --language [--format html|markdown|json]",
        "test": "[<selector-or-path> ...] [--filter <text>] [--list [--flat]] [--fail-fast] [--show-output]",
        "init": "[directory] [--template <name>] [--tests | --no-tests] [--list-templates]",
        "install": "[--locked]",
        "add": "<package-or-source> <version> [as <name>]",
        "remove": "<name>",
        "upgrade": "<name> <version>",
        "lsp": "",
    }.get(action, "")
    examples = {
        "compile": f"  {prog} compile --file src/main.vlnc --output bin/main.vbc",
        "compile-module": f"  {prog} compile-module --file src/library.vlnc --output bin/library.vbcm",
        "build": f"  {prog} build library",
        "run": f"  {prog} run --code '1 2 +'",
        "exec": f"  {prog} exec --file bin/main.vbc",
        "test": f"  {prog} test --filter arithmetic",
        "tidy": f"  {prog} tidy src/main.vlnc --types --docstrings --format",
        "docs": f"  {prog} docs --language --format markdown --output -",
    }.get(action)
    text = f"USAGE\n  {prog} {action} {usage}".rstrip()
    package_details = {
        "init": (
            "Create a Valiance project in a new directory or the current directory.\n\n"
            "OPTIONS\n"
            "  --template <name>  Use application, package, multi-module, or empty\n"
            "  --tests            Include tests that exercise generated project code\n"
            "  --no-tests         Do not create a test scaffold\n"
            "  --list-templates   Show available templates and exit\n\n"
            "EXAMPLES\n"
            f"  {prog} init my_app --template application --tests\n"
            f"  {prog} init . --template package --tests\n"
            f"  {prog} init scratch --template empty --no-tests\n\n"
            "Interactive use provides inline choices; use ↑/↓ and Enter.\n"
            "Non-interactive use defaults to application with tests."
        ),
        "add": (
            "Add an exact-version Git dependency and install its complete dependency graph.\n\n"
            "ARGUMENTS\n"
            "  <package-or-source>  Git URL or local Git repository path\n"
            "  <version>            Exact numeric version; resolves v<version> or <version>\n"
            "  as <name>            Optional local import alias\n\n"
            "EXAMPLES\n"
            f"  {prog} add https://github.com/owner/math.git 1.2.0\n"
            f"  {prog} add ../local-math 1.2.0 as math\n\n"
            "The command updates valiance.toml, recursively fetches dependencies, verifies\n"
            "SHA-256 integrity, and writes exact commit revisions to valiance.lock."
        ),
        "install": (
            "Resolve and install all dependencies from valiance.toml.\n\n"
            "OPTIONS\n"
            "  --locked  Reproduce valiance.lock exactly; fail if it is stale\n\n"
            "EXAMPLES\n"
            f"  {prog} install\n"
            f"  {prog} install --locked    # recommended for CI"
        ),
        "remove": (
            "Remove a direct dependency and its managed package directory.\n\n"
            f"EXAMPLE\n  {prog} remove math"
        ),
        "upgrade": (
            "Change one dependency to another exact version and reinstall the graph.\n\n"
            f"EXAMPLE\n  {prog} upgrade math 1.3.0"
        ),
    }.get(action)
    if package_details:
        text += "\n\n" + package_details
    elif examples:
        text += f"\n\nEXAMPLE\n{examples}"
    return text + f"\n\nUse `{prog} --help` for all commands.\nDocumentation: {DOCS_URL}\n"


def _run(vln_mode: bool, argv: Sequence[str] | None = None) -> int:
    """Parse command-line arguments and dispatch the requested Valiance action."""
    prog = "vln" if vln_mode else DEFAULT_PROG
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return _run_repl()

    if "--version" in args:
        print(f"{prog} {_version_text()}")
        return 0

    if args[0] == "help":
        if len(args) == 1:
            print(_help_text(prog))
            return 0
        action = "analyse" if args[1] == "analyze" else args[1]
        if action not in _ACTIONS:
            print(f"error: unknown command '{args[1]}'", file=sys.stderr)
            print(f"Try `{prog} --help` for available commands.", file=sys.stderr)
            return 2
        print(_command_help_text(prog, action))
        return 0

    if "-h" in args or "--help" in args:
        action = "analyse" if args[0] == "analyze" else args[0]
        print(_command_help_text(prog, action) if action in _ACTIONS else _help_text(prog))
        return 0

    if args[0] not in _ACTIONS and args[0].isidentifier() and not Path(args[0]).exists():
        matches = get_close_matches(args[0], sorted(_ACTIONS), n=1, cutoff=0.6)
        if matches:
            print(f"error: unknown command '{args[0]}'. Did you mean '{matches[0]}'?", file=sys.stderr)
            print(f"Try `{prog} --help` for available commands.", file=sys.stderr)
            return 2

    parsed = _parse_args(args, prog=prog)
    if parsed is None:
        print(f"Try `{prog} --help` for usage.", file=sys.stderr)
        return 2

    if parsed.action == "lsp":
        from valiance.lsp import run_language_server
        return run_language_server()
    if parsed.action == "build":
        return _run_build_command(parsed)
    if parsed.action == "compile-module":
        return _run_compile_module_command(parsed)
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


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the `valiance` console script."""
    return _run(vln_mode=False, argv=argv)


def main_vln(argv: Sequence[str] | None = None) -> int:
    """Entry point for the `vln` console script alias of `valiance`."""
    return _run(vln_mode=True, argv=argv)


def cli_entry() -> int:
    """Dispatch a directly executed or compiled binary by its invoked name."""
    invoked_as = os.path.basename(sys.argv[0]).lower()
    return _run(vln_mode=invoked_as.startswith("vln"))


def _parse_args(args: list[str], *, prog: str = DEFAULT_PROG) -> argparse.Namespace | None:
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
        return _parse_test_args(args, prog=prog)
    if explicit_action in {"tidy", "annotate"}:
        return _parse_tidy_args(explicit_action, args, prog=prog)
    if explicit_action == "docs":
        return _parse_docs_args(args, prog=prog)
    if explicit_action in {"build", "compile-module"}:
        parser = argparse.ArgumentParser(prog=prog, add_help=False)
        parser.add_argument("--file", dest="explicit_source_file")
        parser.add_argument("-o", "--output")
        parser.add_argument("--no-optimize", "--no-optimise", dest="no_optimize", action="store_true")
        parser.add_argument("name", nargs="?")
        try:
            parsed = parser.parse_args(args)
        except SystemExit:
            return None
        parsed.action = explicit_action
        if explicit_action == "compile-module":
            if parsed.explicit_source_file is None:
                print("error: compile-module requires --file", file=sys.stderr)
                return None
            if parsed.name is not None:
                print("error: compile-module does not accept a target name", file=sys.stderr)
                return None
        elif parsed.explicit_source_file is not None or parsed.output is not None:
            print("error: build uses settings from valiance.toml", file=sys.stderr)
            return None
        parsed.target_name = parsed.name
        return parsed

    parser = argparse.ArgumentParser(
        prog=prog,
        add_help=False,
    )
    parser.add_argument("-c", "--code")
    parser.add_argument("--file", dest="explicit_source_file")
    parser.add_argument("-o", "--output")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--emit-bytecode", dest="legacy_output")
    parser.add_argument("--implicit-output", action="store_true")
    parser.add_argument("--preview-lists", action="store_true")
    parser.add_argument("--locked", action="store_true")
    parser.add_argument("--template")
    parser.add_argument("--list-templates", action="store_true")
    parser.add_argument("--tests", dest="init_tests", action="store_true")
    parser.add_argument("--no-tests", dest="init_tests", action="store_false")
    parser.set_defaults(init_tests=None)
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

    if parsed.locked:
        print("error: --locked is only valid with install", file=sys.stderr)
        return None
    if (
        parsed.template is not None
        or parsed.list_templates
        or parsed.init_tests is not None
    ):
        print("error: init template and test options are only valid with init", file=sys.stderr)
        return None
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
    *,
    prog: str = DEFAULT_PROG,
) -> argparse.Namespace | None:
    """Parse tidy args for CLI and REPL orchestration."""
    parser = argparse.ArgumentParser(prog=f"{prog} {action}", add_help=False)
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
                f"error: use `{prog} tidy` to combine annotations with other rewrites",
                file=sys.stderr,
            )
            return None
        if parsed.project_mode:
            print(
                "error: legacy annotate requires a file or --code; use "
                f"`{prog} tidy` for a whole project",
                file=sys.stderr,
            )
            return None
        parsed.tidy_types = True
        parsed.tidy_stdout = True
    elif not (
        parsed.tidy_types or parsed.tidy_docstrings or parsed.tidy_format
    ):
        parsed.tidy_types = True

    if parsed.code is not None:
        parsed.tidy_stdout = True
    if parsed.project_mode and parsed.tidy_stdout:
        print("error: --stdout requires one file or --code", file=sys.stderr)
        return None
    return parsed


def _parse_docs_args(
    args: list[str],
    *,
    prog: str = DEFAULT_PROG,
) -> argparse.Namespace | None:
    """Parse docs args for CLI and REPL orchestration."""
    parser = argparse.ArgumentParser(prog=f"{prog} docs", add_help=False)
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


def _parse_test_args(
    args: list[str],
    *,
    prog: str = DEFAULT_PROG,
) -> argparse.Namespace | None:
    """Parse test args for CLI and REPL orchestration."""
    parser = argparse.ArgumentParser(prog=f"{prog} test", add_help=False)
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
        if (
            parsed.template is not None
            or parsed.list_templates
            or parsed.init_tests is not None
        ):
            print("error: init template and test options are only valid with init", file=sys.stderr)
            return None
        return parsed
    if parsed.locked:
        print("error: --locked is only valid with install", file=sys.stderr)
        return None
    if parsed.action == "init":
        if len(args) > 1:
            print("error: init takes at most one directory", file=sys.stderr)
            return None
        if parsed.list_templates and (
            parsed.template is not None or parsed.init_tests is not None
        ):
            print(
                "error: --list-templates cannot be combined with template or test options",
                file=sys.stderr,
            )
            return None
        parsed.package_args = args
        return parsed
    if (
        parsed.template is not None
        or parsed.list_templates
        or parsed.init_tests is not None
    ):
        print("error: init template and test options are only valid with init", file=sys.stderr)
        return None
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


def _print_project_templates() -> None:
    """Print the built-in init templates in a compact, discoverable list."""
    print("Available project templates:")
    for template in PROJECT_TEMPLATES:
        default = " (default)" if template.name == DEFAULT_PROJECT_TEMPLATE else ""
        print(f"  {template.name:<14} {template.description}{default}")


def _choose_project_options() -> tuple[str, bool]:
    """Choose init options with an arrow-key UI, falling back to text input."""
    interactive = bool(
        getattr(sys.stdin, "isatty", lambda: False)()
        and getattr(sys.stdout, "isatty", lambda: False)()
    )
    if not interactive:
        return DEFAULT_PROJECT_TEMPLATE, True
    try:
        selected = _choose_project_options_tui()
    except (ImportError, OSError, RuntimeError, ValueError):
        selected = None
    if selected is not None:
        return selected
    return _choose_project_options_plain()


def _open_completion_menu() -> None:
    """Open the current prompt completion menu before the user presses a key."""
    from prompt_toolkit.application.current import get_app

    get_app().current_buffer.start_completion(select_first=True)


def _choose_project_options_tui() -> tuple[str, bool] | None:
    """Prompt inline with arrow-selectable completions and one-press Enter."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style

    bindings = KeyBindings()

    @bindings.add("enter")
    def _accept_and_submit(event) -> None:
        """Apply the highlighted completion and submit it with the same Enter."""
        buffer = event.current_buffer
        state = buffer.complete_state
        if state is not None and state.current_completion is not None:
            buffer.apply_completion(state.current_completion)
        buffer.validate_and_handle()

    style = Style.from_dict(
        {
            # Keep the menu readable on both dark and light terminal themes by
            # giving every completion surface an explicit high-contrast colour.
            "prompt": "bold #5fd7ff",
            "completion-menu": "bg:#20242b #f4f4f4",
            "completion-menu.completion": "bg:#20242b #f4f4f4",
            "completion-menu.completion.current": "bg:#5fd7ff #101820 bold",
            "completion-menu.meta.completion": "bg:#20242b #c8d0d9",
            "completion-menu.meta.completion.current": "bg:#5fd7ff #101820",
            "scrollbar.background": "bg:#343a44",
            "scrollbar.button": "bg:#7a8796",
        }
    )
    session = PromptSession(key_bindings=bindings, style=style)
    print("Create a Valiance project")
    print("Use Up/Down to choose, then press Enter. Ctrl+C cancels.\n")
    template_completer = WordCompleter(
        [item.name for item in PROJECT_TEMPLATES],
        meta_dict={item.name: item.description for item in PROJECT_TEMPLATES},
        ignore_case=True,
        sentence=True,
    )
    while True:
        template = session.prompt(
            [("class:prompt", "Template: ")],
            default="",
            completer=template_completer,
            complete_while_typing=True,
            complete_in_thread=False,
            pre_run=_open_completion_menu,
        ).strip().lower()
        try:
            template = normalize_project_template(template)
            break
        except PackageError as exc:
            print(f"Invalid template: {exc}", file=sys.stderr)
    if template == "empty":
        return template, False

    tests_completer = WordCompleter(
        ["yes", "no"],
        meta_dict={
            "yes": "include tests that exercise project code",
            "no": "create source without tests",
        },
        ignore_case=True,
        sentence=True,
    )
    while True:
        tests = session.prompt(
            [("class:prompt", "Include tests: ")],
            default="",
            completer=tests_completer,
            complete_while_typing=True,
            complete_in_thread=False,
            pre_run=_open_completion_menu,
        ).strip().lower()
        if tests in {"yes", "y"}:
            return template, True
        if tests in {"no", "n"}:
            return template, False
        print("Please choose yes or no.", file=sys.stderr)


def _choose_project_options_plain() -> tuple[str, bool]:
    """Provide a portable numbered chooser when prompt-toolkit is unavailable."""
    print("Choose a project template:")
    for index, template in enumerate(PROJECT_TEMPLATES, start=1):
        default = " (default)" if template.name == DEFAULT_PROJECT_TEMPLATE else ""
        print(f"  {index}. {template.name:<14} {template.description}{default}")
    while True:
        try:
            answer = input("Template [1]: ").strip()
        except EOFError:
            return DEFAULT_PROJECT_TEMPLATE, True
        if not answer:
            template_name = DEFAULT_PROJECT_TEMPLATE
            break
        if answer.isdigit() and 1 <= int(answer) <= len(PROJECT_TEMPLATES):
            template_name = PROJECT_TEMPLATES[int(answer) - 1].name
            break
        names = {template.name for template in PROJECT_TEMPLATES}
        if answer.lower() in names:
            template_name = answer.lower()
            break
        print("Please enter a template number or name.", file=sys.stderr)
    if template_name == "empty":
        return template_name, False
    try:
        tests_answer = input("Include tests? [Y/n]: ").strip().lower()
    except EOFError:
        return template_name, True
    return template_name, tests_answer not in {"n", "no"}


def _shell_quote(value: str) -> str:
    """Quote a path for the user's shell only when whitespace requires it."""
    if not value or any(character.isspace() for character in value):
        return '"' + value.replace('"', '\\"') + '"'
    return value


class _PackageProgressUI:
    """Render package progress clearly in both terminals and CI logs."""

    _LABELS = {
        "resolve": "Resolving",
        "fetch": "Fetching",
        "verify": "Verifying",
        "install": "Installing",
        "cached": "Verified",
        "lock": "Lockfile",
        "complete": "Complete",
    }

    def __init__(self) -> None:
        """Detect terminal capabilities and initialize renderer state."""
        self.color = should_color(sys.stdout)
        self.interactive = bool(getattr(sys.stdout, "isatty", lambda: False)())
        self._active = False

    def __call__(self, update: PackageProgress) -> None:
        """Render one progress event as an in-place bar or stable log line."""
        label = self._LABELS.get(update.action, update.action.title())
        package = f" {update.package}" if update.package else ""
        detail = f" - {update.detail}" if update.detail else ""
        bar = ""
        if update.step is not None and update.total:
            width = 20
            filled = min(width, round(width * update.step / update.total))
            bar = f" [{'#' * filled}{'-' * (width - filled)}]"
        line = f"{label:<10}{bar}{package}{detail}"
        if self.interactive:
            print("\r\033[2K" + line, end="", flush=True)
            self._active = True
            if update.action in {"install", "cached", "lock", "complete"}:
                print()
                self._active = False
        else:
            print(line)

    def finish(self) -> None:
        """Terminate any active in-place progress line before other output."""
        if self._active:
            print()
            self._active = False


def _print_package_error(exc: PackageError) -> None:
    """Render a package failure with an optional actionable next step."""
    color = should_color(sys.stderr)
    prefix = _repl_style("Package error", _ANSI_BOLD + _ANSI_RED, color)
    print(f"{prefix}: {exc}", file=sys.stderr)
    if exc.hint:
        hint = _repl_style("help", _ANSI_BOLD + _ANSI_CYAN, color)
        print(f"  {hint}: {exc.hint}", file=sys.stderr)


def _run_package_command(parsed: argparse.Namespace) -> int:
    """Run package command for CLI and REPL orchestration."""
    progress = _PackageProgressUI()
    try:
        if parsed.action == "install":
            mode = "locked" if parsed.locked else "resolve"
            print(f"Installing dependencies ({mode} mode)")
            manifest, lock_path = install(locked=parsed.locked, progress=progress)
            print(
                f"Installed {len(manifest.dependencies)} dependencies; "
                f"updated {lock_path}"
            )
            return 0
        if parsed.action == "init":
            if parsed.list_templates:
                _print_project_templates()
                return 0
            args = getattr(parsed, "package_args", [])
            requested_path = Path(args[0]) if args else None
            if parsed.template is None and parsed.init_tests is None:
                template, include_tests = _choose_project_options()
            else:
                template = parsed.template or DEFAULT_PROJECT_TEMPLATE
                include_tests = (
                    parsed.init_tests if parsed.init_tests is not None else True
                )
            if template == "empty":
                include_tests = False
            root = init_project(
                requested_path, template=template, tests=include_tests
            )
            print(f"Initialized Valiance project: {root}")
            print(f"Template: {template}")
            print(f"Tests: {'included' if include_tests else 'not included'}")
            if root != Path.cwd().resolve():
                target = args[0] if args else str(root)
                print(f"Next: cd {_shell_quote(target)}")
            else:
                if include_tests:
                    print("Next: vln test")
                elif template in {"application", "multi-module"}:
                    print("Next: vln run")
            return 0
        args = parsed.package_args
        if parsed.action == "add":
            manifest = add_dependency(
                args[0],
                args[1],
                alias=args[3] if len(args) == 4 else None,
                progress=progress,
            )
            print(f"Added dependency; updated {manifest.path}")
            return 0
        if parsed.action == "remove":
            manifest = remove_dependency(args[0])
            print(f"Removed dependency; updated {manifest.path}")
            return 0
        if parsed.action == "upgrade":
            manifest = upgrade_dependency(args[0], args[1], progress=progress)
            print(f"Upgraded dependency; updated {manifest.path}")
            return 0
    except PackageError as exc:
        progress.finish()
        _print_package_error(exc)
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
        if source == ":__mode_switched__":
            continue
        if source == ":__save_scratchpad__":
            try:
                saved = frontend.save_scratchpad()
            except OSError as exc:
                print(f"Could not save scratchpad: {exc}", file=sys.stderr)
            else:
                print(f"Saved scratchpad: {saved}" if saved else "Save cancelled.")
            continue
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
        if source in {":repl", ":scratch"}:
            mode = source.removeprefix(":")
            if frontend.set_mode(mode):
                print(f"Switched to {mode} mode.")
            else:
                print("Scratch mode requires an enhanced interactive terminal.")
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
            "One-line REPL ready. Press Ctrl-R to open the shared scratch editor."
        )
    print("State persists between lines. Entries may span multiple lines.")
    print("Type :help, :reset, or :quit.")


def _print_repl_help(*, color: bool, fancy: bool = False) -> None:
    """Print REPL help for CLI and REPL orchestration."""
    print(_repl_style("REPL commands", _ANSI_BOLD, color))
    print("  :help   show this message")
    print("  :reset  clear stack, variables, definitions, and imports")
    print("  :type   show stack types without executing source: :type <source>")
    print("  :clear    clear the terminal")
    print("  :repl     switch to one-line REPL mode")
    print("  :scratch  switch to the persistent scratch editor")
    print("  :quit   exit the REPL")
    if fancy:
        print()
        print("Enhanced editing")
        print("  Ctrl-R            switch modes; changed scratch source runs before REPL")
        print("  Enter             newline in scratch; run in REPL mode")
        print("  Ctrl-Enter / F5   run and retain the complete scratch program")
        print("  Ctrl-Backspace    clear the current input buffer")
        print("  Ctrl-S            save the scratchpad to a .vlnc file")
        print("  Ctrl-T / F2       toggle live type hints")
        print("  Tab / Ctrl-Space  show completions")
        print("  Right arrow       accept an inline history suggestion")
    print()
    print("Both modes share one stack, variables, definitions, and imports.")
    print("The scratch program remains available when you switch away and back.")
    print("Leaving a changed scratchpad runs it first, publishing its definitions.")


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
            diagnostic = from_message("Type error", analyser.diagnostics[0])
            rendered = f"{diagnostic.stage}: {diagnostic.message}"
            if diagnostic.help is not None:
                rendered += f"\nhelp: {diagnostic.help}"
            return rendered
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
) -> str:
    """Compute tidy source for CLI and REPL orchestration."""
    program = Parser(lex(source)).parse_program()
    rendered = source
    if add_types:
        analyser = Analyser(source_file=source_file)
        typed = analyser.analyse(program)
        _print_analyser_messages(analyser, source, source_file)
        rendered = _safe_typed_source(typed, source)
    if add_docstrings:
        rendered = add_missing_docstrings(rendered)
    if apply_format:
        add_trailing_commas = True
        root = find_project_root(source_file or Path.cwd())
        if root is not None:
            add_trailing_commas = "trailing-commas" in load_manifest(root).formatting.add
        rendered = format_source(
            rendered,
            indent_width=2,
            add_trailing_commas=add_trailing_commas,
        )
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


def _safe_typed_source(typed, source: str) -> str:
    """Compute safe typed source for CLI and REPL orchestration."""
    rendered = typed_source(typed, source)
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


def _compile_module_artifact(
    source_file: Path,
    output: Path,
    *,
    module_name: str,
    optimize: bool,
) -> None:
    """Analyse and compile one reusable Valiance bytecode module."""
    source = source_file.read_text(encoding="utf-8")
    program = parse(source)
    analyser = Analyser(source_file=source_file)
    typed = analyser.analyse(program)
    if analyser.diagnostics:
        raise CompileError("; ".join(analyser.diagnostics))
    bytecode = compile_program(typed, optimize=optimize)
    artifact = build_module(module_name, source, bytecode)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(dumps_module(artifact))


def _run_compile_module_command(parsed: argparse.Namespace) -> int:
    """Run a one-off compiled-module build."""
    source = Path(parsed.explicit_source_file).resolve()
    output = (
        Path(parsed.output).resolve()
        if parsed.output is not None
        else source.with_suffix(".vbcm")
    )
    if output.suffix != ".vbcm":
        print("Build error: compile-module output must use '.vbcm'", file=sys.stderr)
        return 1
    try:
        _compile_module_artifact(
            source,
            output,
            module_name=source.stem,
            optimize=not parsed.no_optimize,
        )
    except (OSError, LexError, ParseError, CompileError, BytecodeFormatError) as exc:
        _print_exception_diagnostic(exc, source_file=source)
        return 1
    print(f"Wrote bytecode module: {output}")
    return 0


def _run_build_command(parsed: argparse.Namespace) -> int:
    """Build one or all named targets from the project manifest."""
    try:
        manifest = require_manifest()
        if parsed.target_name is None:
            targets = tuple(manifest.builds.values())
            if not targets:
                raise PackageError("project has no [build.<name>] targets")
        else:
            target = manifest.builds.get(parsed.target_name)
            if target is None:
                available = ", ".join(sorted(manifest.builds)) or "(none)"
                raise PackageError(
                    f"project has no build target {parsed.target_name!r}; "
                    f"available targets: {available}"
                )
            targets = (target,)
        for target in targets:
            if target.entry is not None:
                source_file = project_entry_path(manifest, target.entry)
            else:
                source_file = (manifest.root / str(target.source)).resolve()
                try:
                    source_file.relative_to(manifest.root.resolve())
                except ValueError as exc:
                    raise PackageError(
                        f"build target {target.name!r} source must stay within the project root"
                    ) from exc
                if not source_file.is_file():
                    raise PackageError(
                        f"build target {target.name!r} source does not exist: {source_file}"
                    )
            suffix = ".vbcm" if target.kind == "module" else ".vbc"
            output = manifest.root / (target.output or f"bin/{target.name}{suffix}")
            optimize = target.optimize and not parsed.no_optimize
            if target.kind == "module":
                _compile_module_artifact(
                    source_file,
                    output,
                    module_name=target.name,
                    optimize=optimize,
                )
                print(f"Built {target.name}: {output}")
            else:
                source = source_file.read_text(encoding="utf-8")
                result = _run_source(
                    source,
                    action="compile",
                    bytecode_output=str(output),
                    source_file=source_file,
                    optimize=optimize,
                )
                if result:
                    return result
        return 0
    except (PackageError, OSError, LexError, ParseError, CompileError, BytecodeFormatError) as exc:
        _print_exception_diagnostic(exc)
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
    if isinstance(exc, ParseErrors):
        for error in exc.errors:
            _print_exception_diagnostic(error, source=source, source_file=source_file)
        return
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
    raise SystemExit(cli_entry())
