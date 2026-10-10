"""Recoverable, non-executing compiler products for source cursor inspection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import valiance.vtypes as T
from valiance.analysis.analyser import Analyser
from valiance.analysis.diagnostics import Diagnostic, SourceLocation, from_message
from valiance.asts import (
    DefineNode,
    ElementNode,
    GetVariableNode,
    TypedElementNode,
    TypedFunctionNode,
)
from valiance.incremental.snapshots import (
    CapturedFile,
    RootSource,
    SourceCapture,
    WorkspaceSnapshot,
)
from valiance.modules_system.modules import ModuleLoader
from valiance.parsing.parser import parse_with_diagnostics
from valiance.source_queries import (
    SourceQueries,
    _ast_nodes,
    _overload_signature,
    comment_ranges,
)


@dataclass(frozen=True, slots=True)
class Inspection:
    """Frontend-neutral immutable cursor facts; None means unavailable stack."""

    stack: tuple[str, ...] | None = None
    last_element: str = ""
    function: str = ""
    element: str = ""
    documentation: str = ""
    target: tuple[Path | None, int, int] | None = None


@dataclass(frozen=True, slots=True)
class CursorRange:
    """A source interval with facts from the actual compiler transformation."""

    start: int
    end: int
    facts: Inspection
    priority: int = 0


@dataclass(frozen=True, slots=True)
class InspectionSnapshot:
    """Revision-bound diagnostics and cursor indexes, safe to hand to the UI."""

    workspace: WorkspaceSnapshot
    diagnostics: tuple[Diagnostic, ...]
    ranges: tuple[CursorRange, ...]

    def at(self, offset: int) -> Inspection:
        """Resolve exact token hits before gaps and enclosing statement exits."""
        matches = [item for item in self.ranges if item.start <= offset < item.end]
        if not matches:
            return Inspection()
        return max(
            matches, key=lambda item: (item.priority, -item.end + item.start)
        ).facts


def _stack(branches) -> tuple[str, ...] | None:
    """Expose only a single non-terminal compiler state in this stage."""
    if len(branches) != 1:
        return None
    states = {
        tuple(T.show(typ) for typ in reversed(branch.stack.items))
        for branch in branches
        if not branch.failed and not branch.terminal
    }
    return next(iter(states)) if len(states) == 1 else None


def analyse_sources(
    roots: tuple[RootSource, ...], root: RootSource, revision: int
) -> InspectionSnapshot:
    """Analyse a private captured workspace, including failed imported sources.

    No VM or code generation is involved. Declaration recovery preserves valid
    independent functions; executable failure never fabricates a following stack.
    """
    overlays = tuple(
        CapturedFile(item.path.resolve(), item.source.encode(), True, item.revision)
        for item in roots
        if item.path is not None
    )
    capture = SourceCapture(overlays)
    capture.capture_configuration(root.base_directory)
    loader = ModuleLoader(base_directory=root.base_directory, source_provider=capture)
    parsed = parse_with_diagnostics(root.source)
    observations = []

    def observe(owner, node, before, after):
        """Copy presentation facts while compiler branch ownership stays private."""
        typed = [
            item for branch in after for item in branch.typed_body if item.node is node
        ]
        operation = (
            root.source[node.location.offset :].splitlines()[0] if node.location else ""
        )
        if isinstance(node, ElementNode):
            signatures = {
                _overload_signature(str(node.name), item.overload.overload)
                for item in typed
                if isinstance(item, TypedElementNode) and item.overload is not None
            }
            operation = (
                next(iter(signatures)) if len(signatures) == 1 else str(node.name)
            )
        elif isinstance(node, GetVariableNode):
            types = {T.show(item.typ) for item in typed if item.typ is not None}
            operation = f"${node.name}: " + (
                next(iter(types)) if len(types) == 1 else "unavailable"
            )
        else:
            operation = type(node).__name__.removesuffix("Node")
            types = {T.show(item.typ) for item in typed if item.typ is not None}
            if len(types) == 1:
                operation += ": " + next(iter(types))
        observations.append(
            (node, _stack(before), _stack(after), operation, typed, owner)
        )

    analyser = Analyser(
        source_file=root.path,
        module_loader=loader,
        observer=observe,
        recover_declarations=True,
    )
    analyser.analyse(list(parsed.nodes))
    diagnostics = []

    def gather(source, path, result, analysis):
        """Collect recoverable source diagnostics with their actual file identity."""
        diagnostics.extend(
            Diagnostic(
                type(error).__name__,
                error.message,
                SourceLocation(error.line or 1, error.column or 1),
                source_file=path,
            )
            for error in result.diagnostics
        )
        for message in analysis.diagnostics:
            diagnostic = from_message("Type error", message)
            diagnostics.append(
                Diagnostic(
                    diagnostic.stage,
                    diagnostic.message,
                    diagnostic.location,
                    diagnostic.help,
                    diagnostic.source_file or path,
                )
            )

    gather(root.source, root.path, parsed, analyser)
    seen = {root.path.resolve()} if root.path else set()
    while True:
        pending = [
            file
            for file in capture.files.values()
            if file.path.suffix == ".vlnc"
            and file.data is not None
            and file.path not in seen
        ]
        if not pending:
            break
        for file in pending:
            seen.add(file.path)
            capture.capture_configuration(file.path.parent)
            try:
                source = file.data.decode("utf-8")
            except UnicodeError as error:
                diagnostics.append(
                    Diagnostic("Source error", str(error), source_file=file.path)
                )
                continue
            result = parse_with_diagnostics(source)
            imported = Analyser(
                source_file=file.path, module_loader=loader, recover_declarations=True
            )
            imported.analyse(list(result.nodes))
            gather(source, file.path, result, imported)

    workspace = WorkspaceSnapshot(
        root,
        revision,
        tuple(capture.files[path] for path in sorted(capture.files)),
        overlays,
        loader.std_root,
    )
    queries = SourceQueries(loader)
    uri = root.path.as_uri() if root.path else "untitled:" + root.document_id
    queries.documents[uri] = root.source
    queries.documents.update(
        {
            file.path.as_uri(): file.data.decode("utf-8")
            for file in (*workspace.files, *overlays)
            if file.data is not None and file.path.suffix == ".vlnc"
        }
    )
    queries.programs[uri] = list(parsed.nodes)
    queries.analysers[uri] = analyser
    queries.typed_programs[uri] = [
        typed for observation in observations for typed in observation[4]
    ]
    spans = {id(span.node): span for span in parsed.spans}
    regions = {id(span.node): span for span in parsed.regions}
    definitions = [
        node for node in _ast_nodes(parsed.nodes) if isinstance(node, DefineNode)
    ]

    def context(node):
        """Use the innermost enclosing definition, without leaking its body outward."""
        offset = node.location.offset if node.location else -1
        candidates = [
            definition
            for definition in definitions
            if definition is not node
            and id(definition) in regions
            and regions[id(definition)].start <= offset < regions[id(definition)].end
        ]
        if not candidates:
            return ""
        definition = min(
            candidates, key=lambda item: regions[id(item)].end - regions[id(item)].start
        )
        overloads = analyser.env.overloads_for(definition.name)
        if len(overloads) == 1:
            return _overload_signature(str(definition.name), overloads[0])
        return str(definition.name)

    records = {}
    for node, before, after, operation, _typed, _owner in observations:
        if node.location is None:
            continue
        key = id(node)
        facts = (before, after, operation, context(node))
        if key in records and records[key] != facts:
            records[key] = (None, None, "", context(node))
        else:
            records[key] = facts
    ranges = []
    successful = {
        id(node)
        for node, _, after, _, typed, _ in observations
        if after is not None
        and any(
            isinstance(item, TypedFunctionNode) and item.overloads for item in typed
        )
    }
    for definition in definitions:
        region = regions.get(id(definition))
        if region is not None and id(definition) not in successful:
            ranges.append(CursorRange(region.start, region.end, Inspection(), 5))
    for boundary in parsed.boundaries:
        if id(boundary.node) in records:
            _, after, operation, function = records[id(boundary.node)]
            ranges.append(
                CursorRange(
                    boundary.start,
                    boundary.end,
                    Inspection(after, operation, function),
                    3,
                )
            )
    # Statement exits retain their stack through following blank lines. Failed
    # statements explicitly replace it with unavailable rather than an older fact.
    for region in parsed.regions:
        before, after, operation, function = records.get(
            id(region.node), (None, None, "", "")
        )
        following = min(
            (item.start for item in parsed.regions if item.start >= region.end),
            default=len(root.source) + 1,
        )
        enclosing = [
            item.end
            for item in parsed.regions
            if item.start < region.start and item.end > region.end
        ]
        end = min([following, *enclosing])
        ranges.append(
            CursorRange(region.end, end, Inspection(after, operation, function), 1)
        )
        ranges.append(
            CursorRange(region.start, region.end, Inspection(function=function), 0)
        )
    for node, _before, _after, operation, typed, _owner in observations:
        span = spans.get(id(node))
        if (
            span is None
            or not typed
            or not isinstance(node, (ElementNode, GetVariableNode))
        ):
            continue
        position = {
            "line": node.location.line - 1,
            "character": node.location.column - 1,
        }
        params = {"textDocument": {"uri": uri}, "position": position}
        hover = queries._hover(params)
        navigation = queries._definition(params)
        target = None
        if navigation:
            from valiance.source_queries import _uri_path

            start = navigation["range"]["start"]
            target = (_uri_path(navigation["uri"]), start["line"], start["character"])
        width = len(str(node.name)) + int(isinstance(node, GetVariableNode))
        ranges.append(
            CursorRange(
                span.start,
                span.start + width,
                Inspection(
                    element=operation,
                    documentation=hover["contents"]["value"] if hover else "",
                    target=target,
                ),
                4,
            )
        )
    # Comments, syntax-error lines and unvisited operands have no reliable facts.
    for start, end in comment_ranges(root.source):
        ranges.append(CursorRange(start, end, Inspection(), 5))
    visited = {id(node) for node, *_ in observations}
    for span in parsed.spans:
        if id(span.node) not in visited:
            ranges.append(CursorRange(span.start, span.end, Inspection(), 5))
    for error in parsed.diagnostics:
        lines = root.source.splitlines(keepends=True)
        start = sum(len(line) for line in lines[: (error.line or 1) - 1])
        end = (
            start + len(lines[(error.line or 1) - 1])
            if (error.line or 1) <= len(lines)
            else start + 1
        )
        ranges.append(CursorRange(start, end, Inspection(), 5))
    # Before the first executable statement an empty module stack is known.
    first = min((span.start for span in parsed.regions), default=len(root.source) + 1)
    ranges.append(CursorRange(0, first, Inspection(stack=()), -1))
    workspace = WorkspaceSnapshot(
        root,
        revision,
        tuple(capture.files[path] for path in sorted(capture.files)),
        overlays,
        loader.std_root,
    )
    return InspectionSnapshot(
        workspace, tuple(dict.fromkeys(diagnostics)), tuple(ranges)
    )
