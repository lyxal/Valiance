"""Shared compiler-backed source queries, independent of LSP transport and UI."""

from __future__ import annotations

import re
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import valiance.vtypes as T
from valiance.analysis import Analyser
from valiance.asts import (
    ASTNode,
    DefineNode,
    ElementNode,
    GetVariableNode,
    ImportNode,
    SetVariableNode,
)
from valiance.asts.nodes import TypedElementNode, TypedFunctionNode, TypedNode
from valiance.elements.builtins import BUILTIN_ELEMENTS, BuiltinElement
from valiance.elements.documentation import ElementDocumentation
from valiance.elements.stdlib_native import native_stdlib_functions
from valiance.modules_system.modules import ModuleLoader, ModuleLoadError
from valiance.parsing import LexError, ParseError, ParseErrors, parse
from valiance.parsing.parser import parse_with_diagnostics
from valiance.source_tools import extract_documented_defines
from valiance.vtypes.symbols import Symbol

_WORD = re.compile(
    r"(?:\*::|[$#\\])?[A-Za-z_][A-Za-z0-9_]*(?:(?:::|\.)[A-Za-z_][A-Za-z0-9_]*)*"
    r"|[+\-*%!?=/< >~&^]+".replace(" ", "")
)
_LOCATION = re.compile(r"^(\d+):(\d+):\s*(.*)$", re.DOTALL)


