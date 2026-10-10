"""Transactional persistent sessions shared by CLI and terminal applications."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

import valiance.vtypes as T
from valiance.analysis import Analyser, AnalysisBranch, BranchSet, InputMode
from valiance.analysis.diagnostics import Diagnostic, from_exception, from_message
from valiance.elements.reference_docs import collect_language_references
from valiance.incremental.snapshots import WorkspaceSnapshot
from valiance.parsing import LexError, ParseError, ParseErrors, Parser, lex
from valiance.runtime import CompileError, Program, VirtualMachine, compile_program
from valiance.runtime import RuntimeError as VMError
from valiance.runtime.concurrency import render_concurrency_fault
from valiance.runtime.runtime_values import (
    ObjectValue,
    PanicSignal,
    format_runtime_value,
    object_type_name,
)
from valiance.runtime.vm import _check_duplication_allowed, _duplicate_occurrence
from valiance.source_tools import extract_documented_defines

from .events import (
    CompletionItem,
    DiagnosticEvent,
    OutputEvent,
    ResultEvent,
    SessionEvent,
    SessionStarted,
)


@dataclass
class _SavedReplFrame:
    """One suspended parent frame in the REPL branch stack."""

    analyser: Analyser
    branch: AnalysisBranch
    vm: VirtualMachine
    runtime_stack: list[Any]
    state_version: int


class _OutputDispatcher:
    """Route builtin output into structured events without service-level printing."""

    def __init__(self, emit: Callable[[SessionEvent], None]) -> None:
        """Retain one sink across session reset and branch copies."""
        self.emit = emit
        self.did_print = False

    def __call__(self, value: str) -> None:
        """Emit a printed-output event immediately during execution."""
        self.did_print = True
        self.emit(OutputEvent(value))

    def __deepcopy__(self, memo):
        """Keep copied VM contexts bound to this session's output dispatcher."""
        return self


@dataclass(frozen=True)
class PreparedSubmission:
    """Worker-local compile result; never serialize its analyser or typed state."""

    owner: str
    generation: int
    preparation: int
    source: str
    analyser: Analyser
    branch: AnalysisBranch
    program: Program
    fresh: bool
    advisories: tuple[Diagnostic, ...]
    snapshot: WorkspaceSnapshot | None = None


@dataclass(frozen=True)
class PreparationResult:
    """Preparation succeeds only when parse, analysis and code generation succeed."""

    prepared: PreparedSubmission | None
    diagnostics: tuple[Diagnostic, ...]

    @property
    def successful(self) -> bool:
        """Report whether there is a current executable preparation."""
        return self.prepared is not None


@dataclass(frozen=True)
class ExecutionResult:
    """Execution outcome and presentation events with no mutable VM references."""

    successful: bool
    events: tuple[SessionEvent, ...]


