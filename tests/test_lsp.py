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

class DefinitionProviderTests(unittest.TestCase):
    def definition(self, source_file, source, line, character):
        output = io.BytesIO()
        server = LanguageServer(io.BytesIO(), output)
        server.initialized = True
        uri = source_file.as_uri()
        server.documents[uri] = source
        server._analyse(uri)
        return server._definition(
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
            }
        )

    def test_go_to_definition_opens_imported_project_module(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.0.0"\n\n[dependencies]\n',
                encoding="utf-8",
            )
            shared = root / "shared.vlnc"
            shared.write_text(
                "public define answer(value: Number) -> Number => $value\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"
            source = "import { root.shared.answer }\n42 answer"

            location = self.definition(main, source, 1, 4)

        self.assertEqual(location["uri"], shared.as_uri())
        self.assertEqual(location["range"]["start"], {"line": 0, "character": 14})

    def test_go_to_definition_follows_import_alias(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.0.0"\n\n[dependencies]\n',
                encoding="utf-8",
            )
            shared = root / "shared.vlnc"
            shared.write_text(
                "public define answer(value: Number) -> Number => $value\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"
            source = "import { root.shared.answer as solution }\n42 solution"

            location = self.definition(main, source, 1, 5)

        self.assertEqual(location["uri"], shared.as_uri())

    def test_go_to_definition_follows_dependency_import(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / ".vln" / "math"
            package.mkdir(parents=True)
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.0.0"\n\n'
                '[dependencies]\nmath = { source = "local/math", version = "1.0.0" }\n',
                encoding="utf-8",
            )
            target = package / "math.vlnc"
            target.write_text(
                "public define twice(value: Number) -> Number => $value 2 *\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"
            source = "import { dep.math.twice }\n21 twice"

            location = self.definition(main, source, 1, 4)

        self.assertEqual(location["uri"], target.as_uri())

class HoverEnhancementTests(unittest.TestCase):
    def hover(self, source_file, source, line, character):
        output = io.BytesIO()
        server = LanguageServer(io.BytesIO(), output)
        server.initialized = True
        uri = source_file.as_uri()
        server.documents[uri] = source
        server._analyse(uri)
        return server._hover(
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
            }
        )

    def test_imported_function_hover_includes_source_docstring(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.0.0"\n\n[dependencies]\n',
                encoding="utf-8",
            )
            (root / "math.vlnc").write_text(
                "#?? Double a number.\n"
                "#?? @param value Number to double.\n"
                "#?? @returns The doubled number.\n"
                "public define double(value: Number) -> Number => $value 2 *\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"
            source = "import { root.math.double }\n10 double"

            hover = self.hover(main, source, 1, 5)

        value = hover["contents"]["value"]
        self.assertIn("double(value: Number) -> Number", value)
        self.assertIn("Double a number.", value)
        self.assertIn("**Parameter `value`:** Number to double.", value)
        self.assertIn("**Returns:** The doubled number.", value)

    def test_variable_hover_shows_inferred_type(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            main = Path(tmp) / "main.vlnc"
            source = '$message = "hello"\n$message println'

            hover = self.hover(main, source, 1, 3)

        self.assertIn("$message: String", hover["contents"]["value"])

    def test_imported_multi_function_hover_lists_every_overload(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.0.0"\n\n[dependencies]\n',
                encoding="utf-8",
            )
            (root / "convert.vlnc").write_text(
                "public define convert(value: Number) -> String => \"number\"\n"
                "public define convert(value: String) -> String => $value\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"
            source = 'import { root.convert.convert }\n"x" convert'

            hover = self.hover(main, source, 1, 6)

        value = hover["contents"]["value"]
        self.assertNotIn("convert(value: Number) -> String", value)
        self.assertIn("convert(value: String) -> String", value)

class OverloadAwareNavigationTests(unittest.TestCase):
    def server_for(self, source_file, source):
        server = LanguageServer(io.BytesIO(), io.BytesIO())
        server.initialized = True
        uri = source_file.as_uri()
        server.documents[uri] = source
        server._analyse(uri)
        return server, uri

    def test_imported_overloads_pair_each_signature_with_its_docstring(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.0.0"\n[dependencies]\n',
                encoding="utf-8",
            )
            (root / "greetings.vlnc").write_text(
                "#?? Return a greeting for name.\n"
                "#?? @param name The name to greet.\n"
                "#?? @returns A friendly greeting.\n"
                "public define greeting(name: String) -> String => \"Hello\"\n"
                "#?? Increment an integer.\n"
                "#?? @param x An integer to increment.\n"
                "#?? @returns The incremented integer.\n"
                "public define greeting(x: Integer) -> Integer => $x 1 +\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"
            source = 'import { root.greetings.greeting }\n1 greeting'
            server, uri = self.server_for(main, source)
            hover = server._hover({
                "textDocument": {"uri": uri},
                "position": {"line": 1, "character": 4},
            })

        value = hover["contents"]["value"]
        self.assertNotIn("greeting(name: String) -> String", value)
        selected = value.index("greeting(x: Integer) -> Integer")
        selected_doc = value.index("Increment an integer.")
        self.assertLess(selected, selected_doc)
        self.assertNotIn("Return a greeting for name.", value)
        self.assertNotIn("Selected overload", value)

    def test_go_to_definition_uses_selected_overload_and_name_range(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.0.0"\n[dependencies]\n',
                encoding="utf-8",
            )
            target = root / "greetings.vlnc"
            target.write_text(
                "public define greeting(name: String) -> String => name\n"
                "public define greeting(x: Integer) -> Integer => $x\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"
            source = 'import { root.greetings.greeting }\n1 greeting'
            server, uri = self.server_for(main, source)
            location = server._definition({
                "textDocument": {"uri": uri},
                "position": {"line": 1, "character": 4},
            })

        self.assertEqual(location["uri"], target.as_uri())
        self.assertEqual(location["range"]["start"], {"line": 1, "character": 14})

    def test_string_interpolation_variable_hover_uses_assignment_type(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            main = Path(tmp) / "main.vlnc"
            source = '$name = "Valiance"\n"Hello, $name"'
            server, uri = self.server_for(main, source)
            hover = server._hover({
                "textDocument": {"uri": uri},
                "position": {"line": 1, "character": 10},
            })

        self.assertIn("$name: String", hover["contents"]["value"])

    def test_definition_name_hover_only_shows_that_overloads_docstring(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            main = Path(tmp) / "main.vlnc"
            source = (
                "#?? String docs.\n"
                "define greeting(name: String) -> String => $name\n"
                "#?? Integer docs.\n"
                "define greeting(x: Integer) -> Integer => $x\n"
            )
            server, uri = self.server_for(main, source)
            hover = server._hover({
                "textDocument": {"uri": uri},
                "position": {"line": 3, "character": 9},
            })

        value = hover["contents"]["value"]
        self.assertIn("greeting(x: Integer) -> Integer", value)
        self.assertIn("Integer docs.", value)
        self.assertNotIn("greeting(name: String)", value)
        self.assertNotIn("String docs.", value)

    def test_local_selected_overload_hover_includes_its_docstring(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            main = Path(tmp) / "main.vlnc"
            source = (
                "#?? String conversion docs.\n"
                "define convert(value: String) -> String => $value\n"
                "#?? Integer conversion docs.\n"
                "define convert(value: Integer) -> Integer => $value\n"
                "1 convert\n"
            )
            server, uri = self.server_for(main, source)
            hover = server._hover({
                "textDocument": {"uri": uri},
                "position": {"line": 4, "character": 4},
            })

        value = hover["contents"]["value"]
        self.assertIn("convert(value: Integer) -> Integer", value)
        self.assertIn("Integer conversion docs.", value)
        self.assertNotIn("convert(value: String)", value)
        self.assertNotIn("String conversion docs.", value)

class HoverLimitAndFallbackTests(unittest.TestCase):
    def test_unresolved_overload_hover_is_capped_at_five(self):
        from valiance.lsp import _render_overload_hover
        import valiance.vtypes as T
        from valiance.vtypes.symbols import Symbol

        overloads = tuple(
            T.Overload(
                params=(T.NominalType(Symbol(f"Type{index}")),),
                returns=(T.NominalType(Symbol(f"Result{index}")),),
                param_names=(Symbol("value"),),
            )
            for index in range(8)
        )
        rendered = _render_overload_hover("many", overloads, ("",) * 8)

        self.assertEqual(rendered.count("many(value:"), 5)
        self.assertIn("…and 3 more overloads.", rendered)
        self.assertNotIn("Type5", rendered)
        self.assertNotIn("### Overload", rendered)
        self.assertIn("```\n\n---\n\n```valiance", rendered)

    def test_selected_nested_import_hover_includes_matching_docstring(self):
        import io
        import tempfile
        from pathlib import Path
        from valiance.lsp import LanguageServer

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.0.0"\n[dependencies]\n',
                encoding="utf-8",
            )
            (root / "src" / "app.vlnc").write_text(
                "#?? Return a greeting for `name`.\n"
                "#?? @param name The name to greet.\n"
                "#?? @returns A friendly greeting.\n"
                "public define greeting(name: String) -> String => $name\n"
                "#?? Integer overload documentation.\n"
                "#?? @param x An integer to increment.\n"
                "#?? @returns The incremented integer.\n"
                "public define greeting(x: Integer) -> Integer => $x + 1\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"
            source = "import { root.src.app.greeting }\ngreeting(5) println"
            server = LanguageServer(io.BytesIO(), io.BytesIO())
            server.initialized = True
            uri = main.as_uri()
            server.documents[uri] = source
            server._analyse(uri)
            hover = server._hover({
                "textDocument": {"uri": uri},
                "position": {"line": 1, "character": 3},
            })

        value = hover["contents"]["value"]
        self.assertIn("greeting(x: Integer) -> Integer", value)
        self.assertIn("Integer overload documentation.", value)
        self.assertIn("**Parameter `x`:** An integer to increment.", value)
        self.assertNotIn("Return a greeting for `name`.", value)

    def test_selected_import_doc_fallback_does_not_require_target_analysis(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.0.0"\n[dependencies]\n',
                encoding="utf-8",
            )
            (root / "greetings.vlnc").write_text(
                "import { root.missing.helper }\n"
                "#?? Increment an integer.\n"
                "#?? @param x An integer to increment.\n"
                "#?? @returns The incremented integer.\n"
                "public define greeting(x: Integer) -> Integer => $x\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"
            source = "import { root.greetings.greeting }\n1 greeting"
            server = LanguageServer(io.BytesIO(), io.BytesIO())
            server.initialized = True
            uri = main.as_uri()
            server.documents[uri] = source
            server._analyse(uri)
            import valiance.vtypes as T
            from valiance.vtypes.symbols import Symbol
            selected = T.Overload(
                params=(T.NominalType(Symbol("Integer")),),
                returns=(T.NominalType(Symbol("Integer")),),
                param_names=(Symbol("x"),),
            )

            documentation = server._documentation_from_import_sources(
                uri, "greeting", selected
            )

        self.assertIn("Increment an integer.", documentation)
        self.assertIn("**Parameter `x`:**", documentation)
