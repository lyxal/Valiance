"""Dependency-free Language Server Protocol support for Valiance."""

from __future__ import annotations

import json
import re
import sys
from importlib import resources
from typing import Any, BinaryIO

import valiance.vtypes as T
from valiance.analysis import Analyser
from valiance.analysis.diagnostics import DiagnosticError, from_message
from valiance.asts import (
    ASTNode,
    DefineNode,
    GetVariableNode,
    SetVariableNode,
)
from valiance.asts.nodes import TypedFunctionNode, TypedNode
from valiance.elements.builtins import BUILTIN_ELEMENTS
from valiance.incremental import CompilationDatabase
from valiance.parsing import LexError, ParseError, ParseErrors, parse
from valiance.repl import completion_prefix, default_completion_items
from valiance.source_queries import SourceQueries, comment_ranges
from valiance.source_queries import _ast_nodes as _ast_nodes
from valiance.source_queries import _node_offset as _node_offset
from valiance.source_queries import _offset as _offset
from valiance.source_queries import _overload_signature as _overload_signature
from valiance.source_queries import _render_overload_hover as _render_overload_hover
from valiance.source_queries import _source_offset_at as _source_offset_at
from valiance.source_queries import _typed_nodes as _typed_nodes
from valiance.source_queries import _uri_path as _uri_path

_WORD = re.compile(
    r"(?:\*::|[$#\\])?[A-Za-z_][A-Za-z0-9_]*(?:(?:::|\.)[A-Za-z_][A-Za-z0-9_]*)*"
    r"|[+\-*%!?=/< >~&^]+".replace(" ", "")
)
_LOCATION = re.compile(r"^(\d+):(\d+):\s*(.*)$", re.DOTALL)


class LanguageServer(SourceQueries):
    """Serve Valiance language features using JSON-RPC over stdio."""

    def __init__(self, reader: BinaryIO, writer: BinaryIO) -> None:
        """Initialize a language server using binary input and output streams."""
        self.reader = reader
        self.writer = writer
        self.documents: dict[str, str] = {}
        self.analysers: dict[str, Analyser] = {}
        self.programs: dict[str, list[ASTNode]] = {}
        self.typed_programs: dict[str, list[TypedNode]] = {}
        self.compilation_database = CompilationDatabase()
        self.module_loader = self.compilation_database.module_loader
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
                    "completionProvider": {
                        "triggerCharacters": ["$", "#", "\\", "{", "."]
                    },
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
            self._refresh_workspace(document["uri"])
            return None
        if method == "textDocument/didChange":
            uri = params["textDocument"]["uri"]
            changes = params.get("contentChanges", [])
            if changes:
                self.documents[uri] = changes[-1]["text"]
            self._refresh_workspace(uri)
            return None
        if method == "textDocument/didClose":
            uri = params["textDocument"]["uri"]
            self.documents.pop(uri, None)
            if (path := _uri_path(uri)) is not None:
                self.compilation_database.close_document(path)
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

    def _refresh_workspace(self, changed_uri: str) -> None:
        """Reanalyse open documents through the unified compilation database."""
        for uri, source in self.documents.items():
            path = _uri_path(uri)
            if path is None:
                continue
            current = self.compilation_database._overlays.get(path.resolve())
            if current is None:
                self.compilation_database.open_document(path, source)
            elif current != source:
                self.compilation_database.replace_document(path, source)
        ordered = [changed_uri, *(uri for uri in self.documents if uri != changed_uri)]
        for uri in ordered:
            self._analyse(uri)

    def _analyse(self, uri: str) -> None:
        """Analyse an open document and publish diagnostics at source locations."""
        # Imported signatures may depend on other changed documents even when
        # the declaration's own source text is unchanged.
        if hasattr(self, "_query_sources"):
            self._query_sources.clear()
        source = self.documents.get(uri, "")
        diagnostics: list[dict[str, Any]] = []
        try:
            source_file = _uri_path(uri)
            if source_file is None:
                program = parse(source)
                analyser = Analyser(module_loader=self.module_loader)
                typed_program = analyser.analyse(program)
            else:
                if (
                    self.compilation_database._overlays.get(source_file.resolve())
                    != source
                ):
                    self.compilation_database.open_document(source_file, source)
                snapshot = self.compilation_database.analyse(source_file)
                program = list(snapshot.syntax)
                analyser = snapshot.analyser
                typed_program = list(snapshot.typed)
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
        """Return variables, elements, declarations, types and keywords."""
        uri = params["textDocument"]["uri"]
        source = self.documents.get(uri, "")
        position = params["position"]
        cursor_offset = _source_offset_at(source, position)
        if _cursor_in_comment(source, cursor_offset):
            return []
        prefix = completion_prefix(source[: _offset(source, position)])
        import_prefix = _import_completion_prefix(source, cursor_offset)
        if import_prefix is not None:
            return _import_completion_items(uri, import_prefix)
        items: dict[str, tuple[str, int]] = {
            item.text: (item.meta, 14 if item.meta == "keyword" else 7)
            for item in default_completion_items()
            if not item.text.startswith(":")
        }

        # Built-ins remain available even while the current token leaves the
        # document temporarily unparseable or unanalysable.
        for element in BUILTIN_ELEMENTS:
            label = element.name.dotted()
            items.setdefault(
                label,
                (
                    "\n".join(
                        _overload_signature(label, item) for item in element.overloads
                    ),
                    3,
                ),
            )

        # Recover variable names from source text so completion still works
        # while the user is in the middle of typing an otherwise invalid read.
        source_before_cursor = source[:cursor_offset]
        for match in re.finditer(
            r"\$([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([^=\n]+))?=", source_before_cursor
        ):
            name = f"${match.group(1)}"
            declared = (match.group(2) or "").strip()
            items[name] = (f"variable: {declared}" if declared else "variable", 6)
        for match in re.finditer(
            r"(?:define|fn)(?:\[[^]\n]*\])?[^=\n]*\(([^)]*)\)", source_before_cursor
        ):
            for parameter in match.group(1).split(","):
                parameter_match = re.match(
                    r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([^=]+))?",
                    parameter,
                )
                if parameter_match is None:
                    continue
                name = f"${parameter_match.group(1)}"
                declared = (parameter_match.group(2) or "").strip()
                items.setdefault(
                    name,
                    (f"parameter: {declared}" if declared else "parameter", 6),
                )

        typed_program = self.typed_programs.get(uri, [])
        for typed in _typed_nodes(typed_program):
            node = typed.node
            if isinstance(node, (GetVariableNode, SetVariableNode)):
                if node.location is None or _node_offset(node) > cursor_offset:
                    continue
                name = f"${node.name}"
                detail = (
                    f"variable: {T.show(typed.typ)}"
                    if typed.typ is not None
                    else "variable"
                )
                items[name] = (detail, 6)
            elif isinstance(typed, TypedFunctionNode) and isinstance(node, DefineNode):
                if node.location is None or _node_offset(node) > cursor_offset:
                    continue
                overloads = tuple(
                    item.overload
                    for item in typed.overloads
                    if isinstance(item.overload, T.Overload)
                )
                if overloads:
                    name = str(node.name).removeprefix("\\")
                    items[name] = (
                        "\n".join(
                            _overload_signature(name, item) for item in overloads
                        ),
                        3,
                    )

        # Named parameters may not yet have a typed read at the cursor, so add
        # their declared shape directly from every enclosing declaration seen so far.
        for node in _ast_nodes(self.programs.get(uri, [])):
            if not isinstance(node, DefineNode) or node.location is None:
                continue
            if _node_offset(node) > cursor_offset:
                continue
            for param in node.function.params or ():
                if param.name is None:
                    continue
                name = f"${param.name}"
                detail = (
                    f"parameter: {T.show(param.typ)}"
                    if param.typ is not None
                    else "parameter"
                )
                items.setdefault(name, (detail, 6))

        analyser = self.analysers.get(uri)
        if analyser:
            env = analyser.env
            while env is not None:
                for name in env.overloads:
                    overloads = env.overloads_for(name)
                    label = name.dotted()
                    items.setdefault(
                        label,
                        (
                            "\n".join(
                                _overload_signature(label, item) for item in overloads
                            ),
                            3,
                        ),
                    )
                for collection, kind in (
                    (env.objects, "object"),
                    (env.traits, "trait"),
                    (env.variants, "variant"),
                    (env.enums, "enum"),
                ):
                    for name in collection:
                        items.setdefault(name.dotted(), (kind, 7))
                for name in env.data_tags:
                    items.setdefault(f"#{name.text}", ("data tag", 21))
                env = env.parent

        normalized_prefix = prefix.casefold()
        return [
            {
                "label": text,
                "detail": detail,
                "kind": kind,
                "sortText": text.casefold(),
                "filterText": text,
            }
            for text, (detail, kind) in sorted(
                items.items(), key=lambda item: item[0].casefold()
            )
            if not normalized_prefix or text.casefold().startswith(normalized_prefix)
        ]

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


