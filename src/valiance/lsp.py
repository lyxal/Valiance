"""Dependency-free Language Server Protocol support for Valiance."""

from __future__ import annotations

import json
import re
from dataclasses import fields, is_dataclass
import sys
from pathlib import Path
from typing import Any, BinaryIO
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
from valiance.analysis.diagnostics import DiagnosticError, from_message
from valiance.modules_system.modules import ModuleLoadError, ModuleLoader
from valiance.elements.builtins import BUILTIN_ELEMENTS, BuiltinElement
from valiance.elements.documentation import ElementDocumentation
from valiance.parsing import LexError, ParseError, ParseErrors, parse
from valiance.repl import completion_prefix, default_completion_items
from valiance.source_tools import extract_documented_defines
from valiance.vtypes.symbols import Symbol

_WORD = re.compile(
    r"(?:\*::|[$#\\])?[A-Za-z_][A-Za-z0-9_:]*|[+\-*%!?=/< >~&^]+".replace(" ", "")
)
_LOCATION = re.compile(r"^(\d+):(\d+):\s*(.*)$", re.DOTALL)


class LanguageServer:
    """Serve Valiance language features using JSON-RPC over stdio."""

    def __init__(self, reader: BinaryIO, writer: BinaryIO) -> None:
        """Initialize a language server using binary input and output streams."""
        self.reader = reader
        self.writer = writer
        self.documents: dict[str, str] = {}
        self.analysers: dict[str, Analyser] = {}
        self.programs: dict[str, list[ASTNode]] = {}
        self.typed_programs: dict[str, list[TypedNode]] = {}
        self.initialized = False
        self.shutdown_requested = False
        self.exit_code = 0

    def run(self) -> int:
        """Dispatch protocol messages until the client sends ``exit``."""
        while message := self._read_message():
            if self._dispatch(message):
                break
        return self.exit_code

    def _dispatch(self, message: dict[str, Any]) -> bool:
        """Dispatch one JSON-RPC request or notification."""
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        request = "id" in message
        if method == "exit":
            self.exit_code = 0 if self.shutdown_requested else 1
            return True
        if not self.initialized and method != "initialize":
            if request:
                self._error(request_id, -32002, "server is not initialized")
            return False
        try:
            result = self._handle(method, params)
        except Exception as exc:
            if request:
                self._error(request_id, -32603, str(exc))
            return False
        if request:
            if result is NotImplemented:
                self._error(request_id, -32601, f"method not found: {method}")
            else:
                self._respond(request_id, result)
        return False

    def _handle(self, method: str | None, params: dict[str, Any]) -> Any:
        """Handle one supported LSP method."""
        if method == "initialize":
            self.initialized = True
            return {
                "capabilities": {
                    "textDocumentSync": {"openClose": True, "change": 1},
                    "completionProvider": {"triggerCharacters": ["$", "#", "\\"]},
                    "hoverProvider": True,
                    "definitionProvider": True,
                    "documentSymbolProvider": True,
                    "documentFormattingProvider": True,
                },
                "serverInfo": {"name": "valiance-lsp", "version": "0.1.1"},
            }
        if method == "initialized":
            return None
        if method == "shutdown":
            self.shutdown_requested = True
            return None
        if method in {"$/cancelRequest", "$/setTrace"}:
            return None
        if method == "textDocument/didOpen":
            document = params["textDocument"]
            self.documents[document["uri"]] = document["text"]
            self._analyse(document["uri"])
            return None
        if method == "textDocument/didChange":
            uri = params["textDocument"]["uri"]
            changes = params.get("contentChanges", [])
            if changes:
                self.documents[uri] = changes[-1]["text"]
            self._analyse(uri)
            return None
        if method == "textDocument/didClose":
            uri = params["textDocument"]["uri"]
            self.documents.pop(uri, None)
            self.analysers.pop(uri, None)
            self.programs.pop(uri, None)
            self.typed_programs.pop(uri, None)
            self._notify(
                "textDocument/publishDiagnostics", {"uri": uri, "diagnostics": []}
            )
            return None
        if method == "textDocument/completion":
            return self._completion(params)
        if method == "textDocument/hover":
            return self._hover(params)
        if method == "textDocument/definition":
            return self._definition(params)
        if method == "textDocument/documentSymbol":
            return self._document_symbols(params)
        if method == "textDocument/formatting":
            from valiance.source_tools import format_source

            uri = params["textDocument"]["uri"]
            source = self.documents.get(uri, "")
            formatted = format_source(source, indent_width=2)
            return (
                []
                if formatted == source
                else [{"range": _whole_range(source), "newText": formatted}]
            )
        return NotImplemented

    def _analyse(self, uri: str) -> None:
        """Analyse an open document and publish diagnostics at source locations."""
        source = self.documents.get(uri, "")
        diagnostics: list[dict[str, Any]] = []
        try:
            program = parse(source)
            analyser = Analyser(source_file=_uri_path(uri))
            typed_program = analyser.analyse(program)
            self.programs[uri] = program
            self.typed_programs[uri] = typed_program
            self.analysers[uri] = analyser
            diagnostics.extend(
                _message_diagnostic(item, 1) for item in analyser.diagnostics
            )
            diagnostics.extend(
                _message_diagnostic(item, 2) for item in analyser.warnings
            )
            diagnostics.extend(
                _lint_diagnostic(item) for item in analyser.lint_findings
            )
        except ParseErrors as exc:
            diagnostics.extend(_exception_diagnostic(item) for item in exc.errors)
            self.programs.pop(uri, None)
            self.typed_programs.pop(uri, None)
            self.analysers.pop(uri, None)
        except (LexError, ParseError, DiagnosticError) as exc:
            diagnostics.append(_exception_diagnostic(exc))
            self.programs.pop(uri, None)
            self.typed_programs.pop(uri, None)
            self.analysers.pop(uri, None)
        self._notify(
            "textDocument/publishDiagnostics", {"uri": uri, "diagnostics": diagnostics}
        )

    def _completion(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Return keywords, types, declarations, tags, and elements."""
        uri = params["textDocument"]["uri"]
        source = self.documents.get(uri, "")
        prefix = completion_prefix(source[: _offset(source, params["position"])])
        items = {
            item.text: item.meta
            for item in default_completion_items()
            if not item.text.startswith(":")
        }
        analyser = self.analysers.get(uri)
        if analyser:
            env = analyser.env
            while env is not None:
                for name in env.overloads:
                    overloads = env.overloads_for(name)
                    items.setdefault(
                        name.text,
                        "\n".join(
                            _overload_signature(name.text, item) for item in overloads
                        ),
                    )
                for collection, kind in (
                    (env.objects, "object"),
                    (env.traits, "trait"),
                    (env.variants, "variant"),
                    (env.enums, "enum"),
                ):
                    for name in collection:
                        items.setdefault(name.text, kind)
                for name in env.data_tags:
                    items.setdefault(f"#{name.text}", "data tag")
                env = env.parent
        return [
            {"label": text, "detail": detail, "kind": 3, "sortText": text}
            for text, detail in sorted(items.items())
            if not prefix or text.startswith(prefix)
        ]

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
                self.typed_programs.get(uri, []),
                lookup_name,
                position,
                source=source,
            )
            if variable_type is None:
                return None
            return {
                "contents": {
                    "kind": "markdown",
                    "value": f"```valiance\n${display_name}: {T.show(variable_type)}\n```",
                },
                "range": _word_range(source, position),
            }

        declaration = _definition_at_position(
            source, self.programs.get(uri, []), lookup_name, position
        )
        if declaration is not None:
            overloads = _definition_overloads(
                self.typed_programs.get(uri, []), declaration
            )
            documentation = _definition_documentation_at_line(
                source, lookup_name, declaration.location.line
            )
            value = _render_overload_hover(display_name, overloads, (documentation,))
            return {
                "contents": {"kind": "markdown", "value": value},
                "range": _word_range(source, position),
            }

        selected = _selected_overload_at(
            self.typed_programs.get(uri, []), lookup_name, position
        )
        overloads = analyser.env.overloads_for(Symbol(lookup_name))
        if not overloads and selected is None:
            return None
        docs = self._overload_documentation(
            uri, lookup_name, position, overloads, selected
        )
        builtin = _builtin_for_overloads(lookup_name, overloads, selected)
        builtin_documentation = (
            _element_documentation_markdown(builtin.documentation)
            if builtin is not None and builtin.documentation is not None
            else ""
        )
        if selected is not None:
            selected_doc = self._selected_overload_documentation(
                uri, lookup_name, position, selected
            )
            if not selected_doc:
                selected_doc = next(
                    (
                        docs[index]
                        for index, overload in enumerate(overloads)
                        if index < len(docs) and _same_overload(overload, selected)
                    ),
                    "",
                )
            if not selected_doc:
                selected_doc = builtin_documentation
            value = _render_single_overload_hover(
                display_name, selected, selected_doc
            )
        else:
            if builtin_documentation and not any(docs):
                docs = tuple(builtin_documentation for _ in overloads)
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
        local = _documented_overloads(source, name)
        if local:
            return _match_overload_docs(overloads, local)

        target = self._definition_source(uri, name, position, selected)
        if target is None:
            return tuple("" for _ in overloads)
        _, target_source, target_name = target
        documented = _documented_overloads(target_source, target_name)
        return _match_overload_docs(overloads, documented)

    def _selected_overload_documentation(
        self,
        uri: str,
        name: str,
        position: dict[str, int],
        selected: T.Overload,
    ) -> str:
        """Load the exact docstring belonging to the selected call overload."""
        # Match source documentation first. This covers nested local defines,
        # which are intentionally absent from the module-level environment.
        local_documentation = _match_overload_docs(
            (selected,), _documented_overloads(self.documents.get(uri, ""), name)
        )
        if local_documentation and local_documentation[0]:
            return local_documentation[0]

        # Prefer signature-matched source documentation for imports. This path
        # does not depend on the imported module analysing successfully, and it
        # avoids losing the docstring when definition lookup falls back to a
        # different same-named declaration.
        imported_documentation = self._documentation_from_import_sources(
            uri, name, selected
        )
        if imported_documentation:
            return imported_documentation
        location = self._definition(
            {"textDocument": {"uri": uri}, "position": position}
        )
        if location is None:
            return ""
        source_uri = location["uri"]
        source = self.documents.get(source_uri)
        if source is None:
            path = _uri_path(source_uri)
            if path is None:
                return ""
            try:
                source = path.read_text(encoding="utf-8")
            except OSError:
                return ""
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
        documentation = _definition_documentation_at_line(
            source, declared_name, declaration.location.line
        )
        return documentation or self._documentation_from_import_sources(
            uri, name, selected
        )

    def _documentation_from_import_sources(
        self, uri: str, name: str, selected: T.Overload
    ) -> str:
        """Match selected-overload docs directly from imported source files."""
        current_file = _uri_path(uri)
        if current_file is None:
            return ""
        loader = ModuleLoader()
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
                            source = source_file.read_text(encoding="utf-8")
                    except (ModuleLoadError, OSError):
                        continue
                    documented = _documented_overloads(source, imported_name)
                    matched = _match_overload_docs((selected,), documented)
                    if matched and matched[0]:
                        return matched[0]
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
                source = path.read_text(encoding="utf-8")
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
            self.typed_programs.get(uri, []), target, params["position"]
        )
        declaration = _definition_at_position(
            source, self.programs.get(uri, []), target, params["position"]
        )
        if declaration is not None:
            return _definition_location(uri, source, declaration)
        current_file = _uri_path(uri)
        if current_file is None:
            return None
        loader = ModuleLoader()
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
                        imported_source = source_file.read_text(encoding="utf-8")
                        imported_program = parse(imported_source)
                    except (
                        ModuleLoadError,
                        OSError,
                        LexError,
                        ParseError,
                        ParseErrors,
                    ):
                        continue
                    imported_analyser = Analyser(source_file=source_file)
                    imported_typed = imported_analyser.analyse(imported_program)
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

    def _document_symbols(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Return top-level definitions as document symbols."""
        uri = params["textDocument"]["uri"]
        result = []
        for node in self.programs.get(uri, []):
            if isinstance(node, DefineNode) and node.location:
                rng = _location_range(
                    node.location.line, node.location.column, len(str(node.name))
                )
                result.append(
                    {
                        "name": str(node.name),
                        "kind": 12,
                        "range": rng,
                        "selectionRange": rng,
                    }
                )
        return result

    def _read_message(self) -> dict[str, Any] | None:
        """Read one Content-Length framed JSON-RPC message."""
        headers: dict[str, str] = {}
        while True:
            line = self.reader.readline()
            if not line:
                return None
            if line in {b"\r\n", b"\n"}:
                break
            name, _, value = line.decode("ascii").partition(":")
            headers[name.lower()] = value.strip()
        length = int(headers.get("content-length", "0"))
        return (
            json.loads(self.reader.read(length).decode("utf-8")) if length > 0 else None
        )

    def _write(self, payload: dict[str, Any]) -> None:
        """Write one Content-Length framed JSON-RPC message."""
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.writer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        self.writer.flush()

    def _respond(self, request_id: Any, result: Any) -> None:
        """Write a successful JSON-RPC response."""
        self._write({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _error(self, request_id: Any, code: int, message: str) -> None:
        """Write a JSON-RPC error response."""
        self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            }
        )

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        """Write a JSON-RPC notification."""
        self._write({"jsonrpc": "2.0", "method": method, "params": params})


def run_language_server(
    reader: BinaryIO | None = None, writer: BinaryIO | None = None
) -> int:
    """Run the Valiance language server over binary standard input/output."""
    return LanguageServer(reader or sys.stdin.buffer, writer or sys.stdout.buffer).run()


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


def _ast_nodes(value: Any) -> list[ASTNode]:
    """Flatten raw AST dataclass fields for nested declaration lookup."""
    found: list[ASTNode] = []
    seen: set[int] = set()

    def visit(item: Any) -> None:
        if id(item) in seen:
            return
        if isinstance(item, ASTNode):
            seen.add(id(item))
            found.append(item)
        if isinstance(item, (tuple, list)):
            for child in item:
                visit(child)
        elif is_dataclass(item):
            seen.add(id(item))
            for field in fields(item):
                visit(getattr(item, field.name))

    visit(value)
    return found


def _typed_nodes(value: Any) -> list[TypedNode]:
    """Flatten typed dataclass fields for source-sensitive editor features."""
    found: list[TypedNode] = []
    seen: set[int] = set()

    def visit(item: Any) -> None:
        """Visit typed dataclasses and collection children exactly once."""
        if id(item) in seen:
            return
        if isinstance(item, TypedNode):
            seen.add(id(item))
            found.append(item)
        if isinstance(item, (tuple, list)):
            for child in item:
                visit(child)
        elif is_dataclass(item):
            seen.add(id(item))
            for field in fields(item):
                visit(getattr(item, field.name))

    visit(value)
    return found


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
    """Return the overload selected for the element occurrence under the cursor."""
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
    for typed in typed_program:
        if not isinstance(typed, TypedFunctionNode) or not isinstance(typed.node, DefineNode):
            continue
        node = typed.node
        if str(node.name).removeprefix("\\") != name:
            continue
        fallback = fallback or node
        overloads = _definition_overloads(typed_program, node)
        if selected is not None and any(_same_overload(item, selected) for item in overloads):
            return _definition_location(uri, source, node)
    return _definition_location(uri, source, fallback) if fallback is not None else None


def _same_overload(left: T.Overload, right: T.Overload) -> bool:
    """Compare overload signatures while ignoring module-specific runtime metadata."""
    return (
        left.params == right.params
        and left.returns == right.returns
        and left.param_names == right.param_names
    )


def _documented_overloads(source: str, name: str) -> tuple[tuple[str, str], ...]:
    """Return source signatures paired with their individual docstrings."""
    return tuple(
        (item.signature, _docstring_markdown(item.docstring))
        for item in extract_documented_defines(source)
        if item.name == name
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


def _definition_documentation_at_line(source: str, name: str, line: int) -> str:
    """Return documentation for one exact same-named declaration line."""
    return next(
        (
            _docstring_markdown(item.docstring)
            for item in extract_documented_defines(source)
            if item.name == name and item.line == line
        ),
        "",
    )


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
    if spec.components:
        for component in spec.components:
            visible = str(component.alias or component.name).removeprefix("\\")
            if visible == local_name:
                candidates.append(
                    (spec.path, str(component.name).removeprefix("\\"))
                )
        return tuple(candidates)

    # First preserve a true namespace import. If the complete path is not a
    # source module, mirror the analyser's implicit final-component fallback.
    candidates.append((spec.path, local_name))
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
        sections.append("\n".join(f"- **Note:** {item}" for item in documentation.notes))
    return "\n\n".join(item for item in sections if item)


def _definition_documentation(source: str, name: str) -> str:
    """Render the ``#??`` documentation attached to a named definition."""
    references = (
        item for item in extract_documented_defines(source) if item.name == name
    )
    rendered = tuple(_docstring_markdown(item.docstring) for item in references)
    return "\n\n---\n\n".join(item for item in rendered if item)


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
    fields.extend(f"- {item}" for item in docstring.extra_fields)
    if fields:
        sections.append("\n".join(fields))
    return "\n\n".join(sections)


def _uri_path(uri: str) -> Path | None:
    """Convert a file URI to a local path when possible."""
    parsed = urlparse(uri)
    return Path(unquote(parsed.path)) if parsed.scheme == "file" else None


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


def _whole_range(source: str) -> dict[str, Any]:
    """Return an LSP range spanning a complete document."""
    lines = source.splitlines()
    return {
        "start": {"line": 0, "character": 0},
        "end": {
            "line": max(len(lines) - 1, 0),
            "character": len(lines[-1]) if lines else 0,
        },
    }


def _location_range(line: int, column: int, length: int = 1) -> dict[str, Any]:
    """Convert one-based compiler coordinates to an LSP range."""
    start = {"line": max(line - 1, 0), "character": max(column - 1, 0)}
    return {
        "start": start,
        "end": {
            "line": start["line"],
            "character": start["character"] + max(length, 1),
        },
    }


def _exception_diagnostic(exc: DiagnosticError) -> dict[str, Any]:
    """Convert a parser or lexer exception to an LSP diagnostic."""
    return {
        "range": _location_range(exc.line or 1, exc.column or 1),
        "severity": 1,
        "source": "valiance",
        "message": str(exc),
    }


def _message_diagnostic(message: str, severity: int) -> dict[str, Any]:
    """Convert the compiler's ``line:column`` message to an LSP diagnostic."""
    match = _LOCATION.match(message)
    if match:
        line, column, text = match.groups()
        rng = _location_range(int(line), int(column))
    else:
        text, rng = message, _location_range(1, 1)
    structured = from_message("Type error", message)
    text = structured.message
    if "\ndid you mean " in message and structured.help is not None:
        text += f"\nhelp: {structured.help}"
    return {"range": rng, "severity": severity, "source": "valiance", "message": text}


def _lint_diagnostic(finding: Any) -> dict[str, Any]:
    """Convert a structured analyser lint to an LSP diagnostic."""
    location = getattr(finding, "location", None)
    return {
        "range": _location_range(
            getattr(location, "line", 1), getattr(location, "column", 1)
        ),
        "severity": 3,
        "source": "valiance",
        "code": getattr(finding, "code", None),
        "message": getattr(finding, "message", str(finding)),
    }
