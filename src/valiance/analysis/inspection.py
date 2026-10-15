"""Recoverable, non-executing compiler products for source cursor inspection."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from hashlib import sha256
from pathlib import Path

import valiance.vtypes as T
from valiance.analysis.analyser import Analyser
from valiance.analysis.completion import CompletionContext
from valiance.analysis.diagnostics import Diagnostic, SourceLocation, from_message
from valiance.analysis.traces import TraceCollector
from valiance.asts import (
    DefineNode,
    ElementNode,
    FunctionNode,
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
    _typed_nodes,
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
    locals: tuple[tuple[str, str], ...] = ()
    states: tuple[Inspection, ...] = ()
    unavailable: str = ""
    completion: CompletionContext | None = field(
        default=None, compare=False, repr=False
    )


@dataclass(frozen=True, slots=True)
class CursorRange:
    """A source interval with facts from the actual compiler transformation."""

    start: int
    end: int
    facts: Inspection
    priority: int = 0


@dataclass(frozen=True, slots=True)
class InspectionWorld:
    """One validated function world with a structural correspondence identity."""

    identity: str
    signature: str
    ranges: tuple[CursorRange, ...]


@dataclass(frozen=True, slots=True)
class InspectionScope:
    """Function extent and surviving worlds, independent of cursor page ordinals."""

    identity: str
    start: int
    end: int
    worlds: tuple[InspectionWorld, ...]


@dataclass(frozen=True, slots=True)
class CompletionRecovery:
    """Validate replacements in an unfinished function against its complete body."""

    context: CompletionContext
    roots: tuple[RootSource, ...]
    root: RootSource
    start: int
    end: int

    def candidates(self, prefix: str):
        """Offer only replacements that restore a surviving function world."""
        if not prefix:
            return ()
        result = []
        for item in self.context.candidates(prefix):
            source = (
                self.root.source[: self.start]
                + item.text
                + self.root.source[self.end :]
            )
            root = replace(self.root, source=source)
            roots = tuple(
                root if other.document_id == root.document_id else other
                for other in self.roots
            )
            product = analyse_sources(roots, root, 0)
            scope = product.scope_at(self.start)
            if scope is not None and scope.worlds:
                result.append(item)
        return tuple(result)


@dataclass(frozen=True, slots=True)
class InspectionSnapshot:
    """Revision-bound diagnostics and cursor indexes, safe to hand to the UI."""

    workspace: WorkspaceSnapshot
    diagnostics: tuple[Diagnostic, ...]
    ranges: tuple[CursorRange, ...]

    scopes: tuple[InspectionScope, ...] = ()
    trace_limited: bool = False
    completion_recovery: tuple[CompletionRecovery, ...] = ()

    def completion_at(self, offset: int, world: str | None = None):
        """Use surviving checkpoints, or validate an unfinished call's replacement."""
        facts = self.at(offset, world)
        if facts.completion is not None or facts.states:
            return facts
        recovery = next(
            (
                item
                for item in self.completion_recovery
                if item.start <= offset < item.end
            ),
            None,
        )
        return replace(facts, completion=recovery) if recovery else facts

    def scope_at(self, offset: int) -> InspectionScope | None:
        """Choose the innermost lexical function containing a cursor."""
        scopes = [scope for scope in self.scopes if scope.start <= offset < scope.end]
        return min(scopes, key=lambda scope: scope.end - scope.start, default=None)

    def at(self, offset: int, world: str | None = None) -> Inspection:
        """Resolve exact token hits before gaps and enclosing statement exits."""
        ranges = self.ranges
        scope = self.scope_at(offset)
        if scope and scope.worlds:
            selected = next(
                (item for item in scope.worlds if item.identity == world),
                scope.worlds[0],
            )
            ranges = selected.ranges
        matches = [item for item in ranges if item.start <= offset < item.end]
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
    roots: tuple[RootSource, ...],
    root: RootSource,
    revision: int,
    *,
    trace_limit: int = 20000,
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
    canonical = {
        (type(span.node), span.node.location.offset): span.node
        for span in (*parsed.spans, *parsed.regions)
        if span.node.location
    }
    observations = []
    completion_contexts = {}
    completion_environments = {}
    trace = TraceCollector(trace_limit)

    def observe(owner, node, before, after):
        """Copy presentation facts while compiler branch ownership stays private."""
        if trace.truncated:
            return
        typed = [
            item
            for branch in after
            for item in branch.typed_body
            if item.node is node
            or (
                node.location is not None
                and type(item.node) is type(node)
                and item.node.location == node.location
            )
        ]
        if node.location:
            node = canonical.get((type(node), node.location.offset), node)
            typed = [replace(item, node=node) for item in typed]

        def contexts(branches):
            """Retain only single, valid compiler checkpoints for completion."""
            valid = [branch for branch in branches if not branch.failed]
            return (
                CompletionContext(
                    completion_environments.setdefault(id(node), owner.env),
                    replace(valid[0], typed_body=(), trace=None),
                    node
                    if branches is before and isinstance(node, ElementNode)
                    else None,
                )
                if len(valid) == 1
                else None
            )

        context_pair = (contexts(before), contexts(after))
        completion_contexts[id(node)] = context_pair
        locals_key = (
            tuple(
                (str(name), T.show(typ))
                for name, typ in next(iter(after)).variables.visible_items()
            )
            if len(after) == 1
            else ()
        )
        completion_contexts[(id(node), _stack(before), _stack(after), locals_key)] = (
            context_pair
        )
        operation = (
            root.source[node.location.offset :].splitlines()[0] if node.location else ""
        )
        if isinstance(node, ElementNode):
            signatures = {
                _overload_signature(
                    str(node.name),
                    replace(
                        item.overload.overload,
                        params=item.overload.params,
                        returns=item.overload.actual_returns,
                    ),
                )
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
            (
                node,
                _stack(before),
                _stack(after),
                operation,
                typed,
                owner,
                tuple(
                    (str(name), T.show(typ))
                    for name, typ in next(iter(after)).variables.visible_items()
                )
                if len(after) == 1
                else (),
            )
        )

    analyser = Analyser(
        source_file=root.path,
        module_loader=loader,
        observer=observe,
        trace=trace,
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
    all_observations = tuple(observations)
    definitions = _scope_nodes(parsed)
    # Read committed parent products, excluding provisional child observations.
    committed = [
        typed
        for observation in all_observations
        if observation[5] is analyser
        for typed in observation[4]
    ]
    committed_functions = [
        typed
        for typed in _typed_nodes(committed)
        if isinstance(typed, TypedFunctionNode)
    ]
    keys = {_function_key(node) for node in definitions}
    for typed in committed_functions:
        if (
            isinstance(typed.node, FunctionNode)
            and _function_key(typed.node) not in keys
        ):
            definitions.append(typed.node)
            keys.add(_function_key(typed.node))
    regions = _scope_regions(parsed, definitions)
    source_scopes = {_function_key(node): node for node in definitions}
    accepted = {}
    for typed in committed_functions:
        if isinstance(typed, TypedFunctionNode):
            source = source_scopes.get(_function_key(typed.node))
            if source is not None:
                accepted.setdefault(id(source), []).extend(typed.overloads)
    ranges = _inspection_ranges(
        root,
        parsed,
        analyser,
        queries,
        uri,
        all_observations,
        all_observations,
        accepted_scopes=accepted,
        scope_definitions=definitions,
        completion_contexts=completion_contexts,
    )
    recovery = []
    if not trace.truncated:
        for span in parsed.spans:
            context = completion_contexts.get(id(span.node), (None, None))[0]
            if context is None or not isinstance(
                span.node, (ElementNode, GetVariableNode)
            ):
                continue
            if any(
                region.start <= span.start < region.end and identity not in accepted
                for identity, region in regions.items()
            ):
                recovery.append(
                    CompletionRecovery(
                        context,
                        roots,
                        root,
                        span.start,
                        span.start
                        + len(str(span.node.name))
                        + int(isinstance(span.node, GetVariableNode)),
                    )
                )
    declaration_products = [
        replace(
            typed, overloads=tuple(replace(item, body=()) for item in typed.overloads)
        )
        for observation in all_observations
        for typed in observation[4]
        if isinstance(typed, TypedFunctionNode) and isinstance(typed.node, DefineNode)
    ]
    scopes = []
    for function, exits in trace.functions if not trace.truncated else ():
        if not function.location or not exits:
            continue
        definition = source_scopes.get(_function_key(function))
        if definition is None or id(definition) not in accepted:
            continue
        region = regions[id(definition)]
        # A valid inner analysis cannot make a rejected enclosing definition usable.
        if any(
            item.priority == 5 and item.start <= region.start and item.end >= region.end
            for item in ranges
        ):
            continue
        parents = [
            item
            for item in definitions
            if id(item) in regions
            and regions[id(item)].start <= region.start
            and regions[id(item)].end >= region.end
        ]
        parents.sort(key=lambda item: regions[id(item)].start)
        scope_identity = "/".join(_scope_name(item) for item in parents)
        peers = [
            item
            for item in definitions
            if _scope_name(item) == _scope_name(definition)
            and regions.get(id(item))
            and regions[id(item)].start < region.start
        ]
        scope_identity += ":" + str(len(peers))
        worlds = []
        for overload, ancestry, body in exits:
            if not any(
                item.overload == overload and item.body == body
                for item in accepted[id(definition)]
            ):
                continue
            observations.clear()
            completion_contexts.clear()
            checkpoints = list(trace.checkpoints(ancestry))
            checkpoints.sort(
                key=lambda item: item[0].location.offset if item[0].location else -1
            )
            for node, before, after in checkpoints:
                if node.location is None:
                    continue
                original = canonical.get((type(node), node.location.offset), node)
                typed = tuple(replace(item, node=original) for item in after.typed_body)
                observe(
                    analyser, original, (before,), (replace(after, typed_body=typed),)
                )
            signature = _overload_signature(_scope_name(definition), overload)
            decisions = tuple(
                (type(node).__name__, operation)
                for node, _, _, operation, _, _, _ in observations
            )
            identity = sha256(repr((signature, decisions)).encode()).hexdigest()
            if any(world.identity == identity for world in worlds):
                continue
            queries.typed_programs[uri] = declaration_products + [
                typed for observation in observations for typed in observation[4]
            ]
            world_ranges = _inspection_ranges(
                root,
                parsed,
                analyser,
                queries,
                uri,
                tuple(observations),
                all_observations,
                definition,
                signature,
                tuple(
                    node.location.offset
                    for node, _, after in checkpoints
                    if node.location
                    and (
                        after.terminal
                        or after.return_stack is not None
                        or after.break_type is not None
                    )
                ),
                accepted_scopes=accepted,
                scope_definitions=definitions,
                completion_contexts=completion_contexts,
            )
            worlds.append(InspectionWorld(identity, signature, world_ranges))
        if worlds:
            existing = next(
                (
                    scope
                    for scope in scopes
                    if scope.identity == scope_identity
                    and scope.start == region.start
                    and scope.end == region.end
                ),
                None,
            )
            if existing is not None:
                scopes.remove(existing)
                identities = {world.identity for world in existing.worlds}
                worlds = [
                    *existing.worlds,
                    *(world for world in worlds if world.identity not in identities),
                ]
            scopes.append(
                InspectionScope(scope_identity, region.start, region.end, tuple(worlds))
            )
    if trace.truncated:
        scopes.clear()
        ranges = (CursorRange(0, len(root.source) + 1, Inspection(), 5),)
    workspace = WorkspaceSnapshot(
        root,
        revision,
        tuple(capture.files[path] for path in sorted(capture.files)),
        overlays,
        loader.std_root,
    )
    return InspectionSnapshot(
        workspace,
        tuple(dict.fromkeys(diagnostics)),
        tuple(ranges),
        tuple(scopes),
        trace.truncated,
        tuple(recovery) if not trace.truncated else (),
    )


def _inspection_ranges(
    root,
    parsed,
    analyser,
    queries,
    uri,
    observations,
    all_observations,
    scope_definition=None,
    signature="",
    terminal_offsets=(),
    accepted_scopes=(),
    scope_definitions=None,
    completion_contexts=None,
):
    """Build source hit indexes from already collected semantic checkpoints."""
    spans = {id(span.node): span for span in parsed.spans}
    definitions = (
        scope_definitions if scope_definitions is not None else _scope_nodes(parsed)
    )
    regions = _scope_regions(parsed, definitions)

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
        if signature and definition is scope_definition:
            return signature
        if isinstance(definition, FunctionNode):
            return "fn"
        overloads = analyser.env.overloads_for(definition.name)
        if len(overloads) == 1:
            return _overload_signature(str(definition.name), overloads[0])
        return str(definition.name)

    window = regions.get(id(scope_definition))
    source_regions = tuple(
        region
        for region in parsed.regions
        if window is None or window.start <= region.start < window.end
    )
    source_spans = tuple(
        span
        for span in parsed.spans
        if window is None or window.start <= span.start < window.end
    )
    boundaries = tuple(
        boundary
        for boundary in parsed.boundaries
        if window is None or window.start <= boundary.start < window.end
    )
    records = {}
    completion_contexts = completion_contexts or {}
    states = {}
    before_contexts = {}
    for node, before, after, operation, _typed, _owner, locals_ in observations:
        if node.location is None:
            continue
        key = id(node)
        candidate = completion_contexts.get(
            (key, before, after, locals_), (None, None)
        )[0]
        if candidate is not None:
            before_contexts.setdefault(key, {})[
                (candidate.branch.stack, candidate.branch.variables)
            ] = candidate
        facts = (before, after, operation, context(node))
        state = Inspection(
            after,
            operation,
            context(node),
            locals=locals_,
            completion=completion_contexts.get(
                (id(node), before, after, locals_), (None, None)
            )[1],
        )
        if state not in states.setdefault(key, []):
            states[key].append(state)
        if key in records and records[key] != facts:
            records[key] = (None, None, "", context(node))
        else:
            records[key] = facts
    ranges = []
    successful = set(accepted_scopes)
    for definition in definitions:
        region = regions.get(id(definition))
        if region is not None and id(definition) not in successful:
            ranges.append(CursorRange(region.start, region.end, Inspection(), 5))

    def checkpoint(key, after, operation, function):
        """Label divergent control-flow facts without treating them as overloads."""
        alternatives = states.get(key, [])
        if len(alternatives) == 1:
            return alternatives[0]
        if alternatives:
            return Inspection(function=function, states=tuple(alternatives))
        return Inspection(after, operation, function)

    for boundary in boundaries:
        if id(boundary.node) in records:
            _, after, operation, function = records[id(boundary.node)]
            ranges.append(
                CursorRange(
                    boundary.start,
                    boundary.end,
                    checkpoint(id(boundary.node), after, operation, function),
                    3,
                )
            )
    for span in source_spans:
        if id(span.node) not in records:
            continue
        _, after, operation, function = records[id(span.node)]
        line_end = root.source.find("\n", span.end)
        end = len(root.source) + 1 if line_end < 0 else line_end + 1
        following = min(
            (
                item.start
                for item in source_spans
                if item.start >= span.end and item is not span
            ),
            default=end,
        )
        ranges.append(
            CursorRange(
                span.end,
                min(end, following),
                checkpoint(id(span.node), after, operation, function),
                2,
            )
        )
    # Statement exits retain their stack through following blank lines. Failed
    # statements explicitly replace it with unavailable rather than an older fact.
    for region in source_regions:
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
            CursorRange(
                region.end,
                end,
                checkpoint(id(region.node), after, operation, function),
                1,
            )
        )
        ranges.append(
            CursorRange(region.start, region.end, Inspection(function=function), 0)
        )
    for node, _before, _after, operation, _typed, _owner, _locals in observations:
        span = spans.get(id(node))
        if span is None or not isinstance(node, (ElementNode, GetVariableNode)):
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
        selected = Inspection(
            element=operation,
            documentation=hover["contents"]["value"] if hover else "",
            target=target,
            completion=completion_contexts.get(id(node), (None, None))[0],
        )
        alternatives = []
        for candidate in before_contexts.get(id(node), {}).values():
            alternatives.append(replace(selected, completion=candidate))
        if len(alternatives) > 1:
            selected = replace(selected, states=tuple(alternatives))
        ranges.append(
            CursorRange(
                node.location.offset,
                node.location.offset + width,
                selected,
                4,
            )
        )
    # Comments, syntax-error lines and unvisited operands have no reliable facts.
    for start, end in comment_ranges(root.source):
        ranges.append(CursorRange(start, end, Inspection(), 5))
    visited = {id(node) for node, *_ in observations}
    visited.update(
        id(node)
        for node, *_ in all_observations
        if isinstance(node, (DefineNode, FunctionNode))
    )
    for span in source_spans:
        if id(span.node) not in visited:
            unreachable = window is not None and any(
                window.start <= offset < span.start for offset in terminal_offsets
            )
            facts = Inspection(unavailable="Unreachable" if unreachable else "")
            facts = replace(
                facts,
                completion=completion_contexts.get(id(span.node), (None, None))[0],
            )
            ranges.append(CursorRange(span.start, span.end, facts, 5))
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
    from valiance.analysis.state import AnalysisBranch, InputMode

    if window is not None:
        initial = min(
            (
                node
                for node, *_ in observations
                if node.location
                and window.start < node.location.offset < window.end
                and completion_contexts.get(id(node), (None, None))[0] is not None
            ),
            key=lambda node: node.location.offset,
            default=None,
        )
        if initial is not None:
            ranges.append(
                CursorRange(
                    window.start,
                    initial.location.offset,
                    Inspection(completion=completion_contexts[id(initial)][0]),
                    -2,
                )
            )

    ranges.append(
        CursorRange(
            0,
            first,
            Inspection(
                stack=(),
                completion=CompletionContext(
                    analyser.env, AnalysisBranch(input_mode=InputMode.TOP_LEVEL)
                ),
            ),
            -1,
        )
    )
    return tuple(
        item
        for item in ranges
        if window is None or item.start < window.end and item.end > window.start
    )


