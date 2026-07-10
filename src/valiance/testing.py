"""Static discovery and execution for Valiance tests."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from valiance.analysis import Analyser
from valiance.analysis.annotations import annotation_nodes
from valiance.asts import ASTNode, DefineNode, ElementNode, StringLiteralNode
from valiance.parsing import LexError, ParseError, Parser, lex
from valiance.runtime import (
    AssertionFailure,
    CompileError,
    RuntimeError,
    VirtualMachine,
    compile_program,
)
from valiance.runtime_values import ObjectValue, format_runtime_value


class TestCommandError(Exception):
    """Raised for invalid test discovery or selection."""


@dataclass(frozen=True)
class TestGroup:
    source_file: Path
    canonical_id: str
    local_id: str
    label: str
    parts: tuple[str, ...]

    @property
    def aliases(self) -> frozenset[str]:
        """Return selector aliases accepted for this test group."""
        return frozenset((self.canonical_id, self.local_id))


@dataclass(frozen=True)
class TestCase:
    source_file: Path
    source: str
    base_nodes: tuple[ASTNode, ...]
    definition: DefineNode
    canonical_id: str
    local_id: str
    label: str
    group_labels: tuple[str, ...]
    group_parts: tuple[str, ...]

    @property
    def aliases(self) -> frozenset[str]:
        """Return selector aliases accepted for this test case."""
        return frozenset((self.canonical_id, self.local_id))

    @property
    def searchable_text(self) -> str:
        """Return normalized text used by test-name filtering."""
        return " ".join(
            (
                self.canonical_id,
                self.local_id,
                *self.group_labels,
                self.label,
            )
        ).casefold()


@dataclass(frozen=True)
class TestResult:
    case: TestCase
    outcome: str
    detail: str = ""
    output: str = ""


@dataclass(frozen=True)
class _DiscoveredFile:
    tests: tuple[TestCase, ...]
    groups: tuple[TestGroup, ...]


@dataclass
class _Trie:
    children: dict[str, _Trie]
    label: str | None

    def __init__(self) -> None:
        """Initialize this trie."""
        self.children = {}
        self.label = None


def run_test_command(
    project_root: Path,
    arguments: Iterable[str],
    *,
    filter_text: str | None = None,
    list_only: bool = False,
    flat: bool = False,
    fail_fast: bool = False,
    show_output: bool = False,
) -> int:
    """Discover, select, and run project tests."""
    tests_root = project_root / "tests"
    path_arguments, selectors = _split_arguments(project_root, arguments)
    source_files = _selected_source_files(project_root, tests_root, path_arguments)
    discovered = tuple(_discover_file(path, tests_root) for path in source_files)
    cases = tuple(case for item in discovered for case in item.tests)
    groups = tuple(group for item in discovered for group in item.groups)
    if not cases:
        raise TestCommandError("no tests were discovered")

    selected = _select_cases(cases, groups, selectors)
    if filter_text is not None:
        needle = filter_text.casefold()
        selected = tuple(case for case in selected if needle in case.searchable_text)
        if not selected:
            raise TestCommandError(f"no tests match filter {filter_text!r}")

    if list_only:
        if flat:
            for case in selected:
                print(case.canonical_id)
        else:
            _print_test_tree(selected)
        return 0

    results: list[TestResult] = []
    for case in selected:
        result = _run_case(case)
        results.append(result)
        _print_result(result, show_output=show_output)
        if fail_fast and result.outcome != "pass":
            break

    passed = sum(result.outcome == "pass" for result in results)
    failed = sum(result.outcome == "fail" for result in results)
    errors = sum(result.outcome == "error" for result in results)
    print(f"\n{passed} passed, {failed} failed, {errors} errors")
    return 0 if failed == 0 and errors == 0 else 1


def _split_arguments(
    project_root: Path,
    arguments: Iterable[str],
) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    """Split arguments for the Valiance-language test runner."""
    paths: list[Path] = []
    selectors: list[str] = []
    for argument in arguments:
        candidate = Path(argument)
        explicit_path = (
            argument.startswith(("./", "../", "/"))
            or candidate.suffix == ".vlnc"
            or "/" in argument
            or "\\" in argument
        )
        resolved = candidate if candidate.is_absolute() else project_root / candidate
        if explicit_path or resolved.exists():
            paths.append(resolved.resolve())
        else:
            selectors.append(argument)
    return tuple(paths), tuple(selectors)


def _selected_source_files(
    project_root: Path,
    tests_root: Path,
    paths: tuple[Path, ...],
) -> tuple[Path, ...]:
    """Compute selected source files for the Valiance-language test runner."""
    root = project_root.resolve()
    if not paths:
        if not tests_root.is_dir():
            raise TestCommandError(f"test directory does not exist: {tests_root}")
        files = tuple(sorted(tests_root.rglob("*.vlnc")))
        if not files:
            raise TestCommandError(f"no .vlnc test files found under {tests_root}")
        return files

    files: set[Path] = set()
    for path in paths:
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise TestCommandError(
                f"test path must stay within the project: {path}"
            ) from exc
        if path.is_dir():
            files.update(path.rglob("*.vlnc"))
        elif path.is_file() and path.suffix == ".vlnc":
            files.add(path)
        else:
            raise TestCommandError(
                f"test path does not exist or is not a .vlnc file: {path}"
            )
    if not files:
        raise TestCommandError("selected paths contain no .vlnc test files")
    return tuple(sorted(files))


def _discover_file(source_file: Path, tests_root: Path) -> _DiscoveredFile:
    """Discover file for the Valiance-language test runner."""
    try:
        source = source_file.read_text(encoding="utf-8")
        program = Parser(lex(source)).parse_program()
    except OSError as exc:
        raise TestCommandError(f"could not read {source_file}: {exc}") from exc
    except (LexError, ParseError) as exc:
        raise TestCommandError(f"{source_file}: {exc}") from exc

    base_nodes = tuple(
        node
        for node in program
        if not (isinstance(node, DefineNode) and _test_kind(node) is not None)
    )
    module_parts = _module_parts(source_file, tests_root)
    tests: list[TestCase] = []
    groups: list[TestGroup] = []
    sibling_names: set[str] = set()

    for node in program:
        if not isinstance(node, DefineNode):
            continue
        kind = _test_kind(node)
        if kind is None:
            continue
        name = _validate_test_definition(node, kind, source_file)
        if name in sibling_names:
            raise TestCommandError(
                f"{source_file}: duplicate test declaration name {name!r}"
            )
        sibling_names.add(name)
        if kind == "test":
            tests.append(
                _make_test_case(
                    source_file,
                    source,
                    base_nodes,
                    node,
                    module_parts,
                    (),
                    (),
                )
            )
        else:
            _collect_group(
                source_file,
                source,
                base_nodes,
                node,
                module_parts,
                (),
                (),
                tests,
                groups,
            )

    return _DiscoveredFile(tuple(tests), tuple(groups))


def _collect_group(
    source_file: Path,
    source: str,
    base_nodes: tuple[ASTNode, ...],
    definition: DefineNode,
    module_parts: tuple[str, ...],
    parent_parts: tuple[str, ...],
    parent_labels: tuple[str, ...],
    tests: list[TestCase],
    groups: list[TestGroup],
) -> None:
    """Collect group for the Valiance-language test runner."""
    group_name = _validate_test_definition(definition, "testgroup", source_file)
    group_parts = (*parent_parts, group_name)
    group_label = _annotation_label(definition, "testgroup") or group_name
    group_labels = (*parent_labels, group_label)
    canonical_parts = _canonical_parts(module_parts, group_parts)
    groups.append(
        TestGroup(
            source_file,
            ".".join(canonical_parts),
            ".".join(group_parts),
            group_label,
            group_parts,
        )
    )

    seen: set[str] = set()
    for child in definition.function.body:
        if not isinstance(child, DefineNode) or _test_kind(child) is None:
            location = _location_text(child)
            raise TestCommandError(
                f"{source_file}{location}: @testgroup bodies may contain only "
                "@test and @testgroup definitions"
            )
        kind = _test_kind(child)
        assert kind is not None
        child_name = _validate_test_definition(child, kind, source_file)
        if child_name in seen:
            raise TestCommandError(
                f"{source_file}: duplicate declaration {child_name!r} in "
                f"test group {'.'.join(group_parts)!r}"
            )
        seen.add(child_name)
        if kind == "test":
            tests.append(
                _make_test_case(
                    source_file,
                    source,
                    base_nodes,
                    child,
                    module_parts,
                    group_parts,
                    group_labels,
                )
            )
        else:
            _collect_group(
                source_file,
                source,
                base_nodes,
                child,
                module_parts,
                group_parts,
                group_labels,
                tests,
                groups,
            )


def _make_test_case(
    source_file: Path,
    source: str,
    base_nodes: tuple[ASTNode, ...],
    definition: DefineNode,
    module_parts: tuple[str, ...],
    group_parts: tuple[str, ...],
    group_labels: tuple[str, ...],
) -> TestCase:
    """Create test case for the Valiance-language test runner."""
    name = definition.name.text.removeprefix("\\")
    local_parts = (*group_parts, name)
    canonical_parts = _canonical_parts(module_parts, local_parts)
    label = _annotation_label(definition, "test") or name
    return TestCase(
        source_file,
        source,
        base_nodes,
        definition,
        ".".join(canonical_parts),
        ".".join(local_parts),
        label,
        group_labels,
        group_parts,
    )


def _canonical_parts(
    module_parts: tuple[str, ...],
    local_parts: tuple[str, ...],
) -> tuple[str, ...]:
    """Compute canonical parts for the Valiance-language test runner."""
    if module_parts and local_parts and module_parts[-1] == local_parts[0]:
        return (*module_parts, *local_parts[1:])
    return (*module_parts, *local_parts)


def _module_parts(source_file: Path, tests_root: Path) -> tuple[str, ...]:
    """Compute module parts for the Valiance-language test runner."""
    try:
        relative = source_file.relative_to(tests_root)
    except ValueError:
        relative = Path(source_file.name)
    return (*relative.parts[:-1], relative.stem)


def _test_kind(definition: DefineNode) -> str | None:
    """Compute test kind for the Valiance-language test runner."""
    names = {
        annotation.name.text
        for annotation in annotation_nodes(definition.annotations)
        if annotation.name.text in {"test", "testgroup"}
    }
    if len(names) > 1:
        return "invalid"
    return next(iter(names), None)


def _validate_test_definition(
    definition: DefineNode,
    kind: str,
    source_file: Path,
) -> str:
    """Validate test definition for the Valiance-language test runner."""
    location = _location_text(definition)
    if kind == "invalid":
        raise TestCommandError(
            f"{source_file}{location}: a definition cannot be both @test and @testgroup"
        )
    annotations = tuple(
        annotation
        for annotation in annotation_nodes(definition.annotations)
        if annotation.name.text == kind
    )
    if len(annotations) != 1:
        raise TestCommandError(
            f"{source_file}{location}: @{kind} may only appear once"
        )
    annotation = annotations[0]
    if annotation.kwargs or len(annotation.args) > 1 or any(
        not isinstance(argument, StringLiteralNode) for argument in annotation.args
    ):
        raise TestCommandError(
            f"{source_file}{location}: @{kind} accepts at most one string description"
        )
    if (
        not definition.name.text.startswith("\\")
        or definition.function.params is not None
    ):
        raise TestCommandError(
            f"{source_file}{location}: @{kind} requires a niladic definition "
            "whose name starts with '\\'"
        )
    if definition.is_multi or definition.generics:
        raise TestCommandError(
            f"{source_file}{location}: @{kind} definitions cannot be multi or generic"
        )
    return definition.name.text.removeprefix("\\")


def _annotation_label(definition: DefineNode, name: str) -> str | None:
    """Compute annotation label for the Valiance-language test runner."""
    for annotation in annotation_nodes(definition.annotations):
        if annotation.name.text != name or not annotation.args:
            continue
        argument = annotation.args[0]
        if isinstance(argument, StringLiteralNode):
            return argument.value
    return None


def _location_text(node: ASTNode) -> str:
    """Compute location text for the Valiance-language test runner."""
    if node.location is None:
        return ""
    return f":{node.location.line}:{node.location.column}"


def _select_cases(
    cases: tuple[TestCase, ...],
    groups: tuple[TestGroup, ...],
    selectors: tuple[str, ...],
) -> tuple[TestCase, ...]:
    """Select cases for the Valiance-language test runner."""
    ordered = tuple(
        sorted(
            cases,
            key=lambda case: (str(case.source_file), case.canonical_id),
        )
    )
    if not selectors:
        return ordered

    selected: set[tuple[Path, str]] = set()
    for selector in selectors:
        matching_tests = tuple(case for case in cases if selector in case.aliases)
        matching_groups = tuple(group for group in groups if selector in group.aliases)
        entities = len(matching_tests) + len(matching_groups)
        if entities == 0:
            raise TestCommandError(f"unknown test selector {selector!r}")
        if entities > 1:
            matches = sorted(
                [case.canonical_id for case in matching_tests]
                + [group.canonical_id for group in matching_groups]
            )
            raise TestCommandError(
                f"ambiguous test selector {selector!r}: {', '.join(matches)}"
            )
        if matching_tests:
            case = matching_tests[0]
            selected.add((case.source_file, case.canonical_id))
            continue
        group = matching_groups[0]
        for case in cases:
            belongs_to_group = (
                case.source_file == group.source_file
                and case.group_parts[: len(group.parts)] == group.parts
            )
            if belongs_to_group:
                selected.add((case.source_file, case.canonical_id))

    return tuple(
        case
        for case in ordered
        if (case.source_file, case.canonical_id) in selected
    )


def _run_case(case: TestCase) -> TestResult:
    """Run case for the Valiance-language test runner."""
    definition = _strip_test_annotations(case.definition)
    call = ElementNode(definition.name, location=definition.location)
    program = [*case.base_nodes, definition, call]
    output_parts: list[str] = []
    try:
        analyser = Analyser(source_file=case.source_file)
        typed = analyser.analyse(program)
        if analyser.diagnostics:
            return TestResult(
                case,
                "error",
                "\n".join(analyser.diagnostics),
                "".join(output_parts),
            )
        bytecode = compile_program(typed)
        stack = VirtualMachine(output=output_parts.append).run(bytecode)
    except AssertionFailure as exc:
        return TestResult(case, "fail", str(exc), "".join(output_parts))
    except (CompileError, RuntimeError) as exc:
        return TestResult(case, "error", str(exc), "".join(output_parts))

    if not stack:
        return TestResult(case, "pass", output="".join(output_parts))
    if len(stack) == 1 and _is_assert_error(stack[0]):
        return TestResult(
            case,
            "fail",
            _error_detail(stack[0]),
            "".join(output_parts),
        )
    if len(stack) == 1 and _is_err(stack[0]):
        return TestResult(
            case,
            "error",
            _error_detail(stack[0]),
            "".join(output_parts),
        )
    rendered = ", ".join(
        format_runtime_value(value, quote_strings=True) for value in stack
    )
    return TestResult(
        case,
        "error",
        f"test returned ordinary stack value(s): [{rendered}]",
        "".join(output_parts),
    )


def _strip_test_annotations(definition: DefineNode) -> DefineNode:
    """Strip test annotations for the Valiance-language test runner."""
    kept = tuple(
        annotation
        for annotation in definition.annotations
        if not (
            getattr(annotation, "name", None) is not None
            and annotation.name.text in {"test", "testgroup"}
        )
    )
    function_kept = tuple(
        annotation
        for annotation in definition.function.annotations
        if not (
            getattr(annotation, "name", None) is not None
            and annotation.name.text in {"test", "testgroup"}
        )
    )
    return replace(
        definition,
        annotations=kept,
        function=replace(definition.function, annotations=function_kept),
    )


def _is_assert_error(value: Any) -> bool:
    """Return whether the value is assert error."""
    return (
        isinstance(value, ObjectValue)
        and value.type_name.rsplit(".", 1)[-1] == "AssertError"
    )


def _is_err(value: Any) -> bool:
    """Return whether the value is err."""
    if not isinstance(value, ObjectValue):
        return False
    name = value.type_name.rsplit(".", 1)[-1]
    return name == "Err" or name.endswith("Error")


def _error_detail(value: Any) -> str:
    """Compute error detail for the Valiance-language test runner."""
    if not isinstance(value, ObjectValue):
        return format_runtime_value(value, quote_strings=True)
    detail = value.fields.get("detail")
    message = value.fields.get("message", value.fields.get("value"))
    if detail is not None:
        return f"{message}\n{detail}" if message is not None else str(detail)
    if message is not None:
        return str(message)
    return format_runtime_value(value, quote_strings=True)


def _print_result(result: TestResult, *, show_output: bool) -> None:
    """Print result for the Valiance-language test runner."""
    marker = {"pass": "PASS", "fail": "FAIL", "error": "ERROR"}[result.outcome]
    local_name = result.case.local_id.rsplit(".", 1)[-1]
    description = (
        "" if result.case.label == local_name else f" — {result.case.label}"
    )
    print(f"{marker} {result.case.canonical_id}{description}")
    if result.detail:
        for line in result.detail.splitlines():
            print(f"     {line}")
    if result.output and (show_output or result.outcome != "pass"):
        print("     output:")
        for line in result.output.rstrip("\n").splitlines():
            print(f"       {line}")


def _print_test_tree(cases: tuple[TestCase, ...]) -> None:
    """Print test tree for the Valiance-language test runner."""
    trie = _Trie()
    for case in cases:
        canonical_parts = tuple(case.canonical_id.split("."))
        local_size = len(case.group_parts) + 1
        module_size = len(canonical_parts) - local_size
        node = trie
        for index, part in enumerate(canonical_parts):
            node = node.children.setdefault(part, _Trie())
            group_index = index - module_size
            if 0 <= group_index < len(case.group_labels):
                node.label = case.group_labels[group_index]
            elif index == len(canonical_parts) - 1:
                node.label = case.label
    _print_trie(trie, 0)


def _print_trie(node: _Trie, depth: int) -> None:
    """Print trie for the Valiance-language test runner."""
    for name, child in sorted(node.children.items()):
        suffix = f" — {child.label}" if child.label and child.label != name else ""
        print(f"{'  ' * depth}{name}{suffix}")
        _print_trie(child, depth + 1)
