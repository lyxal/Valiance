import io
import json
import unittest

from valiance.lsp import LanguageServer


def frame(message):
    body = json.dumps(message).encode()
    return f"Content-Length: {len(body)}\r\n\r\n".encode() + body


def decode(data):
    stream = io.BytesIO(data)
    result = []
    while header := stream.readline():
        if not header.strip():
            continue
        length = int(header.decode().split(":", 1)[1])
        stream.readline()
        result.append(json.loads(stream.read(length)))
    return result


class LanguageServerTests(unittest.TestCase):
    def run_server(self, *messages):
        output = io.BytesIO()
        LanguageServer(io.BytesIO(b"".join(frame(item) for item in messages)), output).run()
        return decode(output.getvalue())

    def session(self, source, *requests):
        uri = "file:///tmp/main.vlnc"
        base = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": {"uri": uri, "text": source}}},
        ]
        end = [
            {"jsonrpc": "2.0", "id": 99, "method": "shutdown"},
            {"jsonrpc": "2.0", "method": "exit"},
        ]
        return self.run_server(*base, *requests, *end)

    def test_hover_renders_full_overload_signatures(self):
        source = "1 2 +"
        results = self.session(source, {"jsonrpc": "2.0", "id": 2, "method": "textDocument/hover", "params": {"textDocument": {"uri": "file:///tmp/main.vlnc"}, "position": {"line": 0, "character": 4}}})
        hover = next(item["result"] for item in results if item.get("id") == 2)
        value = hover["contents"]["value"]
        self.assertIn("+(_1: Integer, _2: Integer) -> Integer", value)
        self.assertNotEqual(value.strip(), "overload")

    def test_hover_includes_define_docstring(self):
        source = (
            "#?? Double a number.\n"
            "#??\n"
            "#?? @param value Number to double.\n"
            "#?? @returns The doubled number.\n"
            "define double(value: Number) -> Number => $value 2 *\n"
            "10 double"
        )
        results = self.session(
            source,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "textDocument/hover",
                "params": {
                    "textDocument": {"uri": "file:///tmp/main.vlnc"},
                    "position": {"line": 5, "character": 5},
                },
            },
        )
        hover = next(item["result"] for item in results if item.get("id") == 2)
        value = hover["contents"]["value"]
        self.assertIn("double(value: Number) -> Number", value)
        self.assertIn("Double a number.", value)
        self.assertIn("**Parameter `value`:** Number to double.", value)
        self.assertIn("**Returns:** The doubled number.", value)

    def test_analyser_diagnostic_uses_compiler_location(self):
        source = "define foo(x: Number) -> Number =>\n  $x\n  unknownThing\nend"
        results = self.session(source)
        notification = next(item for item in results if item.get("method") == "textDocument/publishDiagnostics")
        diagnostic = notification["params"]["diagnostics"][0]
        self.assertEqual(diagnostic["range"]["start"], {"line": 2, "character": 2})
        self.assertEqual(diagnostic["message"], "unknown element 'unknownThing'")

    def test_parse_diagnostic_uses_exception_location(self):
        source = "define foo =>\n  [1, 2\nend"
        results = self.session(source)
        notification = next(item for item in results if item.get("method") == "textDocument/publishDiagnostics")
        diagnostic = notification["params"]["diagnostics"][0]
        self.assertGreaterEqual(diagnostic["range"]["start"]["line"], 1)


if __name__ == "__main__":
    unittest.main()