class SourceQueries:
    """Query overload documentation and semantic source navigation."""

    def __init__(self, module_loader=None):
        """Own analysed views installed by the caller’s compiler workspace."""
        self.documents = {}
        self.programs = {}
        self.typed_programs = {}
        self.analysers = {}
        self.module_loader = module_loader or ModuleLoader()
        self._query_sources = {}

    def _node_view(self, uri, *, typed=False):
        """Index one installed syntax product once, invalidating on replacement."""
        products = self.typed_programs if typed else self.programs
        product = products.get(uri)
        cache = getattr(self, "_node_views", None)
        if cache is None:
            cache = self._node_views = {}
        key = (uri, typed)
        cached = cache.get(key)
        if cached is not None and cached[0] is product:
            return cached[1]
        if key not in cache and len(cache) >= 16:
            del cache[next(iter(cache))]
        kind = TypedNode if typed else ASTNode
        view = _NodeIndex(_dataclass_nodes(product, kind), kind)
        cache[key] = (product, view)
        return view

    def _documented_overloads(self, source: str, name: str):
        """Reuse bounded source documentation products across world queries."""
        cache = getattr(self, "_documentation_cache", None)
        if cache is None:
            cache = self._documentation_cache = {}
        if source not in cache:
            if len(cache) >= 8:
                del cache[next(iter(cache))]
            cache[source] = tuple(
                (
                    item.name,
                    item.signature,
                    _docstring_markdown(item.docstring),
                    item.line,
                )
                for item in extract_documented_defines(
                    source, program=parse_with_diagnostics(source).nodes
                )
            )
        return tuple(
            (signature, doc)
            for item_name, signature, doc, _ in cache[source]
            if item_name == name
        )

    def _declaration_documentation(self, source: str, name: str, line: int) -> str:
        """Reuse exact declaration docs, including an intentionally absent docstring."""
        self._documented_overloads(source, name)
        return next(
            (
                doc
                for item_name, _, doc, row in self._documentation_cache[source]
                if item_name == name and row == line
            ),
            "",
        )

    def _source_text(self, path):
        """Prefer editor overlays and otherwise read through the owned loader."""
        uri = path.as_uri()
        if uri in self.documents:
            return self.documents[uri]
        return self.module_loader._read_bytes(path).decode("utf-8")

    def _analysed_source(self, path):
        """Reuse imported query analysis while its overlay or captured text matches."""
        source = self._source_text(path)
        if not hasattr(self, "_query_sources"):
            self._query_sources = {}
        cached = self._query_sources.get(path)
        if cached is not None and cached[0] == source:
            return cached
        program = parse(source)
        analyser = Analyser(source_file=path, module_loader=self.module_loader)
        typed = analyser.analyse(program)
        result = (source, program, typed)
        self._query_sources[path] = result
        return result

    def _hover(self, params: dict[str, Any]) -> dict[str, Any] | None:
        """Return overload-specific signatures, documentation, and variable types."""
        uri = params["textDocument"]["uri"]
        source = self.documents.get(uri, "")
        position = params["position"]
        word = _word_at(source, position)
        analyser = self.analysers.get(uri)
        if not word or analyser is None:
            return None
        display_name = word.lstrip("$#\\")
        lookup_name = word[3:] if word.startswith("*::") else display_name

        if word.startswith("$"):
            variable_type = _variable_type_at(
                self._node_view(uri, typed=True),
                lookup_name,
                position,
                source=source,
            )
            if variable_type is None:
                return None
            return {
                "contents": {
                    "kind": "markdown",
                    "value": (
                        f"```valiance\n${display_name}: {T.show(variable_type)}\n```"
                    ),
                },
                "range": _word_range(source, position),
            }

        declaration = _definition_at_position(
            source, self._node_view(uri), lookup_name, position
        )
        if declaration is not None:
            overloads = _definition_overloads(
                self._node_view(uri, typed=True), declaration
            )
            documentation = self._declaration_documentation(
                source, lookup_name, declaration.location.line
            )
            value = _render_overload_hover(display_name, overloads, (documentation,))
            return {
                "contents": {"kind": "markdown", "value": value},
                "range": _word_range(source, position),
            }

        selected = _selected_overload_at(
            self._node_view(uri, typed=True), lookup_name, position
        )
        overloads = analyser.env.overloads_for(Symbol(lookup_name))
        if not overloads and selected is None:
            return None
        docs = self._overload_documentation(
            uri, lookup_name, position, overloads, selected
        )
        stdlib_documentation = self._stdlib_documentation(uri, lookup_name, selected)
        builtin = _builtin_for_overloads(lookup_name, overloads, selected)
        builtin_documentation = (
            _element_documentation_markdown(builtin.documentation)
            if builtin is not None and builtin.documentation is not None
            else ""
        )
        builtin_overload_documentation = (
            tuple(
                _element_documentation_markdown(documentation)
                if (documentation := builtin.documentation_for(overload)) is not None
                else ""
                for overload in overloads
            )
            if builtin is not None
            else tuple("" for _ in overloads)
        )
        if selected is not None:
            selected_doc = self._selected_overload_documentation(
                uri, lookup_name, position, selected
            )
            if not selected_doc and builtin is not None:
                selected_builtin_documentation = builtin.documentation_for(selected)
                if selected_builtin_documentation is not None:
                    selected_doc = _element_documentation_markdown(
                        selected_builtin_documentation
                    )
            if not selected_doc:
                selected_doc = stdlib_documentation or builtin_documentation
            value = _render_single_overload_hover(display_name, selected, selected_doc)
        else:
            shared_documentation = stdlib_documentation or builtin_documentation
            if not any(docs) and any(builtin_overload_documentation):
                docs = builtin_overload_documentation
            elif shared_documentation and not any(docs):
                docs = tuple(shared_documentation for _ in overloads)
            value = _render_overload_hover(display_name, overloads, docs)
        return {
            "contents": {"kind": "markdown", "value": value},
            "range": _word_range(source, position),
        }

    def _overload_documentation(
        self,
        uri: str,
        name: str,
        position: dict[str, int],
        overloads: tuple[T.Overload, ...],
        selected: T.Overload | None,
    ) -> tuple[str, ...]:
        """Return documentation aligned with each local or imported overload."""
        source = self.documents.get(uri, "")
        local = self._documented_overloads(source, name)
        if local:
            return _match_overload_docs(overloads, local)

        target = self._definition_source(uri, name, position, selected)
        if target is None:
            return tuple("" for _ in overloads)
        _, target_source, target_name = target
        documented = self._documented_overloads(target_source, target_name)
        return _match_overload_docs(overloads, documented)

    def _selected_overload_documentation(
        self,
        uri: str,
        name: str,
        position: dict[str, int],
        selected: T.Overload,
    ) -> str:
        """Load the exact docstring belonging to the selected call overload."""
        # Resolve the declaration first: lexical shadowing can give two local
        # declarations identical signatures but different documentation.
        location = self._definition(
            {"textDocument": {"uri": uri}, "position": position}
        )
        if location is None:
            return self._documentation_from_import_sources(uri, name, selected)
        source_uri = location["uri"]
        source = self.documents.get(source_uri)
        if source is None:
            path = _uri_path(source_uri)
            if path is None:
                return ""
            try:
                source = self._source_text(path)
            except OSError:
                return ""
        if source_uri in self.programs:
            program = self._node_view(source_uri)
        else:
            try:
                program = parse(source)
            except (LexError, ParseError, ParseErrors):
                return ""
        declaration = _definition_at_lsp_start(
            source, program, location["range"]["start"]
        )
        if declaration is None or declaration.location is None:
            return self._documentation_from_import_sources(uri, name, selected)
        declared_name = str(declaration.name).removeprefix("\\")
        documentation = self._declaration_documentation(
            source, declared_name, declaration.location.line
        )
        if source_uri == uri:
            return documentation
        return (
            self._documentation_from_import_sources(uri, name, selected)
            or documentation
        )

    def _documentation_from_import_sources(
        self, uri: str, name: str, selected: T.Overload
    ) -> str:
        """Match selected-overload docs directly from imported source files."""
        current_file = _uri_path(uri)
        loader = self.module_loader
        for node in self.programs.get(uri, []):
            if not isinstance(node, ImportNode):
                continue
            for spec in node.specs:
                for module_path, imported_name in _import_definition_candidates(
                    spec, name
                ):
                    try:
                        source_file = loader.resolve(
                            module_path, current_file=current_file
                        )
                        source_uri = source_file.as_uri()
                        source = self.documents.get(source_uri)
                        if source is None:
                            source = self._source_text(source_file)
                    except (ModuleLoadError, OSError):
                        continue
                    documented = self._documented_overloads(source, imported_name)
                    matched = _match_overload_docs((selected,), documented)
                    if matched and matched[0]:
                        return matched[0]
        return ""

    def _stdlib_documentation(
        self, uri: str, name: str, selected: T.Overload | None
    ) -> str:
        """Return metadata documentation for an imported native stdlib element."""
        modules = native_stdlib_functions()
        for node in self.programs.get(uri, []):
            if not isinstance(node, ImportNode):
                continue
            for spec in node.specs:
                for module_path, imported_name in _import_definition_candidates(
                    spec, name
                ):
                    parts = tuple(module_path.parts)
                    if not parts or parts[0] != "std" or len(parts) < 2:
                        continue
                    module_name = ".".join(parts[1:])
                    functions = modules.get(module_name, ())
                    for function in functions:
                        if function.name.text != imported_name:
                            continue
                        overload = T.Overload(
                            function.params,
                            function.returns,
                            param_names=function.param_names,
                        )
                        if selected is not None and not _same_overload(
                            overload, selected
                        ):
                            continue
                        if function.documentation is not None:
                            return _element_documentation_markdown(
                                function.documentation
                            )
        return ""

    def _definition_source(
        self,
        uri: str,
        name: str,
        position: dict[str, int],
        selected: T.Overload | None,
    ) -> tuple[str, str, str] | None:
        """Resolve the source URI, text, and declared name for one element."""
        location = self._definition(
            {"textDocument": {"uri": uri}, "position": position}
        )
        if location is None:
            return None
        source_uri = location["uri"]
        source = self.documents.get(source_uri)
        if source is None:
            path = _uri_path(source_uri)
            if path is None:
                return None
            try:
                source = self._source_text(path)
            except OSError:
                return None
        try:
            program = parse(source)
        except (LexError, ParseError, ParseErrors):
            return None
        start = location["range"]["start"]
        declaration = _definition_at_lsp_start(source, program, start)
        declared_name = (
            str(declaration.name).removeprefix("\\")
            if declaration is not None
            else name
        )
        return source_uri, source, declared_name

    def _definition(self, params: dict[str, Any]) -> dict[str, Any] | None:
        """Find local or imported definitions, including project dependencies."""
        uri = params["textDocument"]["uri"]
        source = self.documents.get(uri, "")
        word = _word_at(source, params["position"])
        if not word:
            return None
        target = word.lstrip("$#\\")
        selected = _selected_overload_at(
            self._node_view(uri, typed=True), target, params["position"]
        )
        declaration = _definition_at_position(
            source, self._node_view(uri), target, params["position"]
        )
        if declaration is not None:
            return _definition_location(uri, source, declaration)
        declared = {
            (node.name, node.location)
            for node in _ast_nodes(self._node_view(uri))
            if isinstance(node, DefineNode)
        }
        local_typed = [
            typed
            for typed in _typed_nodes(self._node_view(uri, typed=True))
            if isinstance(typed.node, DefineNode)
            and (typed.node.name, typed.node.location) in declared
        ]
        local = _definition_location_for_overload(
            uri, source, local_typed, target, selected
        )
        if local is not None:
            return local
        current_file = _uri_path(uri)
        loader = self.module_loader
        for node in self.programs.get(uri, []):
            if not isinstance(node, ImportNode):
                continue
            for spec in node.specs:
                for module_path, imported_name in _import_definition_candidates(
                    spec, target
                ):
                    try:
                        source_file = loader.resolve(
                            module_path, current_file=current_file
                        )
                        imported_source, imported_program, imported_typed = (
                            self._analysed_source(source_file)
                        )
                    except (
                        ModuleLoadError,
                        OSError,
                        LexError,
                        ParseError,
                        ParseErrors,
                    ):
                        continue
                    location = _definition_location_for_overload(
                        source_file.as_uri(),
                        imported_source,
                        imported_typed,
                        imported_name,
                        selected,
                    )
                    if location is not None:
                        return location
        return None


