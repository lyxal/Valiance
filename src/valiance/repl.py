"""Interactive prompt frontends for the Valiance REPL.

The compiler session deliberately lives outside this module.  This module only
owns terminal capabilities: line editing, highlighting, completion, history,
and live type-hint presentation.  Keeping the boundary narrow means enhanced
terminal behaviour cannot change parsing, analysis, or execution semantics.
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol, TextIO

_REPL_MODE_ENV = "VALIANCE_REPL_MODE"

_KEYWORDS = frozenset(
    {
        "above",
        "any",
        "arr",
        "as",
        "assert",
        "at",
        "novec",
        "break",
        "companion",
        "computed",
        "const",
        "constructed",
        "copy",
        "default",
        "define",
        "dep",
        "dict",
        "disjoint",
        "eager",
        "else",
        "end",
        "enum",
        "exact",
        "except",
        "extend",
        "false",
        "fn",
        "foreach",
        "handle",
        "if",
        "import",
        "match",
        "move",
        "multi",
        "object",
        "private",
        "property",
        "public",
        "readable",
        "record",
        "return",
        "root",
        "tag",
        "trait",
        "true",
        "try",
        "unfold",
        "unit",
        "variant",
        "where",
        "while",
    }
)

_TYPE_NAMES = frozenset(
    {
        "Any",
        "Boolean",
        "Function",
        "Integer",
        "Never",
        "\\None",
        "Number",
        "Real",
        "Result",
        "Some",
        "String",
    }
)

_REPL_COMMANDS = (
    (":help", "show REPL help"),
    (":reset", "clear the screen and REPL state"),
    (":type", "show the stack types for source without executing it"),
    (":clear", "clear the terminal"),
    (":quit", "exit the REPL"),
)

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER_RE = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_COMPLETION_PREFIX_RE = re.compile(r"(?:\*::|[$#:]|\\)?[A-Za-z_][A-Za-z0-9_:]*$")


@dataclass(frozen=True, slots=True)
class ReplCompletion:
    """One completion candidate exposed by the current compiler session."""

    text: str
    meta: str


class ReplFrontend(Protocol):
    """Terminal-facing input interface used by ``valiance.main``."""

    fancy: bool

    def read(self, line_number: int) -> str:
        """Read one source entry, raising EOFError or KeyboardInterrupt normally."""

    def set_mode(self, mode: str) -> bool:
        """Select ``repl`` or ``scratch`` mode when the frontend supports both."""

    def save_scratchpad(self) -> str | None:
        """Save scratch source and return its path, or return ``None``."""

    def submission_kind(self) -> str:
        """Return ``repl``, ``scratch-run``, or ``scratch-switch`` for the last read."""

    def wait_for_scratch_result(self) -> None:
        """Keep an explicit scratch run's output visible until the user continues."""


@dataclass(slots=True)
class PlainReplFrontend:
    """Portable input frontend for redirected, dumb, or unsupported terminals."""

    prompt: Callable[[int], str]
    fancy: bool = False

    def read(self, line_number: int) -> str:
        """Read one source entry from the portable plain-text REPL."""
        return input(self.prompt(line_number))

    def set_mode(self, mode: str) -> bool:
        """Report that the portable frontend only provides one-line REPL mode."""
        return mode == "repl"

    def save_scratchpad(self) -> str | None:
        """Report that the portable frontend has no scratch buffer to save."""
        return None

    def submission_kind(self) -> str:
        """Report that plain input always comes from the one-line REPL."""
        return "repl"

    def wait_for_scratch_result(self) -> None:
        """Do nothing because the plain frontend has no scratch screen."""


def create_repl_frontend(
    *,
    prompt: Callable[[int], str],
    completion_provider: Callable[[], Iterable[ReplCompletion]],
    type_hint_provider: Callable[[str], str | None],
    documentation_provider: Callable[[str, str], str | None] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
) -> ReplFrontend:
    """Create the richest supported frontend, otherwise return the plain one."""

    plain = PlainReplFrontend(prompt)
    if not supports_fancy_terminal(stdin=stdin, stdout=stdout, environ=environ):
        return plain
    try:
        return _PromptToolkitFrontend(
            completion_provider=completion_provider,
            type_hint_provider=type_hint_provider,
            documentation_provider=documentation_provider,
        )
    except (ImportError, OSError, RuntimeError, ValueError):
        return plain


