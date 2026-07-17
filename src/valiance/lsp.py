"""Dependency-free Language Server Protocol support for Valiance."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import unquote, urlparse

import valiance.vtypes as T
from valiance.analysis import Analyser
from valiance.asts import ASTNode, DefineNode
from valiance.analysis.diagnostics import DiagnosticError
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
            analyser.analyse(program)
            self.programs[uri] = program
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
            self.analysers.pop(uri, None)
        except (LexError, ParseError, DiagnosticError) as exc:
            diagnostics.append(_exception_diagnostic(exc))
            self.programs.pop(uri, None)
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
        """Return complete source-like signatures for the symbol under the cursor."""
        uri = params["textDocument"]["uri"]
        source = self.documents.get(uri, "")
        word = _word_at(source, params["position"])
        analyser = self.analysers.get(uri)
        if not word or analyser is None:
            return None
        display_name = word.lstrip("$#\\")
        lookup_name = word[3:] if word.startswith("*::") else display_name
        overloads = analyser.env.overloads_for(Symbol(lookup_name))
        if not overloads:
            return None
        signatures = "\n".join(
            _overload_signature(display_name, item) for item in overloads
        )
        documentation = _definition_documentation(source, lookup_name)
        value = f"```valiance\n{signatures}\n```"
        if documentation:
            value += f"\n\n{documentation}"
        return {
            "contents": {"kind": "markdown", "value": value},
            "range": _word_range(source, params["position"]),
        }

    def _definition(self, params: dict[str, Any]) -> dict[str, Any] | None:
        """Find a definition in the current document."""
        uri = params["textDocument"]["uri"]
        source = self.documents.get(uri, "")
        word = _word_at(source, params["position"])
        if not word:
            return None
        target = word.lstrip("$#\\")
        for node in self.programs.get(uri, []):
            if (
                isinstance(node, DefineNode)
                and str(node.name) == target
                and node.location
            ):
                return {
                    "uri": uri,
                    "range": _location_range(
                        node.location.line, node.location.column, len(target)
                    ),
                }
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
