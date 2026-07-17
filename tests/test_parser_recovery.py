import unittest

from valiance.parsing import (
    LexError,
    ParseError,
    ParseErrors,
    TokenKind,
    lex_with_diagnostics,
    parse,
    parse_with_diagnostics,
)


class ParserRecoveryTests(unittest.TestCase):
    def test_lexer_collects_multiple_errors_and_reaches_eof(self) -> None:
        tokens, diagnostics = lex_with_diagnostics('#\n"unterminated')

        self.assertEqual(2, len(diagnostics))
        self.assertTrue(all(isinstance(item, LexError) for item in diagnostics))
        self.assertIn("expected tag name", diagnostics[0].message)
        self.assertIn("closing double quote", diagnostics[1].message)
        self.assertEqual(TokenKind.EOF, tokens[-1].kind)

    def test_parser_recovers_at_newlines_and_keeps_valid_statements(self) -> None:
        result = parse_with_diagnostics("foo()\n1 2 +\nbar()\n3 4 +")

        self.assertEqual(2, len(result.diagnostics))
        self.assertTrue(all(isinstance(item, ParseError) for item in result.diagnostics))
        self.assertTrue(all("found ')'" in item.message for item in result.diagnostics))
        # Both valid arithmetic lines survive recovery (three nodes each).
        self.assertEqual(6, len(result.nodes))

    def test_strict_parse_raises_batch_with_every_error(self) -> None:
        with self.assertRaises(ParseErrors) as caught:
            parse("foo()\nbar()")

        self.assertEqual(2, len(caught.exception.errors))

    def test_diagnostics_are_returned_in_source_order(self) -> None:
        result = parse_with_diagnostics("foo()\n#\nbar()")
        locations = [(item.line, item.column) for item in result.diagnostics]
        self.assertEqual(sorted(locations), locations)


if __name__ == "__main__":
    unittest.main()
