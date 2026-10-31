import unittest

from valiance.analysis.diagnostics import from_exception, from_message, render
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
    def test_syntax_help_names_the_required_correction(self) -> None:
        cases = (
            ('"missing', 'closing double quote'),
            ('"bad\\q"', r'Valid escapes are `\n`'),
            ('#/ missing', 'Close each `#/`'),
            (';', 'Use a newline'),
            ('`', 'Use double quotes'),
            ('“', 'Replace typographic quotes'),
            ('define foo 1', 'Use `=>`'),
            ('[1', 'close this construct with `]`'),
            ('{1', 'close this construct with `}`'),
            ('foo(1', 'close this construct with `)`'),
            ('record{field => 1', 'close this construct with `}`'),
            ('dict{1 => 2', 'close this construct with `}`'),
        )
        for source, expected_help in cases:
            with self.subTest(source=source):
                errors = parse_with_diagnostics(source).diagnostics
                self.assertEqual(1, len(errors))
                diagnostic = from_exception('Syntax error', errors[0])
                self.assertIn(expected_help, diagnostic.help)
                self.assertNotIn('help:', diagnostic.message)
                self.assertEqual(1, render(diagnostic, source).count('help:'))
                # Text-only consumers preserve the same structured guidance.
                self.assertEqual(
                    diagnostic.help,
                    from_message('Syntax error', errors[0].message).help,
                )

    def test_unknown_corrections_do_not_get_generic_help(self) -> None:
        for source in ('§', '.', '#'):
            with self.subTest(source=source):
                error = parse_with_diagnostics(source).diagnostics[0]
                diagnostic = from_exception('Syntax error', error)
                self.assertIsNone(diagnostic.help)
                self.assertNotIn('help:', render(diagnostic, source))

    def test_unclosed_entries_report_both_valid_tokens(self) -> None:
        error = parse_with_diagnostics('[1').diagnostics[0]
        self.assertIn("expected ',' or ']'; found end of file", error.message)

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
