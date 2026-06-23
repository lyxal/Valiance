import contextlib
import io
import unittest

from valiance.main import main


class MainTests(unittest.TestCase):
    def test_main_without_command_prints_help(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        self.assertIn("usage: valiance", output.getvalue())
        self.assertIn("analyse-demo", output.getvalue())


if __name__ == "__main__":
    unittest.main()
