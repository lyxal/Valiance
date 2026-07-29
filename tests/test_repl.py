import asyncio
import contextlib
import io
import tempfile
from pathlib import Path
import unittest
import unittest.mock

from valiance import repl as repl_module
from valiance.main import _ReplSession
from valiance.repl import (
    PlainReplFrontend,
    ReplCompletion,
    completion_prefix,
    create_repl_frontend,
    highlighted_fragments,
    merge_completion_items,
    supports_fancy_terminal,
)


class _Tty(io.StringIO):
    def isatty(self):
        return True


class ReplFrontendTests(unittest.TestCase):
    def test_redirected_streams_use_plain_frontend(self):
        frontend = create_repl_frontend(
            prompt=lambda line: f"{line}> ",
            completion_provider=tuple,
            type_hint_provider=lambda source: None,
            stdin=io.StringIO(),
            stdout=io.StringIO(),
            environ={"TERM": "xterm-256color"},
        )

        self.assertIsInstance(frontend, PlainReplFrontend)
        self.assertFalse(frontend.fancy)

    def test_plain_mode_can_be_forced_for_a_tty(self):
        self.assertFalse(
            supports_fancy_terminal(
                stdin=_Tty(),
                stdout=_Tty(),
                environ={"TERM": "xterm-256color", "VALIANCE_REPL_MODE": "plain"},
            )
        )

    def test_dumb_terminal_uses_plain_mode(self):
        self.assertFalse(
            supports_fancy_terminal(
                stdin=_Tty(),
                stdout=_Tty(),
                environ={"TERM": "dumb"},
            )
        )

    def test_highlighter_handles_incomplete_source_without_compiler_errors(self):
        fragments = highlighted_fragments('define add(x: Number) => $x 1 + #? note')
        styles = {style for style, _ in fragments}
        rendered = "".join(text for _, text in fragments)

        self.assertEqual(rendered, 'define add(x: Number) => $x 1 + #? note')
        self.assertIn("class:keyword", styles)
        self.assertIn("class:type", styles)
        self.assertIn("class:variable", styles)
        self.assertIn("class:number", styles)
        self.assertIn("class:operator", styles)
        self.assertIn("class:comment", styles)

    def test_completion_prefix_preserves_valiance_sigil(self):
        self.assertEqual(completion_prefix("1 $cou"), "$cou")
        self.assertEqual(completion_prefix("#fin"), "#fin")
        self.assertEqual(completion_prefix(":typ"), ":typ")
        self.assertEqual(completion_prefix("1 2 "), "")

    def test_dynamic_completions_override_generic_metadata(self):
        items = merge_completion_items((ReplCompletion("define", "user element"),))
        define = next(item for item in items if item.text == "define")

        self.assertEqual(define.meta, "user element")
        self.assertTrue(any(item.text == ":type" for item in items))
        self.assertTrue(any(item.text == "Number" for item in items))

    def test_prompt_completer_supports_async_prompt_toolkit_requests(self):
        from prompt_toolkit.completion import CompleteEvent
        from prompt_toolkit.document import Document

        completer = repl_module._ValianceCompleter(
            lambda: (ReplCompletion("increment", "element"),)
        )

        async def collect():
            return [
                item
                async for item in completer.get_completions_async(
                    Document("inc", cursor_position=3),
                    CompleteEvent(completion_requested=True),
                )
            ]

        completions = asyncio.run(collect())
        self.assertEqual([item.text for item in completions], ["increment"])

    def test_session_completions_include_persistent_user_symbols(self):
        session = _ReplSession()
        with contextlib.redirect_stdout(io.StringIO()):
            session.run("$count = 1")
            session.run("define increment(n: Number) -> Number => $n 1 +")

        by_text = {item.text: item.meta for item in session.completion_items()}
        self.assertEqual(by_text["$count"], "variable: Integer")
        self.assertEqual(by_text["increment"], "element")

    def test_enhanced_frontend_declares_repl_as_its_default_mode(self):
        source = Path(repl_module.__file__).read_text(encoding="utf-8")
        self.assertIn('self._mode = "repl"', source)
        self.assertIn('self._last_submitted_source = ""', source)

    def test_scratchpad_alt_menu_shortcuts_are_not_preempted_by_escape(self):
        from prompt_toolkit.keys import Keys

        with contextlib.redirect_stderr(io.StringIO()):
            frontend = repl_module._PromptToolkitFrontend(
                completion_provider=tuple,
                type_hint_provider=lambda source: None,
            )

        escape_bindings = [
            binding
            for binding in frontend._session.app.key_bindings.bindings
            if binding.keys == (Keys.Escape,)
        ]
        alt_menu_keys = {
            binding.keys[1]
            for binding in frontend._session.app.key_bindings.bindings
            if len(binding.keys) == 2 and binding.keys[0] == Keys.Escape
        }

        self.assertTrue(escape_bindings)
        self.assertTrue(all(not binding.eager() for binding in escape_bindings))
        self.assertTrue({"f", "e", "v", "h"}.issubset(alt_menu_keys))

    def test_enhanced_editor_restores_last_program_after_running(self):
        class FakePromptSession:
            def __init__(self):
                self.defaults = []
                self.answers = iter(("1 2 +", "1 2 3 + +"))

            def prompt(self, *args, **kwargs):
                self.defaults.append(kwargs["default"])
                return next(self.answers)

        frontend = repl_module._PromptToolkitFrontend.__new__(
            repl_module._PromptToolkitFrontend
        )
        frontend._session = FakePromptSession()
        frontend._editor_source = ""
        frontend._mode = "scratch"

        self.assertEqual(frontend.read(1), "1 2 +")
        self.assertEqual(frontend.read(2), "1 2 3 + +")
        self.assertEqual(frontend._session.defaults, ["", "1 2 +"])

    def test_enhanced_editor_does_not_replace_program_with_command(self):
        class FakePromptSession:
            def prompt(self, *args, **kwargs):
                return ":help"

        frontend = repl_module._PromptToolkitFrontend.__new__(
            repl_module._PromptToolkitFrontend
        )
        frontend._session = FakePromptSession()
        frontend._editor_source = "1 2 +"
        frontend._mode = "scratch"

        self.assertEqual(frontend.read(2), ":help")
        self.assertEqual(frontend._editor_source, "1 2 +")

    def test_enhanced_editor_modes_share_and_restore_scratch_source(self):
        class FakePromptSession:
            def __init__(self):
                self.defaults = []
                self.answers = iter(("define answer -> Number => 42", "answer"))

            def prompt(self, *args, **kwargs):
                self.defaults.append(kwargs["default"])
                return next(self.answers)

        frontend = repl_module._PromptToolkitFrontend.__new__(
            repl_module._PromptToolkitFrontend
        )
        frontend._session = FakePromptSession()
        frontend._editor_source = ""
        frontend._mode = "scratch"

        self.assertEqual(frontend.read(1), "define answer -> Number => 42")
        self.assertTrue(frontend.set_mode("repl"))
        self.assertEqual(frontend.read(2), "answer")
        self.assertTrue(frontend.set_mode("scratch"))
        self.assertEqual(frontend._editor_source, "define answer -> Number => 42")
        self.assertEqual(frontend._session.defaults, ["", ""])

    def test_scratch_run_waits_before_restoring_editor(self):
        frontend = repl_module._PromptToolkitFrontend.__new__(
            repl_module._PromptToolkitFrontend
        )
        output = io.StringIO()
        with (
            contextlib.redirect_stdout(output),
            unittest.mock.patch("builtins.input", return_value=""),
        ):
            frontend.wait_for_scratch_result()

        rendered = output.getvalue()
        self.assertIn("Press Enter to return to the scratchpad", rendered)
        self.assertTrue(rendered.endswith("\033[2J\033[3J\033[H"))

    def test_enhanced_editor_saves_scratchpad_with_vlnc_extension(self):
        class FakePromptSession:
            def __init__(self, path):
                self.path = path

            def prompt(self, message, **kwargs):
                self.message = message
                return self.path

        with tempfile.TemporaryDirectory() as directory:
            destination = str(Path(directory) / "experiment")
            frontend = repl_module._PromptToolkitFrontend.__new__(
                repl_module._PromptToolkitFrontend
            )
            frontend._session = FakePromptSession(destination)
            frontend._editor_source = "1 2 +"

            saved = frontend.save_scratchpad()

            self.assertEqual(saved, destination + ".vlnc")
            self.assertEqual(Path(saved).read_text(encoding="utf-8"), "1 2 +\n")
            self.assertEqual(frontend._session.message, "Save scratchpad as: ")

    def test_type_preview_does_not_add_definitions_to_the_session(self):
        session = _ReplSession()
        hint = session.type_hint("define temporary(n: Number) -> Number => $n")

        self.assertEqual(hint, "Types: [] -> []")
        self.assertFalse(
            any(item.text == "temporary" for item in session.completion_items())
        )


if __name__ == "__main__":
    unittest.main()