class SessionService:
    """Own committed analyser, imports, globals, runtime stack and branch frames."""

    def __init__(
        self,
        *,
        event_sink: Callable[[SessionEvent], None] | None = None,
        reset_on_fault: bool = True,
    ) -> None:
        """Start a fresh session; subscribers receive structured events only."""
        self._owner = uuid4().hex
        self._state_version = 0
        self._preparation = 0
        self._hint_cache = None
        self._frames = []
        self._event_sink = event_sink
        self._events: list[SessionEvent] = []
        self._output_characters = 0
        self._output_count = 0
        self._output_truncated = False
        self.reset_on_fault = reset_on_fault
        self.output = _OutputDispatcher(self._emit)
        self.vm = None
        self.loaded_source: WorkspaceSnapshot | None = None
        self.reset()

    def _emit(self, event: SessionEvent) -> None:
        """Collect current presentation and stream it to an optional frontend."""
        if self._event_sink is not None:
            self._event_sink(event)
        if isinstance(event, OutputEvent):
            if len(event.text) > 64_000:
                event = OutputEvent(event.text[-64_000:])
                self._output_truncated = True
            self._output_characters += len(event.text)
            self._output_count += 1
        self._events.append(event)
        while self._output_characters > 64_000 or self._output_count > 300:
            index = next(
                i
                for i, item in enumerate(self._events)
                if isinstance(item, OutputEvent)
            )
            removed = self._events.pop(index)
            self._output_characters -= len(removed.text)
            self._output_count -= 1
            self._output_truncated = True

    def _begin_events(self) -> None:
        """Start a bounded retained response independently of streamed output."""
        self._events = []
        self._output_characters = 0
        self._output_count = 0
        self._output_truncated = False

    def _outcome(self, successful: bool) -> ExecutionResult:
        """Mark dropped retained output while preserving lifecycle/diagnostic events."""
        events = list(self._events)
        if self._output_truncated:
            index = next(
                (i for i, event in enumerate(events) if isinstance(event, OutputEvent)),
                len(events),
            )
            events.insert(index, OutputEvent("[Earlier program output dropped]\n"))
        return ExecutionResult(successful, tuple(events))

    def close(self) -> None:
        """Release all owned VM executors, including suspended branch frames."""
        machines = [self.vm, *(frame.vm for frame in self._frames)]
        seen = set()
        for vm in machines:
            if vm is not None and id(vm) not in seen:
                vm.close(wait=False)
                seen.add(id(vm))

    def reset(self) -> None:
        """Clear runtime state without touching any document or frontend history."""
        self.close()
        self.analyser = Analyser()
        self.branch = AnalysisBranch(input_mode=InputMode.TOP_LEVEL)
        self.vm = VirtualMachine(output=self.output)
        self.runtime_stack = []
        self.loaded_source = None
        self._state_version += 1
        self._preparation += 1
        self._hint_cache = None
        self._frames = []

    @property
    def branch_depth(self) -> int:
        """Return the number of currently open REPL branches."""
        return len(self._frames or ())

    def _require_parent(self, command: str) -> _SavedReplFrame:
        """Return the immediate parent frame or reject a branch-only command."""
        if not self._frames:
            raise ValueError(f":{command} requires an open REPL branch")
        return self._frames[-1]

    def open_branch(self) -> None:
        """Push an isolated copy of the complete live REPL state."""
        assert self.analyser is not None and self.branch is not None
        assert self.vm is not None and self.runtime_stack is not None
        assert self.output is not None
        parent = _SavedReplFrame(
            self.analyser, self.branch, self.vm, self.runtime_stack, self._state_version
        )
        analyser, branch, vm, stack = copy.deepcopy(
            (self.analyser, self.branch, self.vm, self.runtime_stack)
        )
        vm.output = self.output
        if self._frames is None:
            self._frames = []
        self._frames.append(parent)
        self.analyser, self.branch, self.vm, self.runtime_stack = (
            analyser,
            branch,
            vm,
            stack,
        )
        self._state_version += 1
        self._hint_cache = None

    def restore_branch(self) -> None:
        """Discard the current frame and restore its immediate parent."""
        parent = self._require_parent("restore")
        self.vm.close(wait=False)
        assert self._frames is not None
        self._frames.pop()
        self.analyser, self.branch, self.vm, self.runtime_stack = (
            parent.analyser,
            parent.branch,
            parent.vm,
            parent.runtime_stack,
        )
        self._state_version = max(self._state_version, parent.state_version) + 1
        self._hint_cache = None

    def continue_branch(self) -> None:
        """Adopt the current frame wholesale in place of its parent."""
        self._require_parent("continue").vm.close(wait=False)
        assert self._frames is not None
        self._frames.pop()
        self._state_version += 1
        self._hint_cache = None

    def _checked_transfer(
        self, count: int
    ) -> tuple[_SavedReplFrame, tuple[Any, ...], tuple[T.Type, ...]]:
        """Validate a branch transfer and return its parent, values, and types."""
        parent = self._require_parent("copy or :escape")
        assert self.runtime_stack is not None and self.branch is not None
        if count < 1:
            raise ValueError("value count must be at least 1")
        if count > len(self.runtime_stack) or count > len(self.branch.stack):
            raise ValueError(
                f"cannot transfer {count} value(s) from a stack of depth "
                f"{len(self.runtime_stack)}"
            )
        return (
            parent,
            tuple(self.runtime_stack[-count:]),
            tuple(self.branch.stack.items[-count:]),
        )

    def copy_to_parent(self, count: int = 1) -> None:
        """Duplicate the top values into the immediate parent frame."""
        parent, values, types = self._checked_transfer(count)
        for value in values:
            _check_duplication_allowed(value)
        duplicated = tuple(_duplicate_occurrence(value) for value in values)
        parent.runtime_stack.extend(duplicated)
        parent.branch = parent.branch.with_stack(parent.branch.stack.push(*types))
        parent.state_version += 1
        self._preparation += 1
        self._hint_cache = None

    def escape_to_parent(self, count: int = 1) -> None:
        """Move the top values into the immediate parent frame."""
        parent, values, types = self._checked_transfer(count)
        assert self.runtime_stack is not None and self.branch is not None
        del self.runtime_stack[-count:]
        self.branch = self.branch.with_stack(self.branch.stack.pop(count))
        parent.runtime_stack.extend(values)
        parent.branch = parent.branch.with_stack(parent.branch.stack.push(*types))
        parent.state_version += 1
        self._state_version += 1
        self._hint_cache = None

    def completion_items(self) -> tuple[CompletionItem, ...]:
        """Return completion metadata derived from the current REPL session."""
        if self.analyser is None or self.branch is None:
            return ()
        items: dict[str, CompletionItem] = {}
        env = self.analyser.env
        depth = 0
        while env is not None:
            scope = "element" if depth == 0 else "built-in element"
            for name in env.overloads:
                text = name.text
                items.setdefault(text, CompletionItem(text, scope))
            for collection, meta in (
                (env.objects, "object"),
                (env.traits, "trait"),
                (env.variants, "variant"),
                (env.enums, "enum"),
            ):
                for name in collection:
                    text = name.text
                    items.setdefault(text, CompletionItem(text, meta))
            for name in env.data_tags:
                text = f"#{name.text}"
                items.setdefault(text, CompletionItem(text, "data tag"))
            env = env.parent
            depth += 1
        for name, typ in self.branch.variables.visible_items():
            text = f"${name.text}"
            items[text] = CompletionItem(text, f"variable: {T.show(typ)}")
        return tuple(items.values())

    def element_documentation(self, name: str, source: str) -> str | None:
        """Render every loaded docstring available for a selected element."""
        normalized = name.strip().removeprefix("\\")
        sections: list[str] = []
        for definition in extract_documented_defines(source):
            if definition.name != normalized:
                continue
            doc = definition.docstring
            lines = [definition.signature, *doc.description]
            lines.extend(
                f"Parameter {item.name}: {item.description}" for item in doc.params
            )
            if doc.returns is not None:
                lines.append(f"Returns: {doc.returns}")
            lines.extend(doc.extra_fields)
            sections.append("\n".join(lines))
        visible = {item.text.removeprefix("\\") for item in self.completion_items()}
        if normalized in visible:
            for reference in collect_language_references(strict=False):
                if normalized not in {
                    reference.name,
                    reference.qualified_name,
                    *reference.aliases,
                }:
                    continue
                lines = [
                    reference.qualified_name,
                    *reference.overloads,
                    reference.summary,
                    *reference.description,
                ]
                lines.extend(
                    f"Parameter {item.name}: {item.description}"
                    for item in reference.parameters
                )
                if reference.returns is not None:
                    lines.append(f"Returns: {reference.returns}")
                sections.append("\n".join(lines))
        return ("\n\n" + "-" * 72 + "\n\n").join(dict.fromkeys(sections)) or None

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
        except (OSError, RuntimeError, VMError) as exc:
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
        return f"Stack types: {format_type_stack(next_branch.stack)}"

    def prepare(
        self,
        source: str = "",
        *,
        fresh: bool = False,
        snapshot: WorkspaceSnapshot | None = None,
    ) -> PreparationResult:
        """Compile an isolated candidate, invalidating every older preparation.

        No committed compiler, loader or VM state changes on failure. A fresh
        load uses the captured root/import view, never current mutable disk
        imports. Ordinary commands continue the committed semantic environment.
        """
        self._preparation += 1
        if snapshot is not None:
            source = snapshot.root.source
            fresh = True
        try:
            syntax = tuple(Parser(lex(source)).parse_program())
            if fresh:
                analyser = Analyser(
                    source_file=snapshot.root.path if snapshot else None,
                    module_loader=snapshot.module_loader() if snapshot else None,
                )
                branch = AnalysisBranch(input_mode=InputMode.TOP_LEVEL)
            else:
                analyser, branch = copy.deepcopy((self.analyser, self.branch))
                # Previously imported modules remain cached session definitions.
                # New command imports use the loaded file's base and current disk
                # rather than being prohibited by its closed source snapshot.
                analyser.module_loader.source_provider = None
                if self.loaded_source is not None:
                    analyser.module_loader.source_overrides.update(
                        {
                            file.path: file.data.decode("utf-8")
                            for file in self.loaded_source.overlays
                            if file.data is not None
                        }
                    )
            analyser.diagnostics.clear()
            analyser.warnings.clear()
            analyser.clear_lints()
            prelude_start = len(analyser.runtime_prelude)
            if fresh:
                prelude_start = 0
                final = analyser.analyse_program_branches(list(syntax))
            else:
                final = analyser.analyse_block(
                    BranchSet((replace(branch, typed_body=()),)),
                    syntax,
                )
            errors = tuple(
                from_message("Type error", item) for item in analyser.diagnostics
            )
            if errors:
                return PreparationResult(None, errors)
            if len(final) != 1:
                return PreparationResult(
                    None,
                    (
                        from_message(
                            "Type error",
                            "source has no single valid stack effect",
                        ),
                    ),
                )
            next_branch = next(iter(final))
            if next_branch.errors:
                return PreparationResult(
                    None,
                    tuple(
                        from_message("Type error", error.message)
                        for error in next_branch.errors
                    ),
                )
            program = compile_program(
                [
                    *analyser.runtime_prelude[prelude_start:],
                    *next_branch.typed_body,
                ]
            )
            advisories = tuple(
                from_message(stage, message)
                for stage, messages in (
                    ("Lint warning", analyser.lints),
                    ("Type warning", analyser.warnings),
                )
                for message in messages
            )
            prepared = PreparedSubmission(
                self._owner,
                self._state_version,
                self._preparation,
                source,
                analyser,
                replace(next_branch, typed_body=()),
                program,
                fresh,
                advisories,
                snapshot,
            )
            return PreparationResult(prepared, advisories)
        except (
            LexError,
            ParseError,
            CompileError,
            OSError,
            RuntimeError,
            VMError,
        ) as exc:
            return PreparationResult(None, exception_diagnostics(exc))

    def execute(
        self,
        prepared: PreparedSubmission,
        *,
        workspace_revision: int | None = None,
    ) -> ExecutionResult:
        """Commit the current preparation and execute its captured program.

        A caller loading a workspace must supply its current workspace revision;
        disk dependencies are checked here as well. UI edits require a new load.
        Prepared submissions are one-shot, tied to this session and generation.
        """
        self._begin_events()
        if (
            prepared.owner != self._owner
            or prepared.generation != self._state_version
            or prepared.preparation != self._preparation
        ):
            raise ValueError("prepared submission is no longer current")
        snapshot = prepared.snapshot
        if snapshot is not None and (
            workspace_revision != snapshot.workspace_revision
            or not snapshot.disk_is_current()
        ):
            self._preparation += 1
            raise ValueError("workspace changed after preparation")
        self._preparation += 1
        if prepared.fresh:
            self.reset()
            self.loaded_source = snapshot
        self.analyser, self.branch = prepared.analyser, prepared.branch
        self._state_version += 1
        self._hint_cache = None
        self.output.did_print = False
        for advisory in prepared.advisories:
            self._emit(DiagnosticEvent(advisory, prepared.source))
        if snapshot is not None:
            self._emit(
                SessionStarted(snapshot.root.document_id, snapshot.root.revision)
            )
        try:
            self.vm.tag_parents.update(dict(prepared.program.tag_parents))
            self.runtime_stack = self.vm.execute(
                prepared.program.main,
                {},
                self.vm.globals,
                initial_stack=list(self.runtime_stack),
            )
            self._emit(
                ResultEvent(
                    tuple(
                        format_runtime_value(
                            value, quote_strings=True, lazy_preview_limit=8
                        )
                        for value in reversed(self.runtime_stack)
                    ),
                    tuple(T.show(typ) for typ in reversed(self.branch.stack.items)),
                    not self.output.did_print,
                )
            )
            return self._outcome(True)
        except (VMError, PanicSignal, OSError, RuntimeError) as exc:
            for diagnostic in exception_diagnostics(exc):
                self._emit(DiagnosticEvent(diagnostic, prepared.source))
            outcome = self._outcome(False)
            if self.reset_on_fault:
                self.reset()
            return outcome

    def run(self, source: str) -> ExecutionResult:
        """Prepare and execute one persistent command with structured failures."""
        result = self.prepare(source)
        if result.prepared is None:
            self._begin_events()
            for item in result.diagnostics:
                self._emit(DiagnosticEvent(item, source))
            return self._outcome(False)
        return self.execute(result.prepared)


