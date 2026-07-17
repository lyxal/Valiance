import unittest

from valiance.analysis import Analyser
from valiance.parsing import LexError, lex, parse


class UnicodeIdentifierLexerTests(unittest.TestCase):
    def test_accepts_major_writing_systems(self):
        names = ("cafe", "Δοκιμή", "переменная", "متغير", "משתנה", "चर", "変数", "變數", "변수", "ตัวแปร", "փոփոխական")
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(lex(name)[0].value, name)

    def test_normalizes_and_retains_raw_source(self):
        token = lex("cafe\u0301")[0]
        self.assertEqual(token.value, "café")
        self.assertEqual(token.raw, "cafe\u0301")
        self.assertEqual(lex("\\cafe\u0301")[0].value, "\\café")

    def test_rejects_emoji_and_forbidden_categories(self):
        for source in ("smile😀", "gear⚙", "a\x00b", "a\u200db", "a\u202eb", "a\ue000b", "a\ud800b"):
            with self.subTest(source=source), self.assertRaises(LexError):
                lex(source)

    def test_legacy_symbolic_elements_are_unchanged(self):
        self.assertEqual([t.value for t in lex("+ ** !=")[:-1]], ["+", " ", "*", "*", " ", "!", "="])


class UnicodeIdentifierDiagnosticTests(unittest.TestCase):
    def test_mixed_script_identifier_is_allowed_but_linted(self):
        analyser = Analyser()
        analyser.analyse(parse("$pаypal = 1\n$pаypal"))
        self.assertIn("unicode-identifier-security", [item.code for item in analyser.lint_findings])


if __name__ == "__main__":
    unittest.main()