def _cursor_in_comment(source: str, cursor_offset: int) -> bool:
    """Return whether an insertion point belongs to a lexical comment."""
    return any(start <= cursor_offset < end for start, end in comment_ranges(source))


def _import_completion_prefix(source: str, cursor_offset: int) -> str | None:
    """Return the import-path fragment inside import braces."""
    before = source[:cursor_offset]
    import_start = before.rfind("import")
    if import_start < 0:
        return None
    brace_start = before.find("{", import_start)
    if brace_start < 0 or before.rfind("}", import_start) > brace_start:
        return None
    fragment = before[brace_start + 1 :].split(",")[-1].strip()
    return fragment


def _import_completion_items(uri: str, prefix: str) -> list[dict[str, Any]]:
    """Return modules only while completing an import clause."""
    modules: set[str] = set()
    std_root = resources.files("valiance.std")
    for path in std_root.iterdir():
        if path.name.startswith("_"):
            continue
        if path.name.endswith(".py") or path.name.endswith(".vlnc"):
            modules.add(f"std.{path.name.rsplit('.', 1)[0]}")

    current_file = _uri_path(uri)
    if current_file is not None:
        directory = current_file.parent
        for parent in (directory, *directory.parents):
            if (parent / "valiance.toml").exists():
                for path in parent.rglob("*.vlnc"):
                    if path == current_file or any(
                        part in {".vln", "bin"} for part in path.parts
                    ):
                        continue
                    relative = path.relative_to(parent).with_suffix("")
                    modules.add("root." + ".".join(relative.parts))
                break

    normalized = prefix.casefold()
    return [
        {
            "label": module,
            "detail": "module",
            "kind": 9,
            "sortText": module.casefold(),
            "filterText": module,
            "insertText": (
                module[len(prefix) :]
                if prefix and module.casefold().startswith(prefix.casefold())
                else module
            ),
        }
        for module in sorted(modules, key=str.casefold)
        if not normalized or module.casefold().startswith(normalized)
    ]


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
    return {
        "range": rng,
        "severity": severity,
        "source": "valiance",
        "code": structured.stage.lower().replace(" ", "-"),
        "message": text,
    }


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