def format_type_stack(stack: T.TypeStack) -> str:
    """Format a type-preview row without terminal styling."""
    return "[" + ", ".join(T.show(item) for item in stack) + "]"


def exception_diagnostics(exc: BaseException) -> tuple[Diagnostic, ...]:
    """Translate compiler/runtime failures once for all session frontends."""
    if isinstance(exc, ParseErrors):
        return tuple(
            item for error in exc.errors for item in exception_diagnostics(error)
        )
    panic = exc if isinstance(exc, PanicSignal) else exc.__cause__
    if isinstance(panic, PanicSignal):
        value = panic.value
        if isinstance(value, ObjectValue) and "message" in value.fields:
            return (
                Diagnostic(
                    f"Uncaught panic: {object_type_name(value)}",
                    format_runtime_value(value.fields["message"]),
                ),
            )
        return (Diagnostic("Uncaught panic", format_runtime_value(value)),)
    stages = (
        (LexError, "Lex error"),
        (ParseError, "Parse error"),
        (CompileError, "Compile error"),
        (VMError, "Runtime error"),
    )
    stage = next((label for kind, label in stages if isinstance(exc, kind)), "Error")
    diagnostic = from_exception(stage, exc)
    if getattr(exc, "task_context", ()) or getattr(exc, "secondary_faults", ()):
        diagnostic = replace(diagnostic, message=render_concurrency_fault(exc))
    return (diagnostic,)
