"""Dependency-free Language Server Protocol implementation for Valiance."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, BinaryIO, TextIO
from urllib.parse import unquote, urlparse

from valiance.analysis import Analyser
from valiance.asts import ASTNode, DefineNode
from valiance.diagnostics import DiagnosticError
from valiance.parsing import LexError, ParseError, parse
from valiance.repl import completion_prefix, default_completion_items
from valiance.types import show

_WORD = re.compile(r"(?:\*::|[$#\\])?[A-Za-z_][A-Za-z0-9_:]*")


class LanguageServer:
    """Serve Valiance language features over JSON-RPC/LSP stdio framing."""

    def __init__(self, reader: BinaryIO, writer: BinaryIO) -> None:
        """Handle init for the language-server protocol."""
        self.reader = reader
        self.writer = writer
        self.documents: dict[str, str] = {}
        self.analysers: dict[str, Analyser] = {}
        self.programs: dict[str, list[ASTNode]] = {}
        self.initialized = False
        self.shutdown_requested = False
        self.exit_code = 0

    def run(self) -> int:
        """Read and dispatch messages until the client sends ``exit``."""
        while message := self._read_message():
            if self._dispatch(message):
                break
        return self.exit_code

    def _dispatch(self, message: dict[str, Any]) -> bool:
        """Handle dispatch for the language-server protocol."""
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        is_request = "id" in message

        if method == "exit":
            self.exit_code = 0 if self.shutdown_requested else 1
            return True
        if not self.initialized and method != "initialize":
            if is_request:
                self._error(request_id, -32002, "server is not initialized")
            return False
        if self.shutdown_requested:
            if is_request:
                self._error(request_id, -32600, "server has shut down")
            return False

        try:
            result = self._handle(method, params)
        except Exception as exc:  # keep malformed client requests from killing server
            if is_request:
                self._error(request_id, -32603, str(exc))
            return False
        if is_request:
            if result is NotImplemented:
                self._error(request_id, -32601, f"method not found: {method}")
            else:
                self._respond(request_id, result)
        return False

    def _handle(self, method: str | None, params: dict[str, Any]) -> Any:
        """Handle handle for the language-server protocol."""
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
                "serverInfo": {"name": "valiance-lsp", "version": "0.1.0"},
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
            self._notify("textDocument/publishDiagnostics", {"uri": uri, "diagnostics": []})
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
            return [] if formatted == source else [{"range": _whole_range(source), "newText": formatted}]
        return NotImplemented

    def _analyse(self, uri: str) -> None:
        """Handle analyse for the language-server protocol."""
        source = self.documents.get(uri, "")
        diagnostics: list[dict[str, Any]] = []
        try:
            program = parse(source)
            analyser = Analyser(source_file=_uri_path(uri))
            analyser.analyse(program)
            self.programs[uri] = program
            self.analysers[uri] = analyser
            diagnostics.extend(_message_diagnostic(message, 1) for message in analyser.diagnostics)
            diagnostics.extend(_message_diagnostic(message, 2) for message in analyser.warnings)
            diagnostics.extend(_lint_diagnostic(finding) for finding in analyser.lint_findings)
        except (LexError, ParseError, DiagnosticError) as exc:
            diagnostics.append(_exception_diagnostic(exc))
            self.analysers.pop(uri, None)
            self.programs.pop(uri, None)
        self._notify("textDocument/publishDiagnostics", {"uri": uri, "diagnostics": diagnostics})

    def _completion(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Handle completion for the language-server protocol."""
        uri = params["textDocument"]["uri"]
        source = self.documents.get(uri, "")
        offset = _offset(source, params["position"])
        prefix = completion_prefix(source[:offset])
        items: dict[str, str] = {item.text: item.meta for item in default_completion_items() if not item.text.startswith(":")}
        analyser = self.analysers.get(uri)
        if analyser is not None:
            env = analyser.env
            depth = 0
            while env is not None:
                for name in env.overloads:
                    overloads = env.overloads_for(name)
                    detail = "\n".join(show(overload) for overload in overloads)
                    items.setdefault(name.text, detail or ("element" if depth == 0 else "built-in element"))
                for names, kind in ((env.objects, "object"), (env.traits, "trait"), (env.variants, "variant"), (env.enums, "enum")):
                    for name in names:
                        items.setdefault(name.text, kind)
                for name in env.data_tags:
                    items.setdefault(f"#{name.text}", "data tag")
                env = env.parent
                depth += 1
        return [
            {"label": text, "detail": detail, "kind": _completion_kind(detail), "sortText": text}
            for text, detail in sorted(items.items())
            if not prefix or text.startswith(prefix)
        ]

    def _hover(self, params: dict[str, Any]) -> dict[str, Any] | None:
        """Handle hover for the language-server protocol."""
        uri = params["textDocument"]["uri"]
        source = self.documents.get(uri, "")
        word = _word_at(source, params["position"])
        analyser = self.analysers.get(uri)
        if not word or analyser is None:
            return None
        name = word.lstrip("$#\\")
        env = analyser.env
        from valiance.symbols import Symbol

        overloads = env.overloads_for(Symbol(name))
        if overloads:
            body = "\n".join(f"```valiance\n{name}: {show(overload)}\n```" for overload in overloads)
            return {"contents": {"kind": "markdown", "value": body}}
        return None

    def _definition(self, params: dict[str, Any]) -> dict[str, Any] | None:
        """Handle definition for the language-server protocol."""
        uri = params["textDocument"]["uri"]
        source = self.documents.get(uri, "")
        word = _word_at(source, params["position"])
        if not word:
            return None
        target = word.lstrip("$#\\")
        for node in self.programs.get(uri, []):
            if isinstance(node, DefineNode) and str(node.name) == target and node.location:
                return {"uri": uri, "range": _location_range(node.location.line, node.location.column, len(target))}
        return None

    def _document_symbols(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Handle document symbols for the language-server protocol."""
        uri = params["textDocument"]["uri"]
        symbols = []
        for node in self.programs.get(uri, []):
            if isinstance(node, DefineNode) and node.location:
                rng = _location_range(node.location.line, node.location.column, len(str(node.name)))
                symbols.append({"name": str(node.name), "kind": 12, "range": rng, "selectionRange": rng})
        return symbols

    def _read_message(self) -> dict[str, Any] | None:
        """Handle read message for the language-server protocol."""
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
        if length <= 0:
            return None
        return json.loads(self.reader.read(length).decode("utf-8"))

    def _write(self, payload: dict[str, Any]) -> None:
        """Handle write for the language-server protocol."""
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.writer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        self.writer.flush()

    def _respond(self, request_id: Any, result: Any) -> None:
        """Handle respond for the language-server protocol."""
        self._write({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _error(self, request_id: Any, code: int, message: str) -> None:
        """Handle error for the language-server protocol."""
        self._write({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        """Handle notify for the language-server protocol."""
        self._write({"jsonrpc": "2.0", "method": method, "params": params})


def run_language_server(reader: BinaryIO | None = None, writer: BinaryIO | None = None) -> int:
    """Run the Valiance language server over binary standard input/output."""
    return LanguageServer(reader or sys.stdin.buffer, writer or sys.stdout.buffer).run()


def _uri_path(uri: str) -> Path | None:
    """Handle uri path for the language-server protocol."""
    parsed = urlparse(uri)
    return Path(unquote(parsed.path)) if parsed.scheme == "file" else None


def _offset(source: str, position: dict[str, int]) -> int:
    """Handle offset for the language-server protocol."""
    lines = source.splitlines(keepends=True)
    line = min(position.get("line", 0), len(lines))
    return sum(len(item) for item in lines[:line]) + position.get("character", 0)


def _word_at(source: str, position: dict[str, int]) -> str | None:
    """Handle word at for the language-server protocol."""
    offset = _offset(source, position)
    for match in _WORD.finditer(source):
        if match.start() <= offset <= match.end():
            return match.group(0)
    return None


def _whole_range(source: str) -> dict[str, Any]:
    """Handle whole range for the language-server protocol."""
    lines = source.splitlines()
    return {"start": {"line": 0, "character": 0}, "end": {"line": max(len(lines) - 1, 0), "character": len(lines[-1]) if lines else 0}}


def _location_range(line: int, column: int, length: int = 1) -> dict[str, Any]:
    """Handle location range for the language-server protocol."""
    start = {"line": max(line - 1, 0), "character": max(column - 1, 0)}
    return {"start": start, "end": {"line": start["line"], "character": start["character"] + max(length, 1)}}


def _exception_diagnostic(exc: DiagnosticError) -> dict[str, Any]:
    """Handle exception diagnostic for the language-server protocol."""
    return {"range": _location_range(exc.line or 1, exc.column or 1), "severity": 1, "source": "valiance", "message": str(exc)}


def _message_diagnostic(message: str, severity: int) -> dict[str, Any]:
    """Handle message diagnostic for the language-server protocol."""
    match = re.match(r"line (\d+), column (\d+):\s*(.*)", message, re.DOTALL)
    if match:
        line, column, text = match.groups()
        rng = _location_range(int(line), int(column))
    else:
        text, rng = message, _location_range(1, 1)
    return {"range": rng, "severity": severity, "source": "valiance", "message": text}


def _lint_diagnostic(finding: Any) -> dict[str, Any]:
    """Handle lint diagnostic for the language-server protocol."""
    location = getattr(finding, "location", None)
    return {"range": _location_range(getattr(location, "line", 1), getattr(location, "column", 1)), "severity": 3, "source": "valiance", "code": getattr(finding, "code", None), "message": getattr(finding, "message", str(finding))}


def _completion_kind(detail: str) -> int:
    """Handle completion kind for the language-server protocol."""
    if detail == "keyword":
        return 14
    if detail == "type":
        return 7
    if detail in {"object", "trait", "variant", "enum"}:
        return 7
    return 3
