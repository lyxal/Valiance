"""Interactive prompt frontends for the Valiance REPL.

The compiler session deliberately lives outside this module.  This module only
owns terminal capabilities: line editing, highlighting, completion, history,
and live type-hint presentation.  Keeping the boundary narrow means enhanced
terminal behaviour cannot change parsing, analysis, or execution semantics.
"""

from __future__ import annotations

import os
import re
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
        "None",
        "Number",
        "Real",
        "Result",
        "Some",
        "String",
    }
)

_REPL_COMMANDS = (
    (":help", "show REPL help"),
    (":reset", "clear REPL state"),
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


def create_repl_frontend(
    *,
    prompt: Callable[[int], str],
    completion_provider: Callable[[], Iterable[ReplCompletion]],
    type_hint_provider: Callable[[str], str | None],
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
        if char == '"':
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
        if line[index] == '"':
            return index + 1
        index += 1
    return len(line)


def _is_tty(stream: TextIO) -> bool:
    """Return whether the value is tty."""
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError):
        return False


class _PromptToolkitFrontend:
    """Enhanced prompt-toolkit frontend loaded lazily as an optional boundary."""

    fancy = True

    def __init__(
        self,
        *,
        completion_provider: Callable[[], Iterable[ReplCompletion]],
        type_hint_provider: Callable[[str], str | None],
    ) -> None:
        """Initialize this prompt toolkit frontend."""
        from prompt_toolkit import PromptSession
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.filters import Condition
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style

        self._type_hint_provider = type_hint_provider
        self._type_hints_enabled = True
        self._editor_source = ""
        self._last_submitted_source = ""
        self._mode = "repl"
        bindings = KeyBindings()

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
                event.current_buffer.validate_and_handle()

        @bindings.add("c-r")
        def _switch_mode(event) -> None:
            """Toggle modes, submitting changed scratch source before entering REPL."""
            if self._mode == "scratch":
                source = event.current_buffer.text
                self._editor_source = source
                self._mode = "repl"
                if source.strip() and source != self._last_submitted_source:
                    # Submit the changed scratch program through the ordinary REPL
                    # pipeline.  Its definitions, variables, imports, and stack
                    # effects therefore become available in one-line mode.
                    event.current_buffer.validate_and_handle()
                    return
            else:
                self._mode = "scratch"
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
            lexer=_ValiancePromptLexer(),
            completer=_ValianceCompleter(completion_provider),
            complete_while_typing=True,
            auto_suggest=AutoSuggestFromHistory(),
            history=InMemoryHistory(),
            key_bindings=bindings,
            style=Style.from_dict(
                {
                    "prompt": "ansigreen bold",
                    "bottom-toolbar": "bg:#20252b #c7d0d9",
                    "bottom-toolbar.type": "bg:#20252b #8bd5ca",
                    "bottom-toolbar.error": "bg:#20252b #ed8796",
                    "completion-menu.completion": "bg:#30363d #d8dee9",
                    "completion-menu.completion.current": "bg:#4c566a #ffffff",
                    "completion-menu.meta.completion": "bg:#30363d #8fbcbb",
                    "completion-menu.meta.completion.current": "bg:#4c566a #ffffff",
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
                    "name": "ansiwhite",
                }
            ),
            reserve_space_for_menu=6,
            multiline=Condition(lambda: self._mode == "scratch"),
        )

    def read(self, line_number: int) -> str:
        """Edit and run a persistent scratch program.

        After execution the previous program is restored into the next editor
        buffer, so results can be inspected and the same source can immediately
        be changed and rerun.  F3 starts a fresh scratch program; compiler and
        runtime state remain governed by the ordinary REPL commands.
        """
        prompt_mode = self._mode
        label = "scratch" if prompt_mode == "scratch" else "repl"
        default = self._editor_source if prompt_mode == "scratch" else ""
        source = self._session.prompt(
            [("class:prompt", f"{label}:{line_number}> ")],
            default=default,
            bottom_toolbar=self._bottom_toolbar,
            prompt_continuation=self._continuation_prompt,
        )
        if (
            prompt_mode == "scratch"
            and source.strip()
            and not source.lstrip().startswith(":")
        ):
            self._editor_source = source
            self._last_submitted_source = source
        return source

    def set_mode(self, mode: str) -> bool:
        """Switch between the one-line REPL and persistent scratch editor."""
        if mode not in {"repl", "scratch"}:
            return False
        self._mode = mode
        return True

    def save_scratchpad(self) -> str | None:
        """Prompt for a ``.vlnc`` path and write the retained scratch source."""
        raw_path = self._session.prompt("Save scratchpad as: ").strip()
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

    @staticmethod
    def _continuation_prompt(width: int, line_number: int, is_soft_wrap: bool) -> str:
        """Render a visible prompt for physical lines after the first."""
        if is_soft_wrap:
            return " " * width
        return "... ".rjust(width)

    def _bottom_toolbar(self):
        """Handle bottom toolbar for interactive terminal presentation."""
        controls = f"{self._mode} · Ctrl-R switch · Ctrl-Enter/F5 run · Ctrl-S save · Ctrl-T/F2 types"
        if not self._type_hints_enabled:
            return [("class:bottom-toolbar", f" types off · {controls}")]
        source = self._session.default_buffer.text.strip()
        if not source or source.startswith(":"):
            return [("class:bottom-toolbar", f" {controls}")]
        try:
            hint = self._type_hint_provider(source)
        except Exception:
            hint = None
        if hint is None:
            return [("class:bottom-toolbar", f" {controls}")]
        style = (
            "class:bottom-toolbar.error"
            if hint.lower().startswith(("type error", "parse error", "lex error"))
            else "class:bottom-toolbar.type"
        )
        return [(style, f" {hint}"), ("class:bottom-toolbar", f" · {controls}")]


class _ValiancePromptLexer:
    def lex_document(self, document):
        """Return prompt-toolkit line fragments for a partially typed document."""
        lines = document.lines

        def get_line(lineno: int):
            """Return the highlighted fragments for one requested display line."""
            if lineno >= len(lines):
                return []
            return highlighted_fragments(lines[lineno])

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
