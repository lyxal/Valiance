import io
import json
import unittest

from valiance.lsp import LanguageServer


def frame(message):
    body = json.dumps(message).encode()
    return f"Content-Length: {len(body)}\r\n\r\n".encode() + body


def messages(data):
    result = []
    stream = io.BytesIO(data)
    while header := stream.readline():
        if not header.strip():
            continue
        length = int(header.decode().split(":", 1)[1])
        stream.readline()
        result.append(json.loads(stream.read(length)))
    return result


class LanguageServerTests(unittest.TestCase):
    def run_server(self, *items):
        output = io.BytesIO()
        server = LanguageServer(io.BytesIO(b"".join(frame(item) for item in items)), output)
        server.run()
        return messages(output.getvalue())

    def test_initialize_advertises_core_features(self):
        result = self.run_server(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "shutdown"},
            {"jsonrpc": "2.0", "method": "exit"},
        )
        capabilities = result[0]["result"]["capabilities"]
        self.assertTrue(capabilities["hoverProvider"])
        self.assertTrue(capabilities["definitionProvider"])
        self.assertTrue(capabilities["documentFormattingProvider"])

    def test_open_publishes_diagnostics_and_completion(self):
        uri = "file:///tmp/main.vlnc"
        result = self.run_server(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": {"uri": uri, "text": "define double(x: Number) -> Number => $x 2 *"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "textDocument/completion", "params": {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 0}}},
            {"jsonrpc": "2.0", "id": 3, "method": "shutdown"},
            {"jsonrpc": "2.0", "method": "exit"},
        )
        self.assertEqual(result[1]["method"], "textDocument/publishDiagnostics")
        self.assertEqual(result[1]["params"]["diagnostics"], [])
        labels = {item["label"] for item in result[2]["result"]}
        self.assertIn("double", labels)
        self.assertIn("define", labels)

    def test_document_symbols_and_definition(self):
        uri = "file:///tmp/main.vlnc"
        source = "define double(x: Number) -> Number => $x 2 *\n10 double"
        result = self.run_server(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": {"uri": uri, "text": source}}},
            {"jsonrpc": "2.0", "id": 2, "method": "textDocument/documentSymbol", "params": {"textDocument": {"uri": uri}}},
            {"jsonrpc": "2.0", "id": 3, "method": "textDocument/definition", "params": {"textDocument": {"uri": uri}, "position": {"line": 1, "character": 5}}},
            {"jsonrpc": "2.0", "id": 4, "method": "shutdown"},
            {"jsonrpc": "2.0", "method": "exit"},
        )
        self.assertEqual(result[2]["result"][0]["name"], "double")
        self.assertEqual(result[3]["result"]["uri"], uri)


if __name__ == "__main__":
    unittest.main()