def comment_ranges(source: str) -> tuple[tuple[int, int], ...]:
    """Index line and nested block comments while respecting quoted strings."""
    ranges = []
    index = 0
    in_string = False
    escaped = False
    while index < len(source):
        char = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
        elif char == '"':
            in_string = True
            index += 1
        elif source.startswith("#?", index):
            end = source.find("\n", index)
            end = len(source) + 1 if end < 0 else end
            ranges.append((index, end))
            index = end
        elif source.startswith("#/", index):
            start, depth = index, 1
            index += 2
            while index < len(source) and depth:
                if source.startswith("#/", index):
                    depth += 1
                    index += 2
                elif source.startswith("/#", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            ranges.append((start, index + int(depth > 0)))
        else:
            index += 1
    return tuple(ranges)


def _overload_signature(name: str, overload: T.Overload) -> str:
    """Render an overload as useful Valiance source rather than ``Overload``."""
    params = []
    for index, typ in enumerate(overload.params):
        label = (
            str(overload.param_names[index])
            if index < len(overload.param_names)
            else f"_{index + 1}"
        )
        params.append(f"{label}: {T.show(typ)}")
    returns = ", ".join(T.show(item) for item in overload.returns)
    tags = T.show(T.Fn((), (), overload.element_tags)) if overload.element_tags else ""
    tag_clause = tags[tags.index("<") :] if tags else ""
    return f"{name}({', '.join(params)}){tag_clause} -> {returns}".rstrip()


class _NodeIndex(list):
    """A flattened syntax view that shared query helpers can reuse directly."""

    def __init__(self, nodes, kind):
        """Associate an immutable installed product's view with its node kind."""
        super().__init__(nodes)
        self.kind = kind


def _dataclass_nodes(value: Any, node_type: type[Any]) -> list[Any]:
    """Walk syntax products without traversing type metadata or captured branches."""
    if isinstance(value, _NodeIndex) and value.kind is node_type:
        return value
    found: list[Any] = []
    seen: set[int] = set()

    def visit(item: Any) -> None:
        """Visit dataclasses and collection children exactly once."""
        if id(item) in seen or node_type is TypedNode and isinstance(item, ASTNode):
            return
        if isinstance(item, node_type):
            seen.add(id(item))
            found.append(item)
        if isinstance(item, (tuple, list)):
            for child in item:
                visit(child)
        elif is_dataclass(item) and type(item).__module__.startswith("valiance.asts."):
            seen.add(id(item))
            for field in fields(item):
                visit(getattr(item, field.name))

    visit(value)
    return found


def _ast_nodes(value: Any) -> list[ASTNode]:
    """Flatten raw AST nodes for nested declaration lookup."""
    return _dataclass_nodes(value, ASTNode)


def _typed_nodes(value: Any) -> list[TypedNode]:
    """Flatten typed nodes for source-sensitive editor features."""
    return _dataclass_nodes(value, TypedNode)


def _source_offset_at(source: str, position: dict[str, int]) -> int:
    """Return a source offset for an LSP position."""
    return _offset(source, position)


def _node_offset(node: ASTNode) -> int:
    """Return a sortable source offset for an AST node."""
    return getattr(getattr(node, "location", None), "offset", -1)


def _variable_type_at(
    program: list[TypedNode],
    name: str,
    position: dict[str, int],
    *,
    source: str,
) -> Any | None:
    """Return the analyser type for a variable, including string interpolation."""
    line = position.get("line", 0) + 1
    character = position.get("character", 0) + 1
    direct: list[TypedNode] = []
    assignments: list[TypedNode] = []
    cursor_offset = _source_offset_at(source, position)
    for typed in _typed_nodes(program):
        node = typed.node
        if not isinstance(node, (GetVariableNode, SetVariableNode)):
            continue
        if str(node.name) != name or typed.typ is None:
            continue
        if isinstance(node, SetVariableNode) and _node_offset(node) <= cursor_offset:
            assignments.append(typed)
        if node.location is not None and node.location.line == line:
            direct.append(typed)
    if direct:
        return min(
            direct,
            key=lambda item: abs(item.node.location.column - character),
        ).typ
    if assignments:
        return max(assignments, key=lambda item: _node_offset(item.node)).typ
    return None


def _selected_overload_at(
    program: list[TypedNode], name: str, position: dict[str, int]
) -> T.Overload | None:
    """Return the selected overload for the element under the cursor."""
    line = position.get("line", 0) + 1
    character = position.get("character", 0) + 1
    candidates: list[TypedElementNode] = []
    for typed in _typed_nodes(program):
        node = typed.node
        if not isinstance(typed, TypedElementNode) or not isinstance(node, ElementNode):
            continue
        if str(node.name) != name or node.location is None or typed.overload is None:
            continue
        if node.location.line == line:
            candidates.append(typed)
    if not candidates:
        return None
    chosen = min(
        candidates,
        key=lambda item: abs(item.node.location.column - character),
    )
    return chosen.overload.overload


def _definition_name_range(source: str, node: DefineNode) -> dict[str, Any]:
    """Return the exact name range rather than the preceding define keyword."""
    name = str(node.name).removeprefix("\\")
    line_index = max(node.location.line - 1, 0)
    lines = source.splitlines()
    line = lines[line_index] if line_index < len(lines) else ""
    start_at = max(node.location.column - 1, 0)
    match = re.search(rf"\b{re.escape(name)}\b", line[start_at:])
    column = start_at + (match.start() if match else 0)
    return {
        "start": {"line": line_index, "character": column},
        "end": {"line": line_index, "character": column + len(name)},
    }


def _definition_location(uri: str, source: str, node: DefineNode) -> dict[str, Any]:
    """Return an LSP location selecting a definition's element name."""
    return {"uri": uri, "range": _definition_name_range(source, node)}


def _definition_at_position(
    source: str,
    program: list[ASTNode],
    name: str,
    position: dict[str, int],
) -> DefineNode | None:
    """Return the exact declaration whose name is under the cursor."""
    for node in _ast_nodes(program):
        if not isinstance(node, DefineNode) or node.location is None:
            continue
        if str(node.name).removeprefix("\\") != name:
            continue
        rng = _definition_name_range(source, node)
        if _position_in_range(position, rng):
            return node
    return None


def _definition_at_lsp_start(
    source: str, program: list[ASTNode], start: dict[str, int]
) -> DefineNode | None:
    """Return the declaration selected by a definition response range."""
    for node in _ast_nodes(program):
        if isinstance(node, DefineNode) and node.location is not None:
            if _definition_name_range(source, node)["start"] == start:
                return node
    return None


def _position_in_range(position: dict[str, int], rng: dict[str, Any]) -> bool:
    """Return whether an LSP position lies inside a single-line range."""
    return (
        position.get("line", 0) == rng["start"]["line"]
        and rng["start"]["character"]
        <= position.get("character", 0)
        <= rng["end"]["character"]
    )


def _definition_overloads(
    typed_program: list[TypedNode], declaration: DefineNode
) -> tuple[T.Overload, ...]:
    """Return only overloads contributed by one exact define declaration."""
    for typed in _typed_nodes(typed_program):
        same_declaration = (
            isinstance(typed, TypedFunctionNode)
            and isinstance(typed.node, DefineNode)
            and (
                typed.node is declaration
                or (
                    typed.node.location == declaration.location
                    and typed.node.name == declaration.name
                )
            )
        )
        if same_declaration:
            return tuple(
                item.overload
                for item in typed.overloads
                if isinstance(item.overload, T.Overload)
            )
    return ()


def _definition_location_for_overload(
    uri: str,
    source: str,
    typed_program: list[TypedNode],
    name: str,
    selected: T.Overload | None,
) -> dict[str, Any] | None:
    """Locate the declaration that contributes a selected overload."""
    fallback: DefineNode | None = None
    matched: DefineNode | None = None
    for typed in typed_program:
        if (
            not isinstance(typed, TypedFunctionNode)
            or typed.node.location is None
            or not isinstance(typed.node, DefineNode)
        ):
            continue
        node = typed.node
        if str(node.name).removeprefix("\\") != name:
            continue
        fallback = fallback or node
        overloads = _definition_overloads(typed_program, node)
        if selected is not None:
            if any(item is selected for item in overloads):
                return _definition_location(uri, source, node)
            if matched is None and any(
                _same_overload(item, selected) for item in overloads
            ):
                matched = node
    declaration = matched or fallback
    return (
        _definition_location(uri, source, declaration)
        if declaration is not None
        else None
    )


def _same_overload(left: T.Overload, right: T.Overload) -> bool:
    """Compare overload signatures while ignoring module-specific runtime metadata."""
    return (
        left.params == right.params
        and left.returns == right.returns
        and left.param_names == right.param_names
    )


def _signature_key(signature: str) -> str:
    """Normalize a rendered definition signature for overload matching."""
    signature = re.sub(r"^(?:public |private |multi )+", "", signature)
    signature = re.sub(r"^define(?:\[[^]]*\])?\s+", "", signature)
    # Inferred effects can appear on the analysed overload even when the source
    # declaration omitted an explicit tag contract. They do not distinguish
    # which source docstring belongs to an otherwise identical signature.
    signature = re.sub(r"\)\s*<[^>]*>\s*->", ") ->", signature)
    signature = re.sub(r"\s+", " ", signature).strip()
    if signature.endswith(")"):
        signature += " ->"
    return signature


def _match_overload_docs(
    overloads: tuple[T.Overload, ...], documented: tuple[tuple[str, str], ...]
) -> tuple[str, ...]:
    """Align each visible overload with the docstring of its source declaration."""
    indexed = [(_signature_key(signature), doc) for signature, doc in documented]
    result: list[str] = []
    for overload in overloads:
        expected = _signature_key(_overload_signature("__name__", overload)).replace(
            "__name__", "", 1
        )
        matched = ""
        for signature, doc in indexed:
            suffix = signature[signature.find("(") :] if "(" in signature else signature
            if suffix == expected:
                matched = doc
                break
        result.append(matched)
    return tuple(result)


def _render_single_overload_hover(
    name: str, overload: T.Overload, documentation: str
) -> str:
    """Render only the overload selected at a statically resolved call site."""
    value = f"```valiance\n{_overload_signature(name, overload)}\n```"
    if documentation:
        value += f"\n\n{documentation}"
    return value


def _render_overload_hover(
    name: str, overloads: tuple[T.Overload, ...], docs: tuple[str, ...]
) -> str:
    """Render at most five unresolved overloads as clearly separated sections."""
    limit = 5
    visible = overloads[:limit]
    sections: list[str] = []
    for index, overload in enumerate(visible):
        signature = f"```valiance\n{_overload_signature(name, overload)}\n```"
        parts = [signature]
        documentation = docs[index] if index < len(docs) else ""
        if documentation:
            parts.append(documentation)
        sections.append("\n\n".join(parts))
    hidden = len(overloads) - limit
    if hidden > 0:
        sections.append(f"*…and {hidden} more overload{'s' if hidden != 1 else ''}.*")
    return "\n\n---\n\n".join(sections)


def _import_definition_candidates(
    spec: Any, local_name: str
) -> tuple[tuple[Any, str], ...]:
    """Return full-module and implicit-final-component import interpretations."""
    candidates: list[tuple[Any, str]] = []
    if not spec.components and "." in local_name:
        namespace, member = local_name.split(".", 1)
        visible_namespace = str(spec.alias or spec.path.parts[-1]).removeprefix("\\")
        if namespace == visible_namespace:
            candidates.append((spec.path, member))
            return tuple(candidates)
    if spec.components:
        for component in spec.components:
            visible = str(component.alias or component.name).removeprefix("\\")
            if visible == local_name:
                candidates.append((spec.path, str(component.name).removeprefix("\\")))
        return tuple(candidates)

    # Mirror analyser precedence: first interpret the final segment as a
    # component of the parent module, then fall back to a nested module.
    if len(spec.path.parts) >= 2:
        visible = str(spec.alias or spec.path.parts[-1]).removeprefix("\\")
        if visible == local_name:
            from valiance.asts import ImportPath

            candidates.append(
                (
                    ImportPath(spec.path.parts[:-1], spec.path.root),
                    spec.path.parts[-1],
                )
            )
    candidates.append((spec.path, local_name))
    return tuple(candidates)


def _builtin_for_overloads(
    name: str,
    overloads: tuple[T.Overload, ...],
    selected: T.Overload | None,
) -> BuiltinElement | None:
    """Return the built-in represented by the visible or selected overloads."""
    for element in BUILTIN_ELEMENTS:
        names = {element.name.text}
        if element.canonical_name is not None:
            names.add(element.canonical_name.text)
        if name not in names:
            continue
        if selected is not None and any(
            _same_overload(item, selected) for item in element.overloads
        ):
            return element
        if overloads and all(
            any(_same_overload(item, candidate) for candidate in element.overloads)
            for item in overloads
        ):
            return element
    return None


def _element_documentation_markdown(documentation: ElementDocumentation) -> str:
    """Render built-in metadata using the same shape as source docstrings."""
    sections = [documentation.summary]
    sections.extend(documentation.description)
    fields = [
        f"- **Parameter `{item.name}`:** {item.description}"
        for item in documentation.parameters
    ]
    if documentation.returns is not None:
        fields.append(f"- **Returns:** {documentation.returns}")
    if fields:
        sections.append("\n".join(fields))
    if documentation.notes:
        sections.append(
            "\n".join(f"- **Note:** {item}" for item in documentation.notes)
        )
    for example in documentation.examples:
        example_sections = ["**Example**", f"```vlnc\n{example.source}\n```"]
        if example.result is not None:
            example_sections.extend(("Result:", f"```text\n{example.result}\n```"))
        if example.description:
            example_sections.append(example.description)
        sections.append("\n\n".join(example_sections))
    return "\n\n".join(item for item in sections if item)


def _docstring_markdown(docstring: Any) -> str:
    """Render parsed Valiance documentation as hover-friendly Markdown."""
    sections: list[str] = []
    if docstring.description:
        sections.append("\n".join(docstring.description))
    fields: list[str] = []
    fields.extend(
        f"- **Parameter `{item.name}`:** {item.description}"
        for item in docstring.params
    )
    fields.extend(
        f"- **Type parameter `{item.name}`:** {item.description}"
        for item in docstring.type_params
    )
    if docstring.returns is not None:
        fields.append(f"- **Returns:** {docstring.returns}")
    fields.extend(
        f"- {item}" for item in docstring.extra_fields
        if not item.startswith("@example ")
    )
    if fields:
        sections.append("\n".join(fields))
    sections.extend(
        "**Example**\n\n```vlnc\n" + item.removeprefix("@example ") + "\n```"
        for item in docstring.extra_fields if item.startswith("@example ")
    )
    return "\n\n".join(sections)


def _uri_path(uri: str) -> Path | None:
    """Convert a file URI to a native path, including Windows drive URIs."""
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    path = unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:/", path):
        path = path[1:]
    if parsed.netloc and parsed.netloc not in {"", "localhost"}:
        path = f"//{parsed.netloc}{path}"
    return Path(path)


def _offset(source: str, position: dict[str, int]) -> int:
    """Convert an LSP zero-based position to a source offset."""
    lines = source.splitlines(keepends=True)
    line = min(position.get("line", 0), len(lines))
    return sum(len(item) for item in lines[:line]) + position.get("character", 0)


def _word_at(source: str, position: dict[str, int]) -> str | None:
    """Return the language token containing an LSP position."""
    offset = _offset(source, position)
    for match in _WORD.finditer(source):
        if match.start() <= offset <= match.end():
            return match.group(0)
    return None


def _word_range(source: str, position: dict[str, int]) -> dict[str, Any] | None:
    """Return the exact LSP range of the token under a position."""
    offset = _offset(source, position)
    line = position.get("line", 0)
    line_start = source.rfind("\n", 0, offset) + 1
    for match in _WORD.finditer(source):
        if match.start() <= offset <= match.end():
            return {
                "start": {"line": line, "character": match.start() - line_start},
                "end": {"line": line, "character": match.end() - line_start},
            }
    return None