def supports_fancy_terminal(
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether an enhanced full-screen-safe prompt should be attempted."""

    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    environ = os.environ if environ is None else environ
    mode = environ.get(_REPL_MODE_ENV, "auto").strip().lower()
    if mode == "plain":
        return False
    if mode not in {"auto", "fancy"}:
        return False
    if mode == "auto":
        if environ.get("TERM", "").lower() == "dumb":
            return False
        if not (_is_tty(stdin) and _is_tty(stdout)):
            return False
    try:
        import prompt_toolkit  # noqa: F401
    except ImportError:
        return False
    return True


def default_completion_items() -> tuple[ReplCompletion, ...]:
    """Return language-level candidates that do not depend on session state."""

    items = [ReplCompletion(word, "keyword") for word in sorted(_KEYWORDS)]
    items.extend(ReplCompletion(word, "type") for word in sorted(_TYPE_NAMES))
    items.extend(ReplCompletion(command, meta) for command, meta in _REPL_COMMANDS)
    return tuple(items)


def completion_prefix(text_before_cursor: str) -> str:
    """Return the token fragment that completion should replace."""

    stripped = text_before_cursor.lstrip()
    if stripped.startswith(":") and not any(char.isspace() for char in stripped):
        return stripped
    match = _COMPLETION_PREFIX_RE.search(text_before_cursor)
    return "" if match is None else match.group(0)


def highlighted_fragments(line: str) -> list[tuple[str, str]]:
    """Tokenise one possibly incomplete line into prompt-toolkit style fragments."""

    fragments: list[tuple[str, str]] = []
    index = 0
    length = len(line)
    while index < length:
        char = line[index]
        if char.isspace():
            end = index + 1
            while end < length and line[end].isspace():
                end += 1
            fragments.append(("", line[index:end]))
            index = end
            continue
        if line.startswith("#?", index):
            fragments.append(("class:comment", line[index:]))
            break
        if line.startswith("#/", index):
            end = line.find("/#", index + 2)
            end = length if end < 0 else end + 2
            fragments.append(("class:comment", line[index:end]))
            index = end
            continue
        if char in {'"', "'"}:
            end = _quoted_end(line, index)
            fragments.append(("class:string", line[index:end]))
            index = end
            continue
        if char == "$":
            match = _IDENTIFIER_RE.match(line, index + 1)
            end = index + 1 if match is None else match.end()
            fragments.append(("class:variable", line[index:end]))
            index = end
            continue
        if char == "#":
            match = _IDENTIFIER_RE.match(line, index + 1)
            end = index + 1 if match is None else match.end()
            while end < length and line[end] in "+0123456789":
                end += 1
            fragments.append(("class:tag", line[index:end]))
            index = end
            continue
        number = _NUMBER_RE.match(line, index)
        if number is not None:
            fragments.append(("class:number", number.group(0)))
            index = number.end()
            continue
        identifier = _IDENTIFIER_RE.match(line, index)
        if identifier is not None:
            text = identifier.group(0)
            if text in _KEYWORDS:
                style = "class:keyword"
            elif text in _TYPE_NAMES or text[:1].isupper():
                style = "class:type"
            else:
                style = "class:name"
            fragments.append((style, text))
            index = identifier.end()
            continue
        if char in "()[]{}.,:|@'":
            fragments.append(("class:punctuation", char))
            index += 1
            continue
        end = index + 1
        while end < length and line[end] in "+-*%!?=/<>~&^\\":
            end += 1
        fragments.append(("class:operator", line[index:end]))
        index = end
    return fragments


def merge_completion_items(
    dynamic: Iterable[ReplCompletion],
) -> tuple[ReplCompletion, ...]:
    """Merge dynamic and language candidates while preserving useful metadata."""

    by_text: dict[str, ReplCompletion] = {}
    for item in (*tuple(dynamic), *default_completion_items()):
        by_text.setdefault(item.text, item)
    return tuple(sorted(by_text.values(), key=lambda item: item.text.lower()))


def _quoted_end(line: str, start: int) -> int:
    """Compute quoted end for interactive terminal presentation."""
    index = start + 1
    while index < len(line):
        if line[index] == "\\":
            index = min(len(line), index + 2)
            continue
        if line[index] == line[start]:
            return index + 1
        index += 1
    return len(line)


def _is_tty(stream: TextIO) -> bool:
    """Return whether the value is tty."""
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError):
        return False


def _copy_to_system_clipboard(text: str) -> bool:
    """Copy text using only operating-system or terminal facilities."""
    if sys.platform == "win32":
        try:
            import ctypes

            encoded = (text + "\0").encode("utf-16-le")
            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32
            kernel32.GlobalAlloc.restype = ctypes.c_void_p
            kernel32.GlobalLock.argtypes = (ctypes.c_void_p,)
            kernel32.GlobalLock.restype = ctypes.c_void_p
            kernel32.GlobalUnlock.argtypes = (ctypes.c_void_p,)
            kernel32.GlobalFree.argtypes = (ctypes.c_void_p,)
            user32.SetClipboardData.argtypes = (ctypes.c_uint, ctypes.c_void_p)
            user32.SetClipboardData.restype = ctypes.c_void_p
            handle = kernel32.GlobalAlloc(0x0002, len(encoded))
            if not handle:
                return False
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                kernel32.GlobalFree(handle)
                return False
            ctypes.memmove(pointer, encoded, len(encoded))
            kernel32.GlobalUnlock(handle)
            if not user32.OpenClipboard(None):
                kernel32.GlobalFree(handle)
                return False
            try:
                user32.EmptyClipboard()
                if not user32.SetClipboardData(13, handle):
                    kernel32.GlobalFree(handle)
                    return False
                handle = None  # The clipboard now owns the allocation.
                return True
            finally:
                user32.CloseClipboard()
        except (AttributeError, OSError):
            return False

    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["pbcopy"],
                input=text,
                text=True,
                check=True,
                timeout=2,
            )
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    # OSC 52 is supported by many modern Unix terminals and remote shells.
    if _is_tty(sys.stdout):
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        try:
            sys.stdout.write(f"\033]52;c;{encoded}\a")
            sys.stdout.flush()
            return True
        except OSError:
            return False
    return False


def _system_clipboard(in_memory_clipboard):
    """Wrap prompt-toolkit's clipboard with best-effort system clipboard writes."""

    class SystemClipboard:
        def set_data(self, data) -> None:
            """Store clipboard data and mirror its text to the system clipboard."""
            in_memory_clipboard.set_data(data)
            _copy_to_system_clipboard(data.text)

        def get_data(self):
            """Return the current in-memory clipboard payload."""
            return in_memory_clipboard.get_data()

        def rotate(self) -> None:
            """Rotate the in-memory clipboard history."""
            in_memory_clipboard.rotate()

    return SystemClipboard()


def _cut_selection_to_clipboard(buffer, clipboard) -> None:
    """Copy the active selection externally before deleting it from the buffer."""
    if buffer.selection_state is None:
        return
    start, end = buffer.document.selection_range()
    data = buffer.copy_selection()
    clipboard.set_data(data)
    buffer.cursor_position = start
    buffer.delete(count=end - start)


class _PromptToolkitFrontend:
    """Enhanced prompt-toolkit frontend loaded lazily as an optional boundary."""

    fancy = True

    def __init__(
        self,
        *,
        completion_provider: Callable[[], Iterable[ReplCompletion]],
        type_hint_provider: Callable[[str], str | None],
        documentation_provider: Callable[[str, str], str | None] | None = None,
    ) -> None:
        """Initialize this prompt toolkit frontend."""
        from prompt_toolkit import PromptSession
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.filters import (
            Condition,
            completion_is_selected,
            has_completions,
        )
        from prompt_toolkit.application import run_in_terminal
        from prompt_toolkit.application.current import get_app
        from prompt_toolkit.formatted_text import FormattedText
        from prompt_toolkit.layout.containers import (
            ConditionalContainer,
            DynamicContainer,
            Window,
        )
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.mouse_events import MouseEventType
        from prompt_toolkit.widgets import MenuContainer, MenuItem
        from prompt_toolkit.selection import SelectionState
        from prompt_toolkit.clipboard import InMemoryClipboard
        from prompt_toolkit.styles import Style

        self._type_hint_provider = type_hint_provider
        self._documentation_provider = documentation_provider or (
            lambda _name, _source: None
        )
        self._type_hints_enabled = True
        self._editor_source = ""
        self._last_submitted_source = ""
        self._mode = "repl"
        self._pending_mode: str | None = None
        self._scratch_submission_kind: str | None = None
        self._last_submission_kind = "repl"
        self._theme_name = "Midnight"
        bindings = KeyBindings()

        @bindings.add("c-a")
        def _select_all(event) -> None:
            """Select the complete editor buffer."""
            buffer = event.current_buffer
            buffer.cursor_position = len(buffer.text)
            buffer.selection_state = SelectionState(original_cursor_position=0)

        @bindings.add("c-c", eager=True)
        def _copy(event) -> None:
            """Copy the active selection to the application clipboard."""
            buffer = event.current_buffer
            if buffer.selection_state is not None:
                data = buffer.copy_selection()
                event.app.clipboard.set_data(data)
            else:
                # Normal terminal CTRL+C behaviour.
                event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

        @bindings.add("c-x", eager=True)
        def _cut(event) -> None:
            """Cut the active selection to the application clipboard."""
            _cut_selection_to_clipboard(event.current_buffer, event.app.clipboard)

        @bindings.add("c-z")
        def _undo(event) -> None:
            """Undo the most recent buffer edit."""
            event.current_buffer.undo()

        @bindings.add("c-y")
        def _redo(event) -> None:
            """Redo the most recently undone buffer edit."""
            event.current_buffer.redo()

        def extend_selection(buffer, movement) -> None:
            """Start or extend a character selection using one cursor movement."""
            if buffer.selection_state is None:
                buffer.start_selection()
            movement()

        @bindings.add("s-left", filter=Condition(lambda: self._mode == "scratch"))
        def _select_left(event) -> None:
            """Extend the scratch selection one character to the left."""
            extend_selection(event.current_buffer, event.current_buffer.cursor_left)

        @bindings.add("s-right", filter=Condition(lambda: self._mode == "scratch"))
        def _select_right(event) -> None:
            """Extend the scratch selection one character to the right."""
            extend_selection(event.current_buffer, event.current_buffer.cursor_right)

        @bindings.add("s-up", filter=Condition(lambda: self._mode == "scratch"))
        def _select_up(event) -> None:
            """Extend the scratch selection one visual line upward."""
            extend_selection(event.current_buffer, event.current_buffer.cursor_up)

        @bindings.add("s-down", filter=Condition(lambda: self._mode == "scratch"))
        def _select_down(event) -> None:
            """Extend the scratch selection one visual line downward."""
            extend_selection(event.current_buffer, event.current_buffer.cursor_down)

        @bindings.add(
            "up",
            filter=Condition(lambda: self._mode == "scratch") & ~has_completions,
        )
        def _scratch_up(event) -> None:
            """Move visually upward in scratch mode without entering REPL history."""
            event.current_buffer.cursor_up(count=event.arg)

        @bindings.add(
            "down",
            filter=Condition(lambda: self._mode == "scratch") & ~has_completions,
        )
        def _scratch_down(event) -> None:
            """Move visually downward in scratch mode without entering REPL history."""
            event.current_buffer.cursor_down(count=event.arg)

        @bindings.add("c-h")
        def _backspace(event) -> None:
            """Delete the selection, or one character when no selection exists."""
            buffer = event.current_buffer
            if buffer.selection_state is not None:
                buffer.cut_selection()
            else:
                buffer.delete_before_cursor(count=1)

        @bindings.add("c-k")
        @bindings.add("f1")
        def _show_documentation(event) -> None:
            """Show documentation for the selected element without using Backspace."""
            buffer = event.current_buffer
            if buffer.selection_state is None:
                return
            start, end = buffer.document.selection_range()
            name = buffer.text[start:end].strip()
            if not name or any(char.isspace() for char in name):
                return
            documentation = self._documentation_provider(name, buffer.text)
            if not documentation:
                documentation = f"No loaded documentation for {name}."

            def display() -> None:
                """Display styled help, then clear it before restoring the editor."""
                width = 76
                print("\033[2J\033[3J\033[H", end="")
                print(f"\033[48;5;24m\033[97;1m  Valiance Element Help  \033[0m")
                print(f"\033[96;1m{name}\033[0m")
                print("\033[38;5;67m" + "─" * width + "\033[0m")
                for line in documentation.splitlines():
                    if line.startswith(("define ", "public define ")) or " -> " in line:
                        print(f"\033[93m{line}\033[0m")
                    elif line.lower().startswith(
                        ("parameter ", "returns:", "example:", "note:")
                    ):
                        print(f"\033[92m{line}\033[0m")
                    else:
                        print(line)
                print(
                    "\n\033[2mPress Enter to return to the scratchpad...\033[0m",
                    end="",
                    flush=True,
                )
                try:
                    input()
                except EOFError:
                    pass
                print("\033[2J\033[3J\033[H", end="", flush=True)

            run_in_terminal(display)

        @bindings.add("c-m", filter=completion_is_selected, eager=True)
        def _accept_completion(event) -> None:
            """Apply the selected autocomplete item instead of inserting a newline."""
            state = event.current_buffer.complete_state
            if state is not None and state.current_completion is not None:
                event.current_buffer.apply_completion(state.current_completion)

        @bindings.add("c-t")
        @bindings.add("f2")
        def _toggle_type_hints(event) -> None:
            """Toggle live type information."""
            self._type_hints_enabled = not self._type_hints_enabled
            event.app.invalidate()

        @bindings.add("c-j")
        @bindings.add("f5")
        def _submit(event) -> None:
            """Run the scratch buffer; Ctrl-Enter is commonly encoded as Ctrl-J."""
            if self._mode == "scratch":
                self._scratch_submission_kind = "run"
                event.current_buffer.validate_and_handle()

        @bindings.add("c-r")
        def _switch_mode(event) -> None:
            """Toggle modes, submitting changed scratch source before entering REPL."""
            if self._mode == "scratch":
                source = event.current_buffer.text
                self._editor_source = source
                self._mode = "repl"
                if source.strip() and source != self._last_submitted_source:
                    self._scratch_submission_kind = "switch"
                    # Submit the changed scratch program through the ordinary REPL
                    # pipeline.  Its definitions, variables, imports, and stack
                    # effects therefore become available in one-line mode.
                    event.current_buffer.validate_and_handle()
                    return
            else:
                # Keep REPL rendering active until this prompt has exited. This
                # prevents the scratch menu from being painted into the old screen.
                self._pending_mode = "scratch"
            event.current_buffer.text = ":__mode_switched__"
            event.current_buffer.cursor_position = len(event.current_buffer.text)
            event.current_buffer.validate_and_handle()

        @bindings.add("c-w")
        def _clear_buffer(event) -> None:
            """Clear the current input; many terminals encode Ctrl-Backspace as Ctrl-W."""
            event.current_buffer.text = ""
            event.current_buffer.cursor_position = 0

        @bindings.add("c-s")
        def _save(event) -> None:
            """Leave the editor briefly so the scratchpad can be saved."""
            if self._mode != "scratch":
                return
            self._editor_source = event.current_buffer.text
            event.current_buffer.text = ":__save_scratchpad__"
            event.current_buffer.cursor_position = len(event.current_buffer.text)
            event.current_buffer.validate_and_handle()

        @bindings.add("c-space")
        def _complete(event) -> None:
            """Update complete state for interactive terminal presentation."""
            event.current_buffer.start_completion(select_first=False)

        self._session = PromptSession(
            lexer=_ValiancePromptLexer(self._error_spans),
            completer=_ValianceCompleter(completion_provider),
            complete_while_typing=True,
            auto_suggest=AutoSuggestFromHistory(),
            history=InMemoryHistory(),
            clipboard=_system_clipboard(InMemoryClipboard()),
            key_bindings=bindings,
            style=Style.from_dict(self._theme_styles("Midnight")),
            reserve_space_for_menu=6,
            mouse_support=Condition(lambda: self._mode == "scratch"),
            multiline=Condition(lambda: self._mode == "scratch"),
        )

        def menu_action(action: str):
            """Return a handler for an editor menu command."""

            def handle() -> None:
                """Run one menu command against the active editor buffer."""
                app = get_app()
                buffer = app.current_buffer
                if action == "save":
                    self._editor_source = buffer.text
                    buffer.text = ":__save_scratchpad__"
                    buffer.validate_and_handle()
                elif action == "run":
                    self._scratch_submission_kind = "run"
                    buffer.validate_and_handle()
                elif action == "switch":
                    self._editor_source = buffer.text
                    self._mode = "repl"
                    buffer.text = ":__mode_switched__"
                    buffer.validate_and_handle()
                elif action == "select-all":
                    buffer.cursor_position = len(buffer.text)
                    buffer.selection_state = SelectionState(original_cursor_position=0)
                elif action == "cut":
                    _cut_selection_to_clipboard(buffer, app.clipboard)
                elif action == "undo":
                    buffer.undo()
                elif action == "redo":
                    buffer.redo()
                elif action == "clear":
                    buffer.text = ""
                    buffer.cursor_position = 0
                elif action == "types":
                    self._type_hints_enabled = not self._type_hints_enabled
                    app.invalidate()
                elif action.startswith("theme:"):
                    self._apply_theme(action.removeprefix("theme:"))
                    app.invalidate()
                elif action == "help":
                    if buffer.selection_state is not None:
                        start, end = buffer.document.selection_range()
                        name = buffer.text[start:end].strip()
                        if name and not any(char.isspace() for char in name):
                            documentation = self._documentation_provider(
                                name, buffer.text
                            )

                            def display() -> None:
                                """Show styled selected-element documentation temporarily."""
                                text = (
                                    documentation
                                    or f"No loaded documentation for {name}."
                                )
                                print("\033[2J\033[3J\033[H", end="")
                                print(
                                    "\033[48;5;24m\033[97;1m  Valiance Element Help  \033[0m"
                                )
                                print(f"\033[96;1m{name}\033[0m")
                                print("\033[38;5;67m" + "─" * 76 + "\033[0m")
                                print(text)
                                print(
                                    "\n\033[2mPress Enter to return to the scratchpad...\033[0m",
                                    end="",
                                    flush=True,
                                )
                                try:
                                    input()
                                except EOFError:
                                    pass
                                print("\033[2J\033[3J\033[H", end="", flush=True)

                            run_in_terminal(display)

            return handle

        body = self._session.layout.container
        # PromptSession deliberately disables focus-on-click. Full-screen editor
        # semantics need clicks in the document to take focus away from menus.
        from prompt_toolkit.layout.controls import BufferControl

        for window in self._session.layout.find_all_windows():
            if (
                isinstance(window.content, BufferControl)
                and window.content.buffer is self._session.default_buffer
            ):
                window.content.focus_on_click = Condition(
                    lambda: self._mode == "scratch"
                )
        diagnostics = ConditionalContainer(
            Window(
                FormattedTextControl(self._diagnostic_fragments),
                style="class:editor.diagnostic",
                wrap_lines=True,
            ),
            filter=Condition(
                lambda: self._mode == "scratch" and bool(self._diagnostic_text())
            ),
        )
        body.children.insert(-1, diagnostics)
        menu_bindings = KeyBindings()

        @menu_bindings.add("escape", eager=True)
        @menu_bindings.add("c-g", eager=True)
        def _dismiss_menu(event) -> None:
            """Always close the active menu and return focus to the editor."""
            menu.selected_menu = [0]
            event.app.layout.focus(self._session.default_buffer)
            event.app.invalidate()

        menu = MenuContainer(
            body=body,
            key_bindings=menu_bindings,
            menu_items=[
                MenuItem(
                    "File    ",
                    children=[
                        MenuItem("Save", handler=menu_action("save")),
                        MenuItem("Run", handler=menu_action("run")),
                        MenuItem("Return to REPL", handler=menu_action("switch")),
                    ],
                ),
                MenuItem(
                    "Edit    ",
                    children=[
                        MenuItem("Select All", handler=menu_action("select-all")),
                        MenuItem("Cut", handler=menu_action("cut")),
                        MenuItem("Undo", handler=menu_action("undo")),
                        MenuItem("Redo", handler=menu_action("redo")),
                        MenuItem("Clear Buffer", handler=menu_action("clear")),
                    ],
                ),
                MenuItem(
                    "View    ",
                    children=[
                        MenuItem("Toggle Types", handler=menu_action("types")),
                        MenuItem(
                            "Theme",
                            children=[
                                MenuItem(
                                    "Midnight", handler=menu_action("theme:Midnight")
                                ),
                                MenuItem(
                                    "Classic Blue",
                                    handler=menu_action("theme:Classic Blue"),
                                ),
                                MenuItem("Slate", handler=menu_action("theme:Slate")),
                                MenuItem("Forest", handler=menu_action("theme:Forest")),
                                MenuItem(
                                    "Solarized Dark",
                                    handler=menu_action("theme:Solarized Dark"),
                                ),
                                MenuItem(
                                    "Dracula", handler=menu_action("theme:Dracula")
                                ),
                                MenuItem("Nord", handler=menu_action("theme:Nord")),
                                MenuItem(
                                    "Monokai", handler=menu_action("theme:Monokai")
                                ),
                                MenuItem(
                                    "Gruvbox Dark",
                                    handler=menu_action("theme:Gruvbox Dark"),
                                ),
                                MenuItem(
                                    "Tokyo Night",
                                    handler=menu_action("theme:Tokyo Night"),
                                ),
                                MenuItem(
                                    "Rose Pine", handler=menu_action("theme:Rose Pine")
                                ),
                                MenuItem(
                                    "Catppuccin Mocha",
                                    handler=menu_action("theme:Catppuccin Mocha"),
                                ),
                                MenuItem("Ocean", handler=menu_action("theme:Ocean")),
                                MenuItem(
                                    "Amber Terminal",
                                    handler=menu_action("theme:Amber Terminal"),
                                ),
                                MenuItem(
                                    "Purple Haze",
                                    handler=menu_action("theme:Purple Haze"),
                                ),
                                MenuItem(
                                    "Cherry Blossom",
                                    handler=menu_action("theme:Cherry Blossom"),
                                ),
                                MenuItem("Mint", handler=menu_action("theme:Mint")),
                                MenuItem("Copper", handler=menu_action("theme:Copper")),
                                MenuItem("Ice", handler=menu_action("theme:Ice")),
                                MenuItem("Desert", handler=menu_action("theme:Desert")),
                                MenuItem("Neon", handler=menu_action("theme:Neon")),
                                MenuItem("Sepia", handler=menu_action("theme:Sepia")),
                                MenuItem("Paper", handler=menu_action("theme:Paper")),
                                MenuItem("Light", handler=menu_action("theme:Light")),
                                MenuItem(
                                    "Gruvbox Light",
                                    handler=menu_action("theme:Gruvbox Light"),
                                ),
                                MenuItem(
                                    "Solarized Light",
                                    handler=menu_action("theme:Solarized Light"),
                                ),
                                MenuItem(
                                    "High Contrast",
                                    handler=menu_action("theme:High Contrast"),
                                ),
                            ],
                        ),
                    ],
                ),
                MenuItem(
                    "Help    ",
                    children=[
                        MenuItem("Selected Element", handler=menu_action("help")),
                    ],
                ),
            ],
        )
        original_menu_fragments = menu._get_menu_fragments

        def menu_fragments_with_key_tips():
            """Underline menu access keys persistently for terminal discoverability."""
            fragments = original_menu_fragments()
            rendered = []
            for fragment in fragments:
                style, text, *handler = fragment
                if text and text.strip() and style.startswith("class:menu-bar"):
                    leading = len(text) - len(text.lstrip())
                    if leading:
                        rendered.append((style, text[:leading], *handler))
                    word = text[leading:]
                    rendered.append((style + " underline", word[:1], *handler))
                    if len(word) > 1:
                        rendered.append((style, word[1:], *handler))
                else:
                    rendered.append(fragment)
            return rendered

        # FormattedTextControl captured the original bound method during
        # MenuContainer construction. Updating only the instance method does not
        # change what the control renders, so point the control at the replacement.
        menu._get_menu_fragments = menu_fragments_with_key_tips
        menu.control.text = menu_fragments_with_key_tips
        menu.control._content_cache.clear()
        menu.control._fragment_cache.clear()
        self._session.layout.container = DynamicContainer(
            lambda: menu if self._mode == "scratch" else body
        )

        def focus_menu(index: int) -> None:
            """Open a top-level menu and display keyboard access underlines."""
            menu.selected_menu = [index, 0]
            get_app().layout.focus(menu.window)
            get_app().invalidate()

        @bindings.add("escape", "f")
        def _alt_file(event) -> None:
            """Open File through its Alt key tip."""
            focus_menu(0)

        @bindings.add("escape", "e")
        def _alt_edit(event) -> None:
            """Open Edit through its Alt key tip."""
            focus_menu(1)

        @bindings.add("escape", "v")
        def _alt_view(event) -> None:
            """Open View through its Alt key tip."""
            focus_menu(2)

        @bindings.add("escape", "h")
        def _alt_help(event) -> None:
            """Open Help through its Alt key tip."""
            focus_menu(3)

        @bindings.add("f10")
        def _focus_menu(event) -> None:
            """Focus the menu bar and reveal all access-key underlines."""
            menu.selected_menu = [0]
            event.app.layout.focus(menu.window)
            event.app.invalidate()

        @bindings.add(
            "escape",
            filter=Condition(lambda: self._mode == "scratch"),
        )
        def _escape_menu(event) -> None:
            """Close an open menu without preempting Alt access-key sequences.

            Terminals encode Alt+key as Escape followed by that key.  Keeping the
            bare Escape binding non-eager lets prompt-toolkit wait for a possible
            second key before falling back to this handler.
            """
            menu.selected_menu = [0]
            if event.app.layout.has_focus(menu.window):
                event.app.layout.focus(self._session.default_buffer)
            event.app.invalidate()

    @staticmethod
    def _theme_styles(name: str) -> dict[str, str]:
        """Return a complete prompt-toolkit palette for a named theme."""
        palettes = {
            "Midnight": ("#263842", "#c2ccd2", "#20272b", "#d5dde3", "#e2a3a3"),
            "Classic Blue": ("#31566f", "#e0e8ed", "#18242d", "#d8e1e6", "#ffb4ab"),
            "Slate": ("#3a4651", "#e1e6ea", "#22272e", "#cdd5dc", "#f0a6a6"),
            "Forest": ("#29473c", "#d7e8df", "#17231f", "#c7dbd0", "#f2aaa5"),
            "Solarized Dark": ("#073642", "#93a1a1", "#002b36", "#839496", "#dc8b8b"),
            "Dracula": ("#44475a", "#f8f8f2", "#282a36", "#f8f8f2", "#ff5555"),
            "Nord": ("#3b4252", "#e5e9f0", "#2e3440", "#d8dee9", "#bf616a"),
            "Monokai": ("#3e3d32", "#f8f8f2", "#272822", "#f8f8f2", "#f92672"),
            "Gruvbox Dark": ("#504945", "#ebdbb2", "#282828", "#ebdbb2", "#fb4934"),
            "Tokyo Night": ("#1f2335", "#c0caf5", "#16161e", "#a9b1d6", "#f7768e"),
            "Rose Pine": ("#26233a", "#e0def4", "#191724", "#e0def4", "#eb6f92"),
            "Catppuccin Mocha": ("#313244", "#cdd6f4", "#1e1e2e", "#cdd6f4", "#f38ba8"),
            "Ocean": ("#164e63", "#cffafe", "#082f49", "#e0f2fe", "#fb7185"),
            "Amber Terminal": ("#3b2f12", "#ffd75f", "#18130a", "#ffbf00", "#ff6b35"),
            "Purple Haze": ("#4c3b66", "#f2e9ff", "#21182f", "#e9d5ff", "#ff7aa2"),
            "Cherry Blossom": ("#8f4967", "#fff0f6", "#2d1721", "#ffd6e7", "#ff8fab"),
            "Mint": ("#357266", "#e8fff8", "#102a26", "#c7f9e9", "#ff8c94"),
            "Copper": ("#7c4a2d", "#ffe8d6", "#2b1810", "#f4c7a1", "#ff7f50"),
            "Ice": ("#5b7fa3", "#f0fbff", "#14222f", "#d9f2ff", "#ff9aa2"),
            "Desert": ("#8a6d3b", "#fff2cc", "#2e2516", "#f3d9a4", "#d95d39"),
            "Neon": ("#4b0082", "#39ff14", "#090014", "#00f5ff", "#ff2bd6"),
            "Sepia": ("#806744", "#ede0c8", "#2b241a", "#dbc49a", "#c65d47"),
            "Paper": ("#dad4c8", "#28231d", "#fffdf7", "#332e27", "#a23b3b"),
            "Light": ("#d7e3ea", "#20313b", "#f5f7f8", "#263238", "#a62020"),
            "Gruvbox Light": ("#d5c4a1", "#3c3836", "#fbf1c7", "#3c3836", "#cc241d"),
            "Solarized Light": ("#eee8d5", "#586e75", "#fdf6e3", "#657b83", "#b33a3a"),
            "High Contrast": ("#000000", "#ffffff", "#000000", "#ffffff", "#ff5f5f"),
        }
        bar_bg, bar_fg, body_bg, body_fg, error_fg = palettes[name]
        return {
            "prompt": "#9aa0a6",
            "menu-bar": f"bg:{bar_bg} {bar_fg}",
            "menu-bar.selected-item": f"bg:{bar_fg} {bar_bg}",
            "menu": f"bg:{bar_bg} {bar_fg}",
            "menu.selected-item": f"bg:{bar_fg} {bar_bg}",
            "editor.gutter": f"bg:{body_bg} #858b90",
            "editor.diagnostic": f"bg:{body_bg} {body_fg}",
            "editor.diagnostic.error": f"bg:{body_bg} {error_fg}",
            "bottom-toolbar": f"bg:{bar_bg} {bar_fg}",
            "bottom-toolbar.type": f"bg:{bar_bg} {bar_fg}",
            "bottom-toolbar.error": f"bg:{bar_bg} {error_fg}",
            "completion-menu.completion": f"bg:{body_bg} {body_fg}",
            "completion-menu.completion.current": f"bg:{bar_fg} {bar_bg}",
            "completion-menu.meta.completion": f"bg:{body_bg} {bar_fg}",
            "completion-menu.meta.completion.current": f"bg:{bar_fg} {bar_bg}",
            "auto-suggestion": "#6c7086 italic",
            "keyword": "ansimagenta bold",
            "type": "ansicyan",
            "number": "ansiyellow",
            "string": "ansigreen",
            "comment": "#6c7086 italic",
            "variable": "ansiblue",
            "tag": "ansired",
            "operator": "ansiwhite bold",
            "punctuation": "#a6adc8",
            "name": body_fg,
            "error": f"{error_fg} underline",
        }

    def _apply_theme(self, name: str) -> None:
        """Apply a named editor theme immediately."""
        from prompt_toolkit.styles import Style

        self._theme_name = name
        self._session.style = Style.from_dict(self._theme_styles(name))

    def read(self, line_number: int) -> str:
        """Edit and run a persistent scratch program.

        After execution the previous program is restored into the next editor
        buffer, so results can be inspected and the same source can immediately
        be changed and rerun.  F3 starts a fresh scratch program; compiler and
        runtime state remain governed by the ordinary REPL commands.
        """
        prompt_mode = self._mode
        if prompt_mode == "scratch":
            # Clear both the normal and alternate-screen viewport before drawing.
            # Some terminals retain the prior REPL scrollback unless explicitly reset.
            sys.stdout.write("\033[2J\033[3J\033[H")
            sys.stdout.flush()
        label = "scratch" if prompt_mode == "scratch" else "repl"
        default = self._editor_source if prompt_mode == "scratch" else ""
        message = (
            [("class:editor.gutter", "  1 │ ")]
            if prompt_mode == "scratch"
            else [("class:prompt", f"{label}:{line_number}> ")]
        )
        if hasattr(self._session, "app"):
            self._session.app.full_screen = prompt_mode == "scratch"
            self._session.app.erase_when_done = prompt_mode == "scratch"
        source = self._session.prompt(
            message,
            default=default,
            bottom_toolbar=self._bottom_toolbar,
            prompt_continuation=self._continuation_prompt,
        )
        if source == ":__mode_switched__" and self._pending_mode is not None:
            self._mode = self._pending_mode
            self._pending_mode = None
        is_scratch_source = (
            prompt_mode == "scratch"
            and bool(source.strip())
            and not source.lstrip().startswith(":")
        )
        if is_scratch_source:
            self._editor_source = source
            self._last_submitted_source = source
            kind = getattr(self, "_scratch_submission_kind", None) or "run"
            self._last_submission_kind = f"scratch-{kind}"
        else:
            self._last_submission_kind = "repl"
        self._scratch_submission_kind = None
        return source

    def set_mode(self, mode: str) -> bool:
        """Switch between the one-line REPL and persistent scratch editor."""
        if mode not in {"repl", "scratch"}:
            return False
        self._mode = mode
        return True

    def submission_kind(self) -> str:
        """Return the origin and intent of the source returned by ``read``."""
        return self._last_submission_kind

    def wait_for_scratch_result(self) -> None:
        """Pause before restoring the full-screen editor after an explicit run."""
        print(
            "\n\033[2mPress Enter to return to the scratchpad...\033[0m",
            end="",
            flush=True,
        )
        try:
            input()
        except EOFError:
            pass
        print("\033[2J\033[3J\033[H", end="", flush=True)

    def save_scratchpad(self) -> str | None:
        """Prompt for a ``.vlnc`` path and write the retained scratch source."""
        # The editor session is multiline, so Enter inserts source text.
        # Use a separate single-line session for the filename prompt.
        from prompt_toolkit import PromptSession

        path_session: PromptSession[str] = PromptSession(multiline=False)
        raw_path = path_session.prompt("Save scratchpad as: ").strip()
        if not raw_path:
            return None
        path = os.path.expanduser(raw_path)
        if not path.lower().endswith(".vlnc"):
            path += ".vlnc"
        with open(path, "w", encoding="utf-8") as target:
            target.write(self._editor_source)
            if self._editor_source and not self._editor_source.endswith("\n"):
                target.write("\n")
        return path

    def _continuation_prompt(self, width: int, line_number: int, is_soft_wrap: bool):
        """Render an editor-like numbered gutter without continuation ellipses."""
        if is_soft_wrap or self._mode != "scratch":
            return " " * width
        return [("class:editor.gutter", f"{line_number + 1:>3} │ ".rjust(width))]

    def _error_spans(self, source: str) -> dict[int, tuple[int, int]]:
        """Return live diagnostic ranges for red underlining."""
        if not source.strip() or source.lstrip().startswith(":"):
            return {}
        try:
            hint = self._type_hint_provider(source)
        except Exception:
            return {}
        if not hint or not hint.lower().startswith(
            ("type error", "parse error", "lex error")
        ):
            return {}
        location = re.search(r"(?:\bat\s+|^|\s)(\d+):(\d+)(?:\b|:)", hint)
        if location:
            line, column = int(location.group(1)) - 1, int(location.group(2)) - 1
            return {max(line, 0): (max(column, 0), max(column, 0) + 1)}
        quoted = re.search(
            r"(?:unknown element|undefined variable|no overloads for element) ['`]([^'`]+)['`]",
            hint,
        )
        if quoted:
            target = quoted.group(1)
            for line, text in enumerate(source.splitlines()):
                column = text.find(target)
                if column >= 0:
                    return {line: (column, column + len(target))}
        return {}

    def _diagnostic_text(self) -> str:
        """Return the complete live diagnostic shown above the status line."""
        if not self._type_hints_enabled:
            return ""
        source = self._session.default_buffer.text
        if not source.strip() or source.lstrip().startswith(":"):
            return ""
        try:
            hint = self._type_hint_provider(source)
        except Exception:
            return ""
        if hint and hint.lower().startswith(("type error", "parse error", "lex error")):
            return hint
        return ""

    def _diagnostic_fragments(self):
        """Render the complete diagnostic in a restrained panel above status."""
        text = self._diagnostic_text()
        return [
            ("class:editor.diagnostic.error", line + "\n") for line in text.splitlines()
        ]

    def _bottom_toolbar(self):
        """Render a compact status line below the diagnostic panel."""
        document = self._session.default_buffer.document
        if self._mode != "scratch":
            source = document.text.strip()
            if not self._type_hints_enabled:
                status = "Stack types off"
            elif not source:
                status = "Stack types: waiting for input"
            else:
                try:
                    status = self._type_hint_provider(source) or "No type information"
                except Exception:
                    status = "No type information"
                status = status.splitlines()[0]
            return [("class:bottom-toolbar.type", f" {status} ")]
        row = document.cursor_position_row + 1
        column = document.cursor_position_col + 1
        modified = " *" if document.text != self._last_submitted_source else ""
        status = "Stack types off" if not self._type_hints_enabled else "Ready"
        if self._type_hints_enabled and document.text.strip():
            try:
                hint = self._type_hint_provider(document.text)
            except Exception:
                hint = None
            if hint:
                first_line = hint.splitlines()[0]
                category = first_line.partition(":")[0]
                if category.lower().endswith(" error"):
                    status = category
                else:
                    status = first_line
        return [
            ("class:bottom-toolbar", " [UTF-8]  [Spaces:4]  "),
            ("class:bottom-toolbar.type", f"{row}:{column}  {status}{modified}"),
            ("class:bottom-toolbar", "  [scratch.vlnc] "),
        ]


class _ValiancePromptLexer:
    def __init__(self, error_spans_provider=None) -> None:
        """Initialize with an optional live diagnostic range provider."""
        self._error_spans_provider = error_spans_provider or (lambda _source: {})

    def lex_document(self, document):
        """Return highlighted lines with live error underlines."""
        lines = document.lines
        spans = self._error_spans_provider(document.text)

        def get_line(lineno: int):
            """Return fragments for one display line."""
            if lineno >= len(lines):
                return []
            fragments = highlighted_fragments(lines[lineno])
            span = spans.get(lineno)
            if span is None:
                return fragments
            start, end = span
            rendered = []
            offset = 0
            for style, text in fragments:
                stop = offset + len(text)
                if stop <= start or offset >= end:
                    rendered.append((style, text))
                else:
                    left = max(start - offset, 0)
                    right = min(end - offset, len(text))
                    if left:
                        rendered.append((style, text[:left]))
                    rendered.append((f"{style} class:error".strip(), text[left:right]))
                    if right < len(text):
                        rendered.append((style, text[right:]))
                offset = stop
            return rendered

        return get_line


class _ValianceCompleter:
    def __init__(
        self,
        provider: Callable[[], Iterable[ReplCompletion]],
    ) -> None:
        """Initialize this valiance completer."""
        self._provider = provider

    def get_completions(self, document, complete_event):
        """Yield completion candidates matching the text before the cursor."""
        from prompt_toolkit.completion import Completion

        before = document.text_before_cursor
        prefix = completion_prefix(before)
        if not prefix and not complete_event.completion_requested:
            return
        lowered = prefix.lower()
        for item in merge_completion_items(self._provider()):
            if lowered and not item.text.lower().startswith(lowered):
                continue
            yield Completion(
                item.text,
                start_position=-len(prefix),
                display_meta=item.meta,
            )

    async def get_completions_async(self, document, complete_event):
        """Yield completion candidates through prompt-toolkit's async interface."""
        for completion in self.get_completions(document, complete_event):
            yield completion