def _scope_nodes(parsed):
    """Index named definitions and independent anonymous lexical functions."""
    nodes = tuple(_ast_nodes(parsed.nodes))
    named = {id(node.function) for node in nodes if isinstance(node, DefineNode)}
    return [
        node
        for node in nodes
        if isinstance(node, DefineNode)
        or isinstance(node, FunctionNode)
        and id(node) not in named
    ]


def _scope_name(node):
    """Return a source-level scope label without using analyser allocation IDs."""
    return str(node.name) if isinstance(node, DefineNode) else "fn"


def _function_key(node):
    """Match copied function syntax by its header and first body source operation."""
    function = node.function if isinstance(node, DefineNode) else node
    location = function.location
    first = next((item.location.offset for item in function.body if item.location), -1)
    return (location.offset if location else -1, first)


def _scope_regions(parsed, definitions=None):
    """Include lowered modifier functions using their retained body source spans."""
    from valiance.parsing.parser import SourceSpan

    regions = {id(span.node): span for span in (*parsed.spans, *parsed.regions)}
    for node in definitions if definitions is not None else _scope_nodes(parsed):
        if id(node) in regions or not isinstance(node, FunctionNode):
            continue
        members = {
            (type(item), item.location.offset)
            for item in _ast_nodes(node.body)
            if item.location
        }
        spans = [
            span
            for span in parsed.spans
            if span.node.location
            and (type(span.node), span.node.location.offset) in members
        ]
        if spans:
            start = min(
                span.node.location.offset for span in spans if span.node.location
            )
            regions[id(node)] = SourceSpan(node, start, max(span.end for span in spans))
    return regions
