"""Keyboard-accessible path, menu and document-protection dialogs."""

from pathlib import Path

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.geometry import Offset
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, Label


class ChoiceDialog(ModalScreen[str | None]):
    """Present explicit choices with Cancel/Escape leaving the workspace intact."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, choices: list[tuple[str, str]]) -> None:
        """Retain ordered labels and stable action identifiers."""
        super().__init__()
        self.title_text, self.choices = title, choices

    def compose(self) -> ComposeResult:
        """Expose every choice as a focusable button rather than a mouse-only menu."""
        with Vertical(classes="dialog"):
            yield Label(self.title_text, markup=False)
            for label, action in self.choices:
                yield Button(label, id=action)

    @on(Button.Pressed)
    def choose(self, message: Button.Pressed) -> None:
        """Return the selected action to the owning application worker."""
        message.stop()
        self.dismiss(message.button.id)

    def action_cancel(self) -> None:
        """Dismiss without selecting an action."""
        self.dismiss(None)


class MenuDialog(ChoiceDialog):
    """Compact dropdown with explicit dismissal and click-away cancellation."""

    def __init__(
        self, title: str, choices: list[tuple[str, str]], *, anchor: Button
    ) -> None:
        """Retain the toolbar button that owns this dropdown."""
        super().__init__(title, choices)
        self.anchor = anchor

    def compose(self) -> ComposeResult:
        """Include a mouse-accessible Close option alongside menu actions."""
        menu = Vertical(classes="dialog menu-dialog")
        region = self.anchor.region
        menu.styles.offset = Offset(region.x, region.bottom)
        with menu:
            yield Label(self.title_text, markup=False)
            for label, action in self.choices:
                yield Button(label, id=f"menu-action-{action}", compact=True)
            yield Button("Close menu", id="menu-dismiss", compact=True)

    def on_resize(self) -> None:
        """Follow the resized toolbar once its background layout has settled."""
        self.call_after_refresh(self._position)

    def _position(self) -> None:
        """Update the anchor; layout constrains placement before painting."""
        menu = self.query_one(".menu-dialog")
        region = self.anchor.region
        menu.styles.offset = Offset(region.x, region.bottom)

    @on(Button.Pressed)
    def choose(self, message: Button.Pressed) -> None:
        """Close explicitly without performing a menu action."""
        message.stop()
        self.dismiss(
            None
            if message.button.id == "menu-dismiss"
            else message.button.id.removeprefix("menu-action-")
        )

    def on_click(self, event: events.Click) -> None:
        """Cancel when the pointer clicks outside the dropdown rectangle."""
        if not self.query_one(".dialog").region.contains(
            event.screen_x, event.screen_y
        ):
            event.stop()
            self.dismiss(None)


class PathDialog(ModalScreen[Path | None]):
    """Browse or enter a UTF-8 file path, resolving relative paths from a shown base."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, base: Path, initial: str = "") -> None:
        """Use the active file's directory or the launch directory as the path base."""
        super().__init__()
        self.title_text, self.base, self.initial = title, base, initial

    def compose(self) -> ComposeResult:
        """Offer directory browsing and a filename field for both open and save."""
        with Vertical(classes="dialog path-dialog"):
            yield Label(self.title_text, markup=False)
            yield Label(f"Relative paths start at {self.base}", markup=False)
            yield Input(self.initial, placeholder="File path", id="path-value")
            yield DirectoryTree(self.base, id="path-tree")
            with Horizontal():
                yield Button("Choose", id="choose")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        """Start in the path field so keyboard-only users can type immediately."""
        self.query_one(Input).focus()

    @on(DirectoryTree.FileSelected)
    def file_selected(self, message: DirectoryTree.FileSelected) -> None:
        """Copy a browsed file into the editable path field."""
        self.query_one(Input).value = str(message.path)
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def submitted(self) -> None:
        """Accept Enter from the path field."""
        self._choose()

    @on(Button.Pressed)
    def pressed(self, message: Button.Pressed) -> None:
        """Handle both buttons without bubbling into the editor toolbar."""
        message.stop()
        if message.button.id == "choose":
            self._choose()
        else:
            self.action_cancel()

    def _choose(self) -> None:
        """Ignore an empty path and canonicalize a chosen filename."""
        value = self.query_one(Input).value.strip()
        if value:
            path = Path(value).expanduser()
            self.dismiss((path if path.is_absolute() else self.base / path).resolve())

    def action_cancel(self) -> None:
        """Cancel a path selection without saving or closing anything."""
        self.dismiss(None)
